"""PINHANHA 2.0 — API + servidor do painel (plataforma de sinais Deriv).

    python -m app.main  →  http://localhost:8000

Auth: login local simples (BOT_PASSWORD env ou 'devin' por defeito) → token.
"""

import secrets
import os
import time
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sigstore
from .analytics import market_analysis
from .assets import ASSETS, SCAN_ASSETS, deriv_symbol, asset_cls, asset_label, is_247
from .backtest import run_backtest
from .engine import engine, VOTERS
from .knowledge import KNOWLEDGE
from .strategies import STRATEGIES

import logging
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="PINHANHA 2.0 — Sinais Deriv")
STATIC = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
async def _startup():
    """Arranca o motor de sinais no boot — plataforma de análise, sem
    execução financeira; o utilizador pode pausar/parar no painel."""
    await engine.start()


BOT_PASSWORD = os.getenv("BOT_PASSWORD", "devin")
TOKENS = set()


def auth(req: Request):
    tok = req.headers.get("x-token") or req.query_params.get("token")
    if tok not in TOKENS:
        raise HTTPException(401, "não autenticado")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": time.time(),
            "connected": engine.feed.connected}


class LoginBody(BaseModel):
    password: str


@app.post("/api/auth/login")
def login(b: LoginBody):
    if b.password != BOT_PASSWORD:
        raise HTTPException(401, "password errada")
    tok = secrets.token_hex(16)
    TOKENS.add(tok)
    return {"token": tok}


# ---------------------------------------------------------------- motor

@app.get("/api/status")
async def status(_=Depends(auth)):
    return engine.status()


@app.post("/api/engine/start")
async def eng_start(_=Depends(auth)):
    await engine.start()
    return {"ok": True}


@app.post("/api/engine/pause")
async def eng_pause(_=Depends(auth)):
    await engine.pause()
    return {"ok": True}


@app.post("/api/engine/resume")
async def eng_resume(_=Depends(auth)):
    await engine.resume()
    return {"ok": True}


@app.post("/api/engine/stop")
async def eng_stop(_=Depends(auth)):
    await engine.stop()
    return {"ok": True}


@app.get("/api/deriv/status")
async def deriv_status(_=Depends(auth)):
    return engine.feed.status()


# ---------------------------------------------------------------- dados

@app.get("/api/assets")
def assets(_=Depends(auth)):
    st = engine.feed.state
    out = []
    for name, (sym, cls, label, sched) in ASSETS.items():
        out.append({"asset": name, "symbol": sym, "cls": cls,
                    "label": label, "schedule": sched, "is247": is_247(name),
                    "mode": st.get(sym, {}).get("mode", "off"),
                    "err": st.get(sym, {}).get("err")})
    return out


@app.get("/api/candles/{asset}")
async def candles_ep(asset: str, period: int = 60, n: int = 120,
                     _=Depends(auth)):
    rows = await engine.candles_for(asset, n)
    return {"asset": asset, "symbol": deriv_symbol(asset),
            "period": period, "candles": rows,
            "src": "deriv"}


@app.get("/api/patterns/{asset}")
async def patterns_ep(asset: str, period: int = 60, _=Depends(auth)):
    from . import patterns
    rows = await engine.candles_for(asset, 150)
    if not rows:
        return {"error": "sem dados"}
    return patterns.scan_candles(rows)


@app.get("/api/analysis/{asset}")
async def analysis(asset: str, _=Depends(auth)):
    candles = await engine.candles_for(asset, 120)
    if not candles:
        return {"error": "sem dados"}
    return market_analysis(candles)


@app.get("/api/ai/insight/{asset}")
async def ai_insight_ep(asset: str, _=Depends(auth)):
    from . import patterns, ai
    rows = await engine.candles_for(asset, 150)
    if not rows:
        return {"error": "sem dados"}
    pat = patterns.scan_candles(rows)
    ana = market_analysis(rows)
    return ai.ai_insight(asset, rows, pat, ana)


@app.get("/api/digits/{asset}")
async def digits_ep(asset: str, n: int = 200, _=Depends(auth)):
    """Estatística dos últimos dígitos dos ticks (índices sintéticos).
    Só disponível em símbolos com stream de ticks ao vivo."""
    sym = deriv_symbol(asset)
    ticks = list(engine.feed.ticks.get(sym, []))[-n:]
    if not ticks:
        return {"asset": asset, "available": False,
                "reason": "sem stream de ticks (modo poll ou sem dados)"}
    pip = engine.feed.pip_size.get(sym, 2)
    digits = []
    for t in ticks:
        q = t["quote"]
        digits.append(int(round(q * 10 ** pip)) % 10)
    counts = {str(d): digits.count(d) for d in range(10)}
    last = digits[-20:][::-1]
    return {"asset": asset, "available": True, "n": len(digits),
            "counts": counts, "last": last,
            "last_price": ticks[-1]["quote"], "ts": ticks[-1]["epoch"]}


