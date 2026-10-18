"""Execução automática real na Deriv — PINHANHA 2.0.

Camada separada do motor de sinais (spec §2):
  DerivExecWS      — WS autorizado: authorize/proposal/buy/proposal_open_contract
  RiskManager      — limites editáveis verificados ANTES de cada compra (spec §5)
  ExecutionReconciler — trades órfãs após restart/timeout (spec §6)
  AutoTrader       — activação/pausa/emergência + pipeline de oportunidades

Nunca executa martingale nem escala a stake por confiança. Nunca repete uma
compra cujo resultado ficou desconhecido — reconcilia primeiro. O token API
vive só no backend (pinhanha2_config, service_role) e nunca aparece em logs,
frontend ou mensagens.
"""

import asyncio
import json
import logging
import time
import urllib.request
import urllib.error

import websockets

from . import tradestore
from .assets import deriv_symbol, asset_cls
from .deriv_feed import WS_HOSTS

log = logging.getLogger("exec")

PING_EVERY = 25
REQ_TIMEOUT = 20
BUY_TIMEOUT = 30
RECONNECT_BASE = 5

DEFAULT_LIMITS = {
    "stake": 1.0,              # valor fixo por operação (moeda da conta)
    "stake_min": 0.35,
    "stake_max": 100.0,
    "max_open": 3,             # operações simultâneas abertas
    "max_per_day": 30,
    "daily_loss_limit": 10.0,  # moeda da conta
    "consec_loss_limit": 5,
    "daily_exposure": 50.0,    # soma de stakes por dia
    "min_payout": 0.80,        # payout/stake mínimo aceitável
    "duration_s": 60,
    "min_conf": 55,            # confiança calibrada mínima p/ executar
    "pause_after_errors": 3,
    "strategy_filter": None,   # nome do votante; None = consenso dos 15
}

STATES = ("candidate", "validated", "proposal", "buy_sent", "confirmed",
          "open", "settled", "rejected")


# ---------------------------------------------------------------- WS

