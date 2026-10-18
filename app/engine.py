"""Signal Engine — PINHANHA 2.0.

Porta 1:1 do pipeline de sinais do PINHANHA original (quotex-bot):
a mesma lista de VOTERS, a mesma ponderação por acertividade medida
(strat_weight), a mesma calibração (calib), e a mesma avaliação WIN/LOSS
60 segundos depois da emissão (primeira vela fechada >= due).

Diferença de infra-estrutura: os dados vêm da Deriv (deriv_feed) em vez
da Quotex/Yahoo — velas 1m por WebSocket com fallback de polling.
"""

import asyncio
import logging
import os
import time

from . import calib
from . import indicators as ind
from . import patterns as pat
from . import regime as rg
from . import sigstore
from .assets import SCAN_ASSETS, deriv_symbol, asset_cls
from .deriv_feed import DerivFeed
from .strategies import STRATEGIES

log = logging.getLogger("engine")

# As mesmas 15 estratégias votantes do PINHANHA original (não mexer sem
# justificação — definição validada no relatório MEGABRAIN).
VOTERS = ["ai_turbo", "multi_indicator", "rsi_momentum", "macd_momentum",
          "momentum", "bible_sr", "price_action", "breakout",
          "bible_engulfing", "bible_pinbar", "q_reversao", "q_pullback",
          "q_breakout_retest", "q_range_fade", "q_mtf"]

CANDLES_NEED = 100