# ---------------------------------------------------------------- sinais

_SIG_CACHE = {"ts": 0.0, "data": None}


@app.get("/api/signals")
async def signals_ep(min_conf: int = 50, _=Depends(auth)):
    """Varredura em tempo real — mesmo pipeline do PINHANHA original."""
    if _SIG_CACHE["data"] and time.time() - _SIG_CACHE["ts"] < 1:
        return _SIG_CACHE["data"]
    _SIG_CACHE["data"] = await engine.scan(min_conf)
    _SIG_CACHE["ts"] = time.time()
    return _SIG_CACHE["data"]


@app.get("/api/sigstats")
async def sigstats_ep(_=Depends(auth)):
    return engine.sig_stats()


@app.get("/api/sigdiag")
async def sigdiag(_=Depends(auth)):
    """Diagnóstico: cache + votos reais por par (o mesmo código do scan)."""
    import traceback as _tb
    out = {}
    for a, ent in engine.cache.items():
        cds = ent["candles"]
        info = {"src": ent.get("src"), "age_s": round(time.time() - ent["ts"], 1),
                "n": len(cds),
                "last_candle": cds[-1]["time"] if cds else None,
                "last_close": cds[-1]["close"] if cds else None}
        try:
            votes = {"call": 0, "put": 0}
            errs = []
            for k in VOTERS:
                st = STRATEGIES[k]
                try:
                    r = st["fn"](cds[-(st["min_candles"] + 45):])
                    if r["signal"]:
                        votes[r["signal"]] += 1
                except Exception as e:
                    errs.append(f"{k}:{e}")
            info["votes"] = votes
            info["errs"] = errs[:3]
        except Exception:
            info["scan_err"] = _tb.format_exc()[-300:]
        out[a] = info
    return {"assets": out, "connected": engine.feed.connected,
            "host": engine.feed.host, "now": time.time(),
            "scan_errs": engine.last_scan_errs}


@app.get("/api/history/signals")
async def history_signals(limit: int = 300, _=Depends(auth)):
    """Histórico persistido (Supabase) + resultados em memória.
    Registos importados do PINHANHA original trazem source='import'."""
    rows = await sigstore.list_history(limit=limit)
    mem = [{"id": r["id"], "asset": r["asset"], "signal": r["signal"],
            "entry": r["entry"], "exit": r["exit"], "result": r["result"],
            "conf": r["conf"], "emit_ts": r.get("emit_ts"),
            "eval_ts": r.get("eval_ts"), "source": "live",
            "voters": r.get("voters")}
           for r in engine.sig_results[-limit:]]
    seen = {r.get("id") for r in rows}
    merged = rows + [m for m in mem if m["id"] not in seen]
    merged.sort(key=lambda r: r.get("emit_ts") or 0, reverse=True)
    return {"signals": merged[:limit],
            "persisted": sigstore.enabled()}


# ---------------------------------------------------------------- estratégias

@app.get("/api/strategies")
def strategies(_=Depends(auth)):
    stats = engine.sig_stats()["strategies"]
    out = {}
    for k, v in STRATEGIES.items():
        s = stats.get(k) or {}
        out[k] = {"name": v["name"], "desc": v["desc"],
                  "min_candles": v["min_candles"],
                  "voter": k in VOTERS,
                  "measured_wr": s.get("wr"), "measured_n": s.get("n")}
    return out


class BacktestReq(BaseModel):
    strategy: str
    asset: str = "V100"
    n_candles: int = 500
    amount: float = 10
    expiry_candles: int = 5


@app.post("/api/backtest")
async def backtest(b: BacktestReq, _=Depends(auth)):
    cds = await engine.candles_for(b.asset, min(b.n_candles, 1000))
    if not cds:
        return {"ok": False, "error": f"sem dados Deriv para {b.asset}"}
    return run_backtest(b.strategy, cds[-b.n_candles:], b.asset, 60,
                        b.amount, b.expiry_candles)


@app.get("/api/knowledge")
def knowledge(_=Depends(auth)):
    return KNOWLEDGE


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0",
                port=int(os.getenv("PORT", 8000)))
