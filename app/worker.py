"""PINHANHA 2.0 — worker headless (corre no GitHub Actions).

Publica o estado em `bot_state` / `deriv_feed` (lido pela edge function
p2-panel que serve o painel) e executa os comandos de `bot_cmds`
(ligação Deriv, selecção de conta, autotrade, limites, queries pesadas).

A Deriv tem API oficial — ligação directa, sem relay nem bloqueios.

    python -m app.worker
"""

import asyncio
import logging
import os
import time

from . import ai
from . import patterns
from . import sigstore
from . import tradestore
from . import execution
from . import streaks
from . import tickmind
from . import variants as vmod
from . import forex_engine as fx
from .analytics import market_analysis
from .assets import (ASSETS, SCAN_ASSETS, deriv_symbol, asset_cls,
                     asset_label, is_247)
from .backtest import run_backtest
from .engine import engine, VOTERS
from .knowledge import KNOWLEDGE
from .strategies import STRATEGIES

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")

DEADLINE = time.time() + int(os.getenv("WORKER_SECONDS", "20400"))
trader = None
_last_vrank = 0.0


# ------------------------------------------------------------------ publish

async def _publish():
    d = await engine.scan(50)
    await sigstore.state_put("signals", d)
    st = engine.status()
    st["autotrade"] = trader.status()
    st["deriv_feed"] = engine.feed.status()
    await sigstore.state_put("status", st)
    await sigstore.state_put("sigstats", engine.sig_stats())
    fs = engine.feed.state
    await sigstore.state_put("assets", [
        {"asset": n, "symbol": s, "cls": c, "label": l, "schedule": sc,
         "is247": is_247(n), "mode": fs.get(s, {}).get("mode", "off"),
         "err": fs.get(s, {}).get("err")}
        for n, (s, c, l, sc) in ASSETS.items()])
    stats = engine.sig_stats()["strategies"]
    await sigstore.state_put("strategies", {
        k: {"name": v["name"], "desc": v["desc"],
            "min_candles": v["min_candles"], "voter": k in VOTERS,
            "measured_wr": (stats.get(k) or {}).get("wr"),
            "measured_n": (stats.get(k) or {}).get("n")}
        for k, v in STRATEGIES.items()})
    await sigstore.state_put("trades", {
        "trades": await tradestore.list_trades(limit=60)})
    # sigdiag lite — cache + erros de scan
    now = time.time()
    await sigstore.state_put("sigdiag", {
        "assets": {a: {"src": e.get("src"),
                       "age_s": round(now - e["ts"], 1),
                       "n": len(e["candles"]),
                       "last_candle": e["candles"][-1]["time"]
                       if e["candles"] else None}
                   for a, e in engine.cache.items()},
        "connected": engine.feed.connected, "host": engine.feed.host,
        "now": now, "scan_errs": engine.last_scan_errs})
    # velas → deriv_feed (upsert em lote, um POST)
    rows = [{"asset": a, "candles": e["candles"][-150:]}
            for a, e in engine.cache.items() if e["candles"]]
    if rows:
        await sigstore.feed_upsert(rows)
    # análise de ticks (barato — corre sempre)
    tick_out = {}
    for a in SCAN_ASSETS:
        sym = deriv_symbol(a)
        ticks = list(engine.feed.ticks.get(sym, []))
        if ticks:
            tick_out[a] = tickmind.analyze(ticks)
    if tick_out:
        await sigstore.state_put("tickmind", tick_out)
    # streaks + ranking de variantes (pesado — a cada ~15 min)
    global _last_vrank
    if time.time() - _last_vrank > 900:
        _last_vrank = time.time()
        try:
            hist = await sigstore.list_history(limit=2000)
            await sigstore.state_put("streaks", streaks.report(hist))
        except Exception:
            log.exception("streaks")
        try:
            scored = []
            done = 0
            for a in SCAN_ASSETS:
                if done >= 3:
                    break
                cds = (engine.cache.get(a) or {}).get("candles") or []
                if len(cds) < 60:
                    continue
                done += 1
                for v in vmod.VARIANTS:
                    n, w, wr = vmod.rank(v, cds, horizon=1, max_eval=60)
                    if n >= 8:
                        scored.append({"id": v["id"], "family": v["family"],
                                       "asset": a, "n": n, "wr": round(wr, 3)})
            scored.sort(key=lambda x: -x["wr"])
            await sigstore.state_put("variants", {
                "total": len(vmod.VARIANTS),
                "families": len({v["family"] for v in vmod.VARIANTS}),
                "evaluated": len(scored),
                "top": scored[:40],
                "ts": time.time()})
        except Exception:
            log.exception("variants")
        # ---------- real_forex_engine ----------
        try:
            known_fx = [deriv_symbol(a) for a in SCAN_ASSETS
                        if asset_cls(a) == "forex"]
            fx_syms = await fx.discover_forex(engine.feed, known_fx)
            await sigstore.state_put("forex_symbols", {
                "n": len(fx_syms), "symbols": fx_syms,
                "validated": all(s["market"] == "forex" for s in fx_syms),
                "ts": time.time()})
        except Exception:
            log.exception("forex_symbols")
        try:
            fx_out = {}
            for a in SCAN_ASSETS:
                if asset_cls(a) != "forex":
                    continue
                cds = (engine.cache.get(a) or {}).get("candles") or []
                fx_out[a] = fx.scan(cds)
            await sigstore.state_put("forex_lab", {
                "assets": fx_out,
                "n_variants": len(fx.FOREX_VARIANTS), "ts": time.time()})
        except Exception:
            log.exception("forex_lab")


