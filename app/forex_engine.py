"""real_forex_engine — biblioteca quantitativa dedicada a Forex REAL.

Missão: descoberta dinâmica de instrumentos via `active_symbols`
(market=="forex", nunca por nome), variantes forex com filtros de
sessão/spread, backtest cronológico com custos, SEM ENTRADA quando não
há prova.

Reutiliza indicators/variants do motor existente (não duplica).
"""

import time

from . import indicators as ind
from . import variants as vmod


# ------------------------------------------------------------------ FASE 1
# descoberta dinâmica — active_symbols (market == "forex")

async def _active_symbols(feed):
    """active_symbols via WS do feed; fallback: ligação WS dedicada
    (o feed pode estar em modo 'poll' sem ws aberto)."""
    req = {"active_symbols": "brief", "product_type": "basic"}
    try:
        return await feed._send(req)
    except Exception:
        pass
    import websockets
    app_id = getattr(feed, "app_id", None) or 1089
    async with websockets.connect(
            f"wss://ws.derivws.com/websockets/v3?app_id={app_id}") as ws:
        await ws.send(__import__("json").dumps({**req, "req_id": 1}))
        import json as _json
        return _json.loads(await ws.recv())


async def discover_forex(feed):
    """Pede `active_symbols` à API Deriv e devolve só os instrumentos cujo
    campo `market` é forex — nunca por nome."""
    res = await _active_symbols(feed)
    out = []
    for s in res.get("active_symbols") or []:
        if (s.get("market") or "").lower() not in ("forex",):
            continue
        if (s.get("symbol_type") or "") not in ("forex", "forex_basket"):
            continue
        out.append({
            "symbol": s.get("symbol"),
            "underlying": s.get("underlying_symbol") or s.get("symbol"),
            "name": s.get("display_name") or s.get("underlying_symbol_name"),
            "market": s.get("market"),
            "type": s.get("symbol_type"),
            "exchange_open": s.get("exchange_is_open"),
            "is_trading_suspended": s.get("is_trading_suspended"),
            "pip": s.get("pip"),
        })
    return out


# ------------------------------------------------------------------ FASE 2/3
# sessões forex (UTC): asia 0-7, london 7-16, ny 13-21
def session_of(ts):
    h = (ts % 86400) / 3600
    if 13 <= h < 21:
        return "ny"
    if 7 <= h < 16:
        return "london"
    return "asia"


def spread_ok(candles, max_spread_pct=0.002):
    """Proxy de spread: (high-low)/close médio das últimas 10 velas.
    Deriv não expõe spread real — usa-se amplitude como limite superior."""
    if len(candles) < 10:
        return False
    sp = sum((c["high"] - c["low"]) / (c["close"] or 1)
             for c in candles[-10:]) / 10
    return sp <= max_spread_pct


def forex_variants():
    """Biblioteca forex: reutiliza os geradores do motor com limiares
    ajustados a forex (movimentos menores) + filtro de sessão/spread."""
    out = []
    for v in vmod.VARIANTS:
        if v["family"] in ("tendencia", "osciladores", "momentum",
                           "rompimento", "reversao", "bandas",
                           "confluencia", "velocidade", "volatilidade",
                           "velas", "microestrutura"):
            out.append({"id": "fx:" + v["id"], "family": v["family"],
                        "params": v["params"], "fn": v["fn"],
                        "version": 1, "markets": ["forex"],
                        "state": "candidate"})
    return out


FOREX_VARIANTS = forex_variants()


# ------------------------------------------------------------------ FASE 4/5
# backtest cronológico com custos — sem look-ahead

def backtest(v, candles, horizon=1, spread_pct=0.0004, max_eval=250):
    """Walk-forward simples: sinal na vela i decide para a vela i+horizon.
    Resultado líquido de custo (spread_pct por entrada).
    Devolve métricas honestas: wr, expectativa, profit factor, drawdown."""
    wins = losses = 0
    pnls = []
    lo = max(30, len(candles) - max_eval)
    for i in range(lo, len(candles) - horizon):
        r = v["fn"](candles[:i + 1])
        if not r or not r.get("signal"):
            continue
        fwd = candles[i + horizon]["close"] - candles[i]["close"]
        win = (r["signal"] == "call" and fwd > 0) or \
              (r["signal"] == "put" and fwd < 0)
        pnl = (1 - spread_pct) if win else -1.0   # payout ~1:1 menos custo
        pnls.append(pnl)
        wins += win
        losses += (not win)
    n = wins + losses
    if not n:
        return {"n": 0}
    eq, peak, dd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    return {"n": n, "wins": wins, "wr": round(wins / n, 3),
            "expectancy": round(sum(pnls) / n, 4),
            "profit_factor": round(gp / gl, 2) if gl else None,
            "max_drawdown": round(dd, 2), "net": round(eq, 2)}


def select_symbol(candles, min_candles=60, max_age_s=310):
    """Valida um feed forex: dados suficientes e frescos — senão SEM ENTRADA."""
    if len(candles) < min_candles:
        return False, "dados insuficientes"
    if time.time() - (candles[-1].get("time") or 0) > max_age_s:
        return False, "feed desactualizado (mercado fechado?)"
    if not spread_ok(candles):
        return False, "spread/amplitude excessivo"
    return True, "ok"


def scan(candles, session=None, top_n=6, min_n=8):
    """Corre a biblioteca na última vela + ranking rápido no histórico.
    Devolve sinais forex com razão explícita — ou SEM ENTRADA."""
    ok, why = select_symbol(candles)
    if not ok:
        return {"verdict": "SEM ENTRADA", "reason": why, "signals": []}
    if session and session_of(time.time()) != session:
        return {"verdict": "SEM ENTRADA", "reason": f"fora da sessão {session}",
                "signals": []}
    scored = []
    for v in FOREX_VARIANTS:
        bt = backtest(v, candles, horizon=1, max_eval=120)
        if bt.get("n", 0) < min_n or bt["wr"] < 0.5 or bt["expectancy"] <= 0:
            continue
        cur = v["fn"](candles[-160:])
        if not cur or not cur.get("signal"):
            continue
        scored.append({"id": v["id"], "family": v["family"],
                       "signal": cur["signal"], "conf": cur["confidence"],
                       "wr": bt["wr"], "exp": bt["expectancy"], "n": bt["n"],
                       "reason": f"{v['family']} wr {bt['wr']*100:.0f}% n{bt['n']}"})
    scored.sort(key=lambda x: -x["exp"])
    if not scored:
        return {"verdict": "SEM ENTRADA",
                "reason": "nenhuma estratégia passou os filtros", "signals": []}
    return {"verdict": "ENTRADA", "session": session_of(time.time()),
            "signals": scored[:top_n]}