class DerivExecWS:
    """Ligação WS autorizada para execução — separada do feed de dados."""

    def __init__(self, token, app_id=1089):
        self.token = token
        self.app_id = app_id
        self.ws = None
        self.host = None
        self._pending = {}
        self._req_id = 0
        self._reader_task = None
        self._keepalive_task = None
        self.account = None          # dict authorize da conta seleccionada
        self.accounts = []           # lista de contas da authorize
        self._pat = False            # token PAT (API nova REST→OTP→WS)
        self._sym_field = "symbol"   # 'underlying_symbol' no canal OTP-WS
        self.connected = False
        self.authorized = False
        self.last_error = None
        self._on_poc = None          # callback proposal_open_contract

    # --------------------------------------------------------- PAT (API nova)

    def _rest(self, method, path, body=None):
        """Chamada REST à API nova — Bearer PAT + Deriv-App-ID."""
        req = urllib.request.Request(
            f"https://api.derivws.com{path}", method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Deriv-App-ID": str(self.app_id),
                     "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        r = urllib.request.urlopen(req, timeout=15)
        return json.loads(r.read())

    async def _connect_pat(self):
        """PAT: lista contas via REST; o WS abre-se por conta via OTP."""
        try:
            d = await asyncio.to_thread(
                self._rest, "GET", "/trading/v1/options/accounts")
        except Exception as e:
            self.last_error = str(e)[:200]
            return False
        self._pat = True
        self._sym_field = "underlying_symbol"
        self.accounts = [{
            "loginid": a.get("account_id"),
            "currency": a.get("currency"),
            "balance": float(a.get("balance") or 0),
            "is_virtual": a.get("account_type") == "demo",
            "account_type": a.get("account_type"),
            "status": a.get("status"),
        } for a in (d.get("data") or [])]
        self.authorized = True
        log.info("deriv PAT autorizado: %d contas", len(self.accounts))
        return True

    async def _pat_open_ws(self, loginid, acct):
        """OTP one-time (120s) → URL WS dedicada à conta demo/real."""
        d = await asyncio.to_thread(
            self._rest, "POST",
            f"/trading/v1/options/accounts/{loginid}/otp", {})
        url = (d.get("data") or {}).get("url")
        if not url:
            raise RuntimeError("OTP sem url")
        self.ws = await asyncio.wait_for(
            websockets.connect(url, open_timeout=15, ping_interval=None,
                               max_size=2 << 20), timeout=20)
        self.host = "api.derivws.com"
        self.connected = True
        self._reader_task = asyncio.create_task(self._reader())
        self._keepalive_task = asyncio.create_task(self._keepalive())
        self.account = {"loginid": loginid,
                        "currency": acct.get("currency"),
                        "is_virtual": acct.get("is_virtual"),
                        "balance": acct.get("balance")}
        try:
            b = await self._send({"balance": 1})
            self.account["balance"] = b["balance"]["balance"]
        except Exception:
            pass
        log.info("deriv PAT ws ligado: %s", loginid)
        return True

    async def connect(self):
        """Liga e autoriza. PAT → REST; clássico → roda hosts WS."""
        self.last_error = None
        if self.token.startswith("pat_"):
            return await self._connect_pat()
        for i in range(len(WS_HOSTS)):
            url = WS_HOSTS[(i) % len(WS_HOSTS)].format(app=self.app_id)
            host = url.split("//", 1)[1].split("/", 1)[0]
            try:
                self.ws = await asyncio.wait_for(
                    websockets.connect(url, open_timeout=15,
                                       ping_interval=None, max_size=2 << 20),
                    timeout=20)
                self.host = host
                self.connected = True
                self._reader_task = asyncio.create_task(self._reader())
                self._keepalive_task = asyncio.create_task(self._keepalive())
                auth = await self._send({"authorize": self.token})
                a = auth["authorize"]
                self.authorized = True
                self.accounts = a.get("account_list") or []
                self.account = {k: a.get(k) for k in
                                ("loginid", "currency", "balance",
                                 "is_virtual", "email", "fullname",
                                 "scopes", "landing_company_name")}
                log.info("deriv exec autorizado: %s (%s)",
                         a.get("loginid"), host)
                return True
            except Exception as e:
                self.last_error = str(e)[:200]
                log.warning("exec ws %s: %s", host, e)
                await self._teardown()
        self.connected = False
        return False

    async def _teardown(self):
        self.connected = False
        # PAT: o token REST continua válido mesmo com WS fechado
        self.authorized = self._pat and bool(self.accounts)
        for t in (self._reader_task, self._keepalive_task):
            if t:
                t.cancel()
        self._reader_task = self._keepalive_task = None
        for f in self._pending.values():
            if not f.done():
                f.set_exception(RuntimeError("ws fechado"))
        self._pending.clear()
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        self.ws = None

    async def disconnect(self):
        await self._teardown()
        self.account = None
        self.accounts = []

    async def _reader(self):
        try:
            async for raw in self.ws:
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                rid = m.get("req_id")
                if rid in self._pending:
                    f = self._pending.pop(rid)
                    if "error" in m:
                        e = m["error"]
                        f.set_exception(RuntimeError(
                            f"{e.get('code','?')}: {e.get('message','erro')}"))
                    else:
                        f.set_result(m)
                if m.get("msg_type") == "proposal_open_contract" and self._on_poc:
                    try:
                        self._on_poc(m["proposal_open_contract"])
                    except Exception as e:
                        log.warning("poc cb: %s", e)
        except Exception as e:
            self.last_error = str(e)[:200]
        finally:
            self.connected = False

    async def _keepalive(self):
        while True:
            await asyncio.sleep(PING_EVERY)
            try:
                await self._send({"ping": 1})
            except Exception:
                return

    async def _send(self, payload, timeout=REQ_TIMEOUT):
        if not self.ws:
            raise RuntimeError("ws desligado")
        self._req_id += 1
        rid = self._req_id
        payload = dict(payload)
        payload["req_id"] = rid
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    # ------------------------------------------------------------ trading

    async def select_account(self, loginid):
        """Verifica que o loginid pertence às contas autorizadas."""
        for a in self.accounts:
            if a.get("loginid") == loginid:
                if self._pat:
                    await self._teardown()
                    return await self._pat_open_ws(loginid, a)
                self.account = {k: a.get(k) for k in
                                ("loginid", "currency", "is_virtual")}
                # refresh balance
                try:
                    b = await self._send({"balance": 1, "account": loginid})
                    self.account["balance"] = b["balance"]["balance"]
                except Exception:
                    pass
                return True
        return False

    async def proposal(self, symbol, contract_type, stake, currency,
                       duration_s=60):
        dur_unit, dur = ("s", duration_s) if duration_s < 60 else ("m",
                                                                 duration_s // 60)
        m = await self._send({
            "proposal": 1, "amount": stake, "basis": "stake",
            "contract_type": contract_type, "currency": currency,
            "duration": dur, "duration_unit": dur_unit,
            self._sym_field: symbol})
        return m["proposal"]

    async def buy(self, proposal_id, price):
        return (await self._send({"buy": proposal_id, "price": price},
                                 timeout=BUY_TIMEOUT))["buy"]

    async def watch_contract(self, contract_id):
        await self._send({"proposal_open_contract": 1,
                          "contract_id": contract_id, "subscribe": 1})

    async def portfolio(self):
        return (await self._send({"portfolio": 1})).get("portfolio", {})

    async def profit_table(self, limit=50):
        """Histórico REAL de contratos fechados na conta (profit_table)."""
        m = await self._send({"profit_table": 1, "limit": limit,
                              "offset": 0, "sort": "DESC"})
        return (m.get("profit_table") or {}).get("transactions") or []

    async def forget_all(self):
        try:
            await self._send({"forget_all": ["proposal_open_contract"]})
        except Exception:
            pass


# ---------------------------------------------------------------- risk

class RiskManager:
    """Limites verificados antes de CADA compra (spec §5). Sem martingale."""

    def __init__(self):
        self.limits = dict(DEFAULT_LIMITS)
        self.blocked_reason = None     # motivo activo de bloqueio
        self.consec_losses = 0
        self.err_streak = 0

    async def load(self):
        saved = await tradestore.get_config("risk_limits")
        if isinstance(saved, dict):
            self.limits.update({k: saved[k] for k in saved
                                if k in DEFAULT_LIMITS})

    async def save(self, patch):
        for k, v in patch.items():
            if k in DEFAULT_LIMITS:
                if k == "strategy_filter":
                    self.limits[k] = str(v) if v else None
                    continue
                try:
                    self.limits[k] = float(v)
                except (TypeError, ValueError):
                    pass
        self.limits["max_open"] = int(self.limits["max_open"])
        self.limits["max_per_day"] = int(self.limits["max_per_day"])
        self.limits["consec_loss_limit"] = int(self.limits["consec_loss_limit"])
        self.limits["pause_after_errors"] = int(
            self.limits["pause_after_errors"])
        self.limits["min_conf"] = float(self.limits["min_conf"])
        await tradestore.set_config("risk_limits", self.limits)

    def _day_totals(self, day_rows):
        losses = sum(1 for r in day_rows if r.get("result") == "loss")
        lost_amt = sum(-(r.get("profit") or 0) for r in day_rows
                       if (r.get("profit") or 0) < 0)
        exposure = sum(r.get("stake") or 0 for r in day_rows)
        return len(day_rows), lost_amt, exposure, losses

    def check(self, sig, open_count, day_rows):
        """(ok, motivo). Avaliado antes de cada compra."""
        L = self.limits
        stake = L["stake"]
        if stake < L["stake_min"] or stake > L["stake_max"]:
            return False, f"stake {stake} fora de [{L['stake_min']},{L['stake_max']}]"
        if (sig.get("confidence") or 0) < L["min_conf"]:
            return False, f"conf {sig.get('confidence')} < min {L['min_conf']}"
        if open_count >= L["max_open"]:
            return False, f"max abertas {open_count}/{L['max_open']}"
        n, lost, expo, _ = self._day_totals(day_rows)
        if n >= L["max_per_day"]:
            return False, f"max ops/dia {n}/{L['max_per_day']}"
        if lost >= L["daily_loss_limit"]:
            return False, f"perda diária {lost:.2f} >= {L['daily_loss_limit']}"
        if expo + stake > L["daily_exposure"]:
            return False, f"exposição diária {expo:.2f}+{stake} > {L['daily_exposure']}"
        if self.consec_losses >= L["consec_loss_limit"]:
            return False, f"{self.consec_losses} perdas seguidas >= {L['consec_loss_limit']}"
        if self.err_streak >= L["pause_after_errors"]:
            return False, f"{self.err_streak} falhas seguidas — pausado"
        return True, None

    def on_result(self, profit):
        if profit is None:
            return
        self.consec_losses = self.consec_losses + 1 if profit < 0 else 0

    def on_error(self):
        self.err_streak += 1

    def on_success(self):
        self.err_streak = 0


# ---------------------------------------------------------------- trader

class AutoTrader:
    """Pipeline sinal→compra→acompanhamento. Idempotente por sig_id."""

    def __init__(self, engine):
        self.engine = engine
        self.risk = RiskManager()
        self.ws = None
        self.enabled = False           # execução automática activa
        self.emergency = False         # paragem de emergência
        self.mode = None               # 'demo' | 'real'
        self.trades = {}               # sig_id -> dict
        self._day_rows_cache = (0, [])
        self._busy = set()             # sig_ids em pipeline
        self._rec_task = None
        self.last_error = None
        self.last_order = None

    # ---------------------------------------------------------- auth/estado

    async def connect(self, token):
        """Autoriza a conta Deriv e guarda o token no backend."""
        if self.ws:
            await self.ws.disconnect()
        import os
        ws = DerivExecWS(
            token,
            app_id=os.getenv("DERIV_APP_ID", "1089"))
        ok = await ws.connect()
        if not ok:
            self.last_error = ws.last_error
            return False, self.last_error
        self.ws = ws
        ws._on_poc = self._on_poc
        await tradestore.set_config("deriv_token", token)
        return True, None

    async def restore(self):
        """Arranque: repõe token, conta, limites e reconcilia pendentes."""
        await self.risk.load()
        token = await tradestore.get_config("deriv_token")
        loginid = await tradestore.get_config("deriv_loginid")
        st = await tradestore.get_config("autotrade_state")
        if st and st.get("emergency"):
            self.emergency = True
        if token:
            ok, err = await self.connect(token)
            if ok and loginid:
                await self.select_account(loginid)
                # repõe execução activa — foi activada explicitamente antes
                if st and st.get("enabled") and not self.emergency:
                    self.enabled = True
            await self.reconcile()

    async def select_account(self, loginid):
        if not self.ws:
            return False, "sem ligação"
        ok = await self.ws.select_account(loginid)
        if not ok:
            return False, "conta não autorizada"
        self.mode = "demo" if self.ws.account.get("is_virtual") else "real"
        await tradestore.set_config("deriv_loginid", loginid)
        await self._persist_state()
        return True, None

    async def disconnect(self):
        """Revoga: desliga, apaga token/conta/estado, pára execução."""
        self.enabled = False
        self.mode = None
        if self.ws:
            await self.ws.disconnect()
            self.ws = None
        for k in ("deriv_token", "deriv_loginid", "autotrade_state"):
            await tradestore.del_config(k)
        await self._persist_state()

    async def _persist_state(self):
        await tradestore.set_config("autotrade_state", {
            "enabled": self.enabled, "emergency": self.emergency,
            "mode": self.mode})

    async def set_enabled(self, on):
        if on:
            if not self.ws or not self.ws.authorized:
                return False, "liga primeiro a conta Deriv"
            if not self.mode:
                return False, "selecciona a conta DEMO ou REAL"
            if self.emergency:
                return False, "emergência activa — desbloqueia primeiro"
        self.enabled = on
        await self._persist_state()
        return True, None

    async def set_emergency(self, on):
        """Botão de emergência: bloqueia novas entradas imediatamente.
        Não fecha contratos abertos — a API não garante venda antecipada
        para todos os tipos de contrato (spec §5)."""
        self.emergency = on
        if on:
            self.enabled = False
        await self._persist_state()

    # ---------------------------------------------------------- pipeline

    async def on_signal(self, sig):
        """Callback do engine: nova oportunidade emitida."""
        if not self.enabled or self.emergency or not self.ws:
            return
        if self.mode is None or sig.get("watch"):
            return
        # Rise/Fall 60s só é oferecido nos índices sintéticos — forex/crypto
        # são rejeitados pela Deriv ("Trading is not offered"). Ignorar já aqui.
        if asset_cls(sig.get("asset")) != "synthetic":
            return
        sf = self.risk.limits.get("strategy_filter")
        if sf and sf not in (sig.get("voters") or []):
            return   # estratégia seleccionada no painel não votou neste sinal
        sig_id = sig.get("sig_id")
        if not sig_id or sig_id in self._busy or sig_id in self.trades:
            return
        self._busy.add(sig_id)
        asyncio.create_task(self._pipeline(sig))

    async def _pipeline(self, sig):
        sig_id = sig["sig_id"]
        t = {"id": sig_id, "emit_ts": time.time(),
             "asset": sig["asset"], "signal": sig["signal"],
             "state": "candidate", "is_virtual": self.mode == "demo",
             "account_loginid": (self.ws.account or {}).get("loginid")}
        try:
            # 1. dedup idempotente — a linha existe → já tratada
            await tradestore.upsert_trade(t)
            existing = [r for r in await tradestore.list_trades(limit=50)
                        if r["id"] == sig_id]
            if existing and existing[0]["state"] != "candidate":
                return

            # 2. risk check
            day_rows = await self._day_rows()
            open_n = sum(1 for x in self.trades.values()
                         if x.get("state") in ("confirmed", "open", "buy_sent"))
            ok, why = self.risk.check(sig, open_n, day_rows)
            if not ok:
                t["state"] = "rejected"
                t["error"] = why
                self.trades[sig_id] = t
                await tradestore.upsert_trade(t)
                log.info("rejeitado %s: %s", sig_id, why)
                return
            t["state"] = "validated"

            # 3. proposal (preço fresco — nunca preço antigo)
            sym = deriv_symbol(sig["asset"])
            acct = self.ws.account or {}
            ctype = "CALL" if sig["signal"] == "call" else "PUT"
            stake = self.risk.limits["stake"]
            try:
                prop = await self.ws.proposal(sym, ctype, stake,
                                              acct.get("currency", "USD"),
                                              int(self.risk.limits["duration_s"]))
            except Exception as e:
                self.risk.on_error()
                t.update(state="rejected", error=f"proposal: {e}"[:300])
                self.trades[sig_id] = t
                await tradestore.upsert_trade(t)
                return
            ask = float(prop.get("ask_price") or 0)
            payout = float(prop.get("payout") or 0)
            t.update(state="proposal", proposal_id=prop.get("id"),
                     symbol=sym, contract_type=ctype, stake=ask,
                     payout=payout, currency=acct.get("currency"),
                     duration_s=int(self.risk.limits["duration_s"]))

            # 4. valida preço/payout vs limites
            if ask <= 0 or ask > self.risk.limits["stake"] * 1.5:
                t.update(state="rejected",
                         error=f"ask {ask} fora do limite")
                self.trades[sig_id] = t
                await tradestore.upsert_trade(t)
                return
            if payout and ask and (payout - ask) / ask < self.risk.limits["min_payout"]:
                t.update(state="rejected",
                         error=f"payout implícito {(payout-ask)/ask:.2f} < "
                               f"{self.risk.limits['min_payout']}")
                self.trades[sig_id] = t
                await tradestore.upsert_trade(t)
                return

            # 5. buy
            t["state"] = "buy_sent"
            self.last_order = {"id": sig_id, "ts": time.time(),
                               "symbol": sym, "type": ctype, "stake": ask}
            self.trades[sig_id] = t
            await tradestore.upsert_trade(t)
            try:
                buy = await self.ws.buy(prop["id"], ask)
            except Exception as e:
                # resultado desconhecido — reconcilia antes de qualquer retry
                t.update(state="buy_sent", error=f"buy?: {e}"[:300])
                self.trades[sig_id] = t
                await tradestore.upsert_trade(t)
                self.risk.on_error()
                log.warning("buy incerto %s: %s — a reconciliar", sig_id, e)
                await self._reconcile_one(sig_id)
                return
            t.update(state="confirmed", contract_id=str(buy.get("contract_id")),
                     buy_price=float(buy.get("purchase_time") and ask or ask),
                     opened_at=time.time())
            t["buy_price"] = float(buy.get("buy_price") or ask)
            self.last_order["contract_id"] = t["contract_id"]
            self.trades[sig_id] = t
            await tradestore.upsert_trade(t)
            self.risk.on_success()
            log.info("COMPRA %s %s %s stake=%s contract=%s",
                     sym, ctype, ask, stake, t["contract_id"])

            # 6. segue o contrato até resultado
            t["state"] = "open"
            await tradestore.upsert_trade(t)
            try:
                await self.ws.watch_contract(int(t["contract_id"]))
            except Exception as e:
                log.warning("watch %s: %s", t["contract_id"], e)
        except Exception as e:
            log.exception("pipeline %s", sig_id)
            t.update(state="rejected", error=str(e)[:300])
            self.trades[sig_id] = t
            await tradestore.upsert_trade(t)
        finally:
            self._busy.discard(sig_id)

    def _on_poc(self, poc):
        """Stream proposal_open_contract → resultado final."""
        cid = str(poc.get("contract_id"))
        t = next((x for x in self.trades.values()
                  if x.get("contract_id") == cid), None)
        if not t:
            return
        t["profit"] = float(poc.get("profit") or 0)
        if poc.get("is_sold") or poc.get("status") in ("sold", "won", "lost"):
            t["state"] = "settled"
            t["settled_at"] = time.time()
            prof = t["profit"]
            t["result"] = "win" if prof > 0 else ("loss" if prof < 0
                                                  else "void")
            self.risk.on_result(prof)
            try:
                asyncio.get_running_loop().create_task(
                    tradestore.upsert_trade(t))
            except RuntimeError:
                pass   # sem loop (testes síncronos)
            log.info("settled %s: %s profit=%.2f", cid, t["result"], prof)

    # ---------------------------------------------------------- reconcile

    async def _day_rows(self):
        now = time.time()
        day0 = now - now % 86400
        if self._day_rows_cache[0] < now - 60:
            self._day_rows_cache = (now, await tradestore.day_stats(day0))
        return self._day_rows_cache[1]

    async def _reconcile_one(self, sig_id):
        """Timeout de compra: consulta o portfolio antes de assumir falha."""
        try:
            pf = await self.ws.portfolio()
            t = self.trades.get(sig_id)
            if not t:
                return
            for c in pf.get("contracts", []):
                if (c.get("symbol") == t.get("symbol") and
                        abs(float(c.get("buy_price") or 0) - (t.get("stake") or 0)) < .01):
                    t.update(state="open",
                             contract_id=str(c.get("contract_id")),
                             buy_price=float(c.get("buy_price") or 0))
                    await tradestore.upsert_trade(t)
                    await self.ws.watch_contract(int(t["contract_id"]))
                    return
            t.update(state="rejected", error="buy sem confirmação nem contrato")
            await tradestore.upsert_trade(t)
        except Exception as e:
            log.warning("reconcile %s: %s", sig_id, e)

    async def reconcile(self):
        """Restart: repõe watchers nos contratos abertos, rejeita os
        intermédios nunca confirmados."""
        try:
            pend = await tradestore.pending_reconcile()
        except Exception:
            return
        for r in pend:
            self.trades[r["id"]] = r
            if r.get("state") in ("open", "confirmed") and r.get("contract_id"):
                try:
                    await self.ws.watch_contract(int(r["contract_id"]))
                except Exception:
                    pass
            elif r.get("state") == "buy_sent":
                await self._reconcile_one(r["id"])
            else:
                r["state"] = "rejected"
                r["error"] = "restart antes da compra"
                await tradestore.upsert_trade(r)

    # ---------------------------------------------------------- status

    def status(self):
        acct = self.ws.account if self.ws else None
        open_n = sum(1 for t in self.trades.values()
                     if t.get("state") in ("confirmed", "open", "buy_sent"))
        settled = [t for t in self.trades.values()
                   if t.get("state") == "settled"]
        realized = sum(t.get("profit") or 0 for t in settled)
        unrealized = sum(t.get("profit") or 0 for t in self.trades.values()
                         if t.get("state") == "open")
        return {
            "connected": bool(self.ws and self.ws.connected),
            "authorized": bool(self.ws and self.ws.authorized),
            "enabled": self.enabled,
            "emergency": self.emergency,
            "mode": self.mode,
            "account": {k: acct.get(k) for k in
                        ("loginid", "currency", "balance", "is_virtual",
                         "fullname", "scopes")} if acct else None,
            "accounts": [{"loginid": a.get("loginid"),
                          "currency": a.get("currency"),
                          "is_virtual": bool(a.get("is_virtual")),
                          "balance": a.get("balance")}
                         for a in (self.ws.accounts if self.ws else [])],
            "open": open_n,
            "realized_pl": round(realized, 2),
            "unrealized_pl": round(unrealized, 2),
            "consec_losses": self.risk.consec_losses,
            "err_streak": self.risk.err_streak,
            "limits": self.risk.limits,
            "last_order": self.last_order,
            "last_error": self.last_error or (self.ws.last_error
                                              if self.ws else None),
            "host": self.ws.host if self.ws else None,
        }


trader = None


def init(engine):
    global trader
    trader = AutoTrader(engine)
    engine.on_signal = lambda s: asyncio.ensure_future(trader.on_signal(s))
    return trader