# ------------------------------------------------------------------ cmds

async def _digits(asset, n=200):
    sym = deriv_symbol(asset)
    ticks = list(engine.feed.ticks.get(sym, []))[-n:]
    pip = engine.feed.pip_size.get(sym, 2)
    if not ticks:
        candles = engine.feed.candles.get(sym) or []
        if not candles:
            return {"asset": asset, "available": False,
                    "reason": "sem dados de ticks nem candles"}
        digits = [int(round(c.get("close", 0) * 10 ** pip)) % 10
                  for c in candles[-n:]]
        counts = {str(d): digits.count(d) for d in range(10)}
        return {"asset": asset, "available": True, "n": len(digits),
                "source": "candles", "counts": counts,
                "last": digits[-20:][::-1],
                "last_price": candles[-1].get("close"),
                "ts": candles[-1].get("time")}
    digits = [int(round(t["quote"] * 10 ** pip)) % 10 for t in ticks]
    counts = {str(d): digits.count(d) for d in range(10)}
    return {"asset": asset, "available": True, "n": len(digits),
            "counts": counts, "last": digits[-20:][::-1],
            "last_price": ticks[-1]["quote"], "ts": ticks[-1]["epoch"]}


async def _query(p):
    route, asset = p.get("route"), p.get("asset")
    if route in ("analysis", "patterns", "ai_insight"):
        rows = await engine.candles_for(asset, 150)
        if not rows:
            return {"error": "sem dados"}
        if route == "analysis":
            return market_analysis(rows)
        if route == "patterns":
            return patterns.scan_candles(rows)
        ana = market_analysis(rows)
        return ai.ai_insight(asset, rows, patterns.scan_candles(rows), ana)
    if route == "digits":
        return await _digits(asset, int(p.get("n", 200)))
    if route == "backtest":
        cds = await engine.candles_for(asset, min(int(p.get("n_candles", 500)), 1000))
        if not cds:
            return {"ok": False, "error": f"sem dados Deriv para {asset}"}
        return run_backtest(p.get("strategy"), cds[-int(p.get("n_candles", 500)):],
                            asset, 60, float(p.get("amount", 10)),
                            int(p.get("expiry_candles", 5)))
    if route == "portfolio":
        if not trader.ws or not trader.ws.connected:
            return {"connected": False, "contracts": []}
        pf = await trader.ws.portfolio()
        return {"connected": True, "contracts": pf.get("contracts") or []}
    if route == "profit-table":
        if not trader.ws or not trader.ws.connected:
            return {"connected": False, "transactions": []}
        tx = await trader.ws.profit_table(limit=int(p.get("limit", 30)))
        return {"connected": True, "transactions": tx}
    return {"error": f"rota desconhecida {route}"}


async def _cmds():
    for c in await sigstore.cmds_pending():
        res = None
        try:
            k, p = c.get("kind"), c.get("payload") or {}
            if k == "engine":
                await getattr(engine, p.get("action", "start"))()
                res = {"ok": True}
            elif k == "connect":
                ok, err = await trader.connect(p.get("token", ""))
                res = trader.status()
                if not ok:
                    res = {**res, "ok": False, "detail": err}
            elif k == "select":
                ok, err = await trader.select_account(p.get("loginid"))
                res = trader.status()
                if not ok:
                    res = {**res, "ok": False, "detail": err}
            elif k == "disconnect":
                await trader.disconnect()
                res = trader.status()
            elif k == "autotrade":
                ok, err = await trader.set_enabled(bool(p.get("on")))
                res = trader.status()
                if not ok:
                    res = {**res, "ok": False, "detail": err}
            elif k == "emergency":
                await trader.set_emergency(bool(p.get("on")))
                res = trader.status()
            elif k == "limits":
                await trader.risk.save(p.get("limits") or p)
                res = trader.status()
            elif k == "query":
                res = await _query(p)
            else:
                res = {"ok": False, "msg": f"kind desconhecido {k}"}
        except Exception as e:
            res = {"ok": False, "detail": str(e)[:300]}
        await sigstore.cmd_done(c["id"], res)


# ------------------------------------------------------------------ main

async def main():
    global trader
    trader = execution.init(engine)
    await engine.start()
    try:
        await trader.restore()
    except Exception:
        log.exception("restore")
    await sigstore.state_put("knowledge", KNOWLEDGE)
    log.info("worker PINHANHA 2 a correr até %s",
             time.strftime("%H:%M:%S", time.gmtime(DEADLINE)))
    while time.time() < DEADLINE:
        try:
            await _publish()
        except Exception:
            log.exception("publish")
        try:
            await _cmds()
        except Exception:
            log.exception("cmds")
        await asyncio.sleep(12)


if __name__ == "__main__":
    asyncio.run(main())