class SignalEngine:
    def __init__(self):
        # feed público: app_id CLÁSSICO numérico (a API antiga rejeita o
        # App ID alfanumérico do developers.deriv.com)
        app_id = os.getenv("DERIV_FEED_APP_ID", "1089")
        self.feed = DerivFeed([deriv_symbol(a) for a in SCAN_ASSETS
                               if deriv_symbol(a)],
                              app_id=app_id, n_candles=CANDLES_NEED)
        self.feed.set_tick_callback(self._on_tick)
        self.running = False          # varredura/sinais activos
        self.paused = False
        self.cache = {}               # asset -> {"ts", "candles", "src"}
        self.sig_track = []           # emitidos por avaliar
        self.sig_results = []         # avaliados
        self._seen = set()
        self.calib = calib.build([])
        self.last_scan_errs = {}
        self._tracker_task = None
        self._resync_task = None
        self._scan_task = None
        self.on_signal = None   # callback do AutoTrader (execução)
        self.bad_assets = {}
        self.state = {"phase": "STOPPED", "error": None,
                      "started_ts": None, "last_signal_ts": None}

    # ---------------------------------------------------------- ligação

    @property
    def connected(self):
        return self.feed.connected

    def status(self):
        st = self.feed.status()
        st.update({
            "running": self.running,
            "paused": self.paused,
            "phase": self.state["phase"],
            "engine_error": self.state["error"],
            "started_ts": self.state["started_ts"],
            "last_signal_ts": self.state["last_signal_ts"],
            "signals_pending": len(self.sig_track),
            "signals_evaluated": len(self.sig_results),
            "assets_cached": len(self.cache),
            "mode": "SIGNALS",      # nunca executa ordens
        })
        return st

    async def start(self):
        if self.running and not self.paused:
            return
        self.running = True
        self.paused = False
        self.state["phase"] = "RUNNING"
        self.state["started_ts"] = self.state["started_ts"] or time.time()
        if not self.feed._task:
            await self.feed.start()
        if not self._tracker_task:
            self._tracker_task = asyncio.create_task(self._tracker())
        if not self._resync_task:
            self._resync_task = asyncio.create_task(self._cache_sync())
        if not self._scan_task:
            self._scan_task = asyncio.create_task(self._scan_loop())

    async def pause(self):
        self.paused = True
        self.state["phase"] = "PAUSED"

    async def resume(self):
        self.paused = False
        if self.running:
            self.state["phase"] = "RUNNING"

    async def stop(self):
        self.running = False
        self.paused = False
        self.state["phase"] = "STOPPED"
        await self.feed.stop()
        for t in (self._tracker_task, self._resync_task, self._scan_task):
            if t:
                t.cancel()
        self._tracker_task = self._resync_task = self._scan_task = None

    # ---------------------------------------------------------- cache

    def _on_tick(self, deriv_sym, price, epoch):
        asset = next((a for a in SCAN_ASSETS if deriv_symbol(a) == deriv_sym),
                     None)
        if not asset:
            return
        ent = self.cache.get(asset)
        if not ent:
            return
        self._merge_ticks(asset, ent["candles"], [{"time": epoch, "price": price}])

    @staticmethod
    def _merge_ticks(asset, cds, ticks):
        """Mesma função do original: actualiza a vela 1m em formação."""
        last = 0
        for tk in ticks:
            t = int(tk["time"])
            if t > 10_000_000_000:
                t //= 1000
            if t <= last:
                continue
            last = t
            p = float(tk["price"])
            b = t - t % 60
            if cds and int(cds[-1]["time"]) == b:
                c = cds[-1]
                c["close"] = p
                c["high"] = max(c["high"], p)
                c["low"] = min(c["low"], p)
            elif not cds or b > int(cds[-1]["time"]):
                cds.append({"time": b, "open": p, "high": p,
                            "low": p, "close": p})
                del cds[:-CANDLES_NEED]

    async def _scan_loop(self):
        """Scan em background a cada 60s — emite sinais mesmo sem UI aberta,
        para a acertividade ser medida continuamente."""
        await asyncio.sleep(20)   # deixa o feed fazer o bootstrap
        while True:
            try:
                if self.running and not self.paused:
                    await self.scan(min_conf=50)
            except Exception as e:
                log.warning("scan_loop: %s", e)
            await asyncio.sleep(60)

    async def _cache_sync(self):
        """Copia as velas do feed para a cache do motor (a cada 3s)."""
        while True:
            try:
                for a in SCAN_ASSETS:
                    sym = deriv_symbol(a)
                    if not sym:
                        continue
                    cds = self.feed.candles.get(sym)
                    if cds:
                        ent = self.cache.get(a)
                        # mantém a mesma lista para os ticks se fundirem
                        if ent and ent["candles"] is not cds:
                            ent["candles"][:] = cds
                        else:
                            self.cache[a] = {"ts": time.time(),
                                             "candles": cds, "src": "deriv"}
                        ent = self.cache[a]
                        ent["ts"] = time.time()
            except Exception as e:
                log.warning("cache sync: %s", e)
            await asyncio.sleep(3)

    async def candles_for(self, asset, n=120):
        sym = deriv_symbol(asset)
        if not sym:
            return []
        cds = await self.feed.candles_for(sym, n)
        ent = self.cache.get(asset)
        if ent is None and cds:
            self.cache[asset] = {"ts": time.time(), "candles": cds, "src": "deriv"}
        return (ent or {}).get("candles") or cds

    # ---------------------------------------------------------- tracking

    def _sig_track(self, rows):
        now = time.time()
        new = []
        for r in rows:
            sid = r.get("sig_id") or f"{r['asset']}-{r['signal']}-{int(now)}"
            if sid in self._seen:
                continue
            self._seen.add(sid)
            new.append({"id": sid, "asset": r["asset"], "signal": r["signal"],
                        "entry": r["price"], "conf": r["confidence"],
                        "watch": r["watch"], "cls": r["cls"],
                        "voters": r.get("voters") or [], "ctx5": r.get("ctx5"),
                        "emit_ts": now, "due": now + 60, "result": "pending",
                        "exit": None, "eval_ts": None})
        self.sig_track.extend(new)
        del self.sig_track[:-600]
        if len(self._seen) > 5000:
            self._seen.clear()
        if new:
            self.state["last_signal_ts"] = now
            asyncio.create_task(sigstore.save_emitted(new))
            if self.on_signal:
                for r in rows:
                    sid = r.get("sig_id") or f"{r['asset']}-{r['signal']}-{int(now)}"
                    if sid in {n["id"] for n in new}:
                        try:
                            self.on_signal(r)
                        except Exception as e:
                            log.warning("on_signal: %s", e)

    def _sig_evaluate(self):
        """Avalia sinais vencidos: primeira vela fechada >= due (igual ao original)."""
        now = time.time()
        keep, done = [], []
        for t in self.sig_track:
            if t["due"] > now:
                keep.append(t)
                continue
            ent = self.cache.get(t["asset"])
            cds = (ent or {}).get("candles") or []
            if not cds:
                if now - t["due"] < 600:
                    keep.append(t)
                    continue
                t["result"] = "void"
            else:
                after = [c for c in cds if c["time"] >= t["due"]]
                if not after:
                    if now - t["due"] < 600:
                        keep.append(t)
                        continue
                    t["result"] = "void"
                else:
                    exit_px = float(after[0]["close"])
                    t["exit"] = exit_px
                    if abs(exit_px - t["entry"]) < 1e-9:
                        t["result"] = "void"
                    else:
                        won = (exit_px > t["entry"]) if t["signal"] == "call" \
                            else (exit_px < t["entry"])
                        t["result"] = "win" if won else "loss"
            t["eval_ts"] = now
            self.sig_results.append(t)
            done.append(t)
        self.sig_track[:] = keep
        del self.sig_results[:-4000]
        return done

    async def _tracker(self):
        """Mesma lógica do original: repõe histórico, avalia a cada 15s,
        reconstrói as tabelas de calibração."""
        try:
            done, pend = await sigstore.load_recent()
            self.sig_results.extend(done)
            self.sig_track.extend(pend)
            self._seen.update(t["id"] for t in done + pend)
            log.info("sigstore: %d avaliados + %d pendentes repostos",
                     len(done), len(pend))
        except Exception as e:
            log.warning("sigstore load: %s", e)
        self.calib = calib.build(self.sig_results)
        while True:
            try:
                done = self._sig_evaluate()
                if done:
                    asyncio.create_task(sigstore.save_results(done))
                    self.calib = calib.build(self.sig_results)
            except Exception:
                pass
            await asyncio.sleep(15)

    def strat_weight(self, k):
        """Peso da estratégia pelo win-rate medido ao vivo (igual ao original)."""
        rows = [r for r in self.sig_results
                if r.get("result") in ("win", "loss")
                and k in (r.get("voters") or [])]
        n = len(rows)
        if n < 30:
            return 1.0
        wr = sum(1 for r in rows if r["result"] == "win") / n
        if wr >= 0.70:
            return 1.4
        if wr >= 0.60:
            return 1.2
        if wr < 0.45:
            return 0.7
        return 1.0

    def sig_stats(self):
        """Resumo da acertividade medida — mesmo cálculo do original."""
        def agg(rows):
            rows = [r for r in rows if r["result"] in ("win", "loss")]
            n = len(rows)
            w = sum(1 for r in rows if r["result"] == "win")
            return {"n": n, "wins": w,
                    "wr": round(100 * w / n, 1) if n else None}
        out = {"total": agg(self.sig_results), "pending": len(self.sig_track),
               "void": sum(1 for r in self.sig_results
                           if r["result"] == "void")}
        for cls in ("forex", "crypto", "synthetic"):
            out[cls] = agg([r for r in self.sig_results if r["cls"] == cls])
        out["live"] = agg([r for r in self.sig_results if not r["watch"]])
        out["watch"] = agg([r for r in self.sig_results if r["watch"]])
        for lo, hi in ((45, 65), (65, 75), (75, 85), (85, 97)):
            out[f"conf_{lo}_{hi}"] = agg(
                [r for r in self.sig_results if lo <= r["conf"] < hi])
        by_strat = {}
        for r in self.sig_results:
            for k in r.get("voters") or []:
                by_strat.setdefault(k, []).append(r)
        out["strategies"] = {k: agg(v) for k, v in by_strat.items()}
        out["recent"] = [{"asset": r["asset"], "signal": r["signal"],
                          "conf": r["conf"], "result": r["result"],
                          "entry": r["entry"], "exit": r["exit"],
                          "emit_ts": r.get("emit_ts")}
                         for r in self.sig_results if r["result"] != "void"][-20:]
        return out

    # ---------------------------------------------------------- varredura

    def target_assets(self):
        out = [a for a in SCAN_ASSETS
               if self.bad_assets.get(a, 0) < time.time()]
        return out

    async def scan(self, min_conf=50):
        """Cópia fiel do scan /api/signals do PINHANHA original."""
        assets = self.target_assets()

        def scan_one(a):
            try:
                cached = self.cache.get(a)
                if not cached or time.time() - cached["ts"] >= 300:
                    return None
                cds = cached["candles"]
                if not cds:
                    return None
                closes = [c["close"] for c in cds]
                votes = {"call": 0, "put": 0}
                confs = {"call": [], "put": []}
                wsum = {"call": 0.0, "put": 0.0}
                voter_keys = {"call": [], "put": []}
                why = []
                for k in VOTERS:
                    st = STRATEGIES[k]
                    r = st["fn"](cds[-(st["min_candles"] + 45):])
                    if not r["signal"]:
                        continue
                    w = self.strat_weight(k)
                    if w <= 0:
                        continue
                    votes[r["signal"]] += w
                    confs[r["signal"]].append(r["confidence"] * w)
                    wsum[r["signal"]] += w
                    voter_keys[r["signal"]].append(k)
                    why.append(f"{st['name']}: {r['signal']} {r['confidence']}%")
                sig = "call" if votes["call"] > votes["put"] else "put"
                n = sum(1 for k in voter_keys[sig])
                opp = votes["put" if sig == "call" else "call"]
                if n < 1 or votes[sig] <= opp:
                    return None
                watch = n < 3
                raw = int(sum(confs[sig]) / wsum[sig]) if confs[sig] else 45

                cls = asset_cls(a)
                i = len(cds) - 1
                e20 = ind.ema(closes, 20)
                e50 = ind.ema(closes, 50)
                rsi_v = ind.rsi(closes, 14)
                mc = ind.macd(closes)
                atr_pct = rg.atr_percentile(cds, i)
                ts = rg.trend_strength(cds, i)
                blocked = rg.no_trade_regime(cds, i)
                pats = pat.detect_candle_patterns(cds[-8:])
                pat_hit = next((p.get("pattern") or p.get("name")
                                for p in reversed(pats)
                                if p.get("direction") == sig), None)
                if pat_hit is None and pats:
                    pat_hit = pats[-1].get("pattern") or pats[-1].get("name")
                if e20 and e50 and e20 > e50:
                    trend = "Alta"
                elif e20 and e50:
                    trend = "Baixa"
                elif ts and ts > 0.5 and len(closes) > 20:
                    trend = "Alta" if closes[-1] > closes[-20] else "Baixa"
                else:
                    trend = "Lateral"
                aligned = (sig == "call" and rsi_v and rsi_v < 70) or \
                          (sig == "put" and rsi_v and rsi_v > 30)
                agree = -1
                if len(cds) >= 4:
                    closed = cds[-4:-1]
                    agree = sum(1 for c in closed
                                if (c["close"] > c["open"]) == (sig == "call"))
                ctx5 = None
                if len(cds) >= 15:
                    now5 = int(time.time()) // 300 * 300
                    c5 = {}
                    for c in cds:
                        b5 = int(c["time"]) // 300 * 300
                        if b5 < now5:
                            c5[b5] = c["close"]
                    keys5 = sorted(c5)
                    if len(keys5) >= 3:
                        up5 = c5[keys5[-1]] > c5[keys5[-3]]
                        ctx5 = "up" if up5 else "down"
                p_cal, n_eff = calib.estimate(self.calib, cls, voter_keys[sig])
                n_set, _w_set, p_set = calib.set_stats(
                    self.calib, cls, voter_keys[sig])
                if calib.no_trade_exact(self.calib, cls, voter_keys[sig]):
                    return None
                grade = calib.grade_set(n_set, p_set)
                conf = int(round(100 * p_cal))
                watch = grade not in ("forte", "valido") or \
                    len(voter_keys[sig]) < 2
                hist_ok = mc and ((sig == "call" and mc["hist"] > 0) or
                                  (sig == "put" and mc["hist"] < 0))
                mom = "Forte" if (aligned and hist_ok) else \
                      "Fraco" if aligned else "Contra"
                vol = ("Alta" if atr_pct is not None and atr_pct > 0.66 else
                       "Média" if atr_pct is not None and atr_pct > 0.33
                       else "Baixa")
                candle_t = int(cds[-1]["time"]) if cds else 0
                expires_in = max(0, candle_t + 60 - int(time.time()))
                return {"asset": a, "signal": sig, "confidence": conf,
                        "score": raw, "grade": grade,
                        "grade_label": calib.LABEL[grade],
                        "n_hist": n_eff, "agree3": agree, "cls": cls,
                        "votes": f"{n}/{len(VOTERS)}",
                        "voters": voter_keys[sig],
                        "ctx5": ctx5,
                        "sig_time": candle_t,
                        "sig_id": f"{a}-{sig}-{candle_t}",
                        "expires_in": expires_in,
                        "action": "COMPRAR AGORA" if sig == "call"
                                  else "VENDER AGORA",
                        "reasons": why[:6],
                        "watch": watch,
                        "price": round(closes[-1], 5),
                        "rsi": round(rsi_v, 1) if rsi_v else None,
                        "trend": trend, "momentum": mom, "volatility": vol,
                        "pattern": pat_hit or "—",
                        "src": cached.get("src"),
                        "regime_ok": not blocked,
                        "stale": bool(cds and
                                      time.time() - (cds[-1]["time"] or 0)
                                      > 300),
                        "suggested_expiry": 60}
            except Exception:
                import traceback as _tb
                self.last_scan_errs[a] = _tb.format_exc()[-400:]
                return None

        rows = [x for x in (scan_one(a) for a in assets) if x]
        # dedup por (classe base, direção, preço) — igual ao original
        seen, deduped = {}, []
        for r in sorted(rows, key=lambda r: r["cls"] == "synthetic"):
            base = r["asset"].replace("(1s)", "")
            key = (base, r["signal"], round(r["price"], 3))
            if key in seen:
                continue
            seen[key] = True
            deduped.append(r)
        rows = [r for r in deduped if r["confidence"] >= min_conf]
        rows.sort(key=lambda r: (r["watch"], -r["confidence"], -r["score"]))
        if len(rows) < 3:
            below = [r for r in deduped if r["confidence"] < min_conf]
            below.sort(key=lambda r: (-r["confidence"], -r["score"]))
            for r in below[:max(0, 6 - len(rows))]:
                r["watch"] = True
                rows.append(r)
        rows = rows[:12]
        if self.running and not self.paused:
            self._sig_track(rows)
        return {"signals": rows, "scanned": len(assets),
                "cached": len(self.cache),
                "connected": self.feed.connected,
                "feed_mode": self.feed.mode,
                "host": self.feed.host,
                "ts": time.time()}


engine = SignalEngine()
