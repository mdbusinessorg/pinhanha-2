"""Testes: contrato das estratégias, pipeline de sinais, calibração."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.strategies import STRATEGIES
from app.engine import SignalEngine, VOTERS


def flat(n=80, price=1.10):
    return [{"open": price, "high": price + .001, "low": price - .001,
             "close": price, "time": i} for i in range(n)]


def uptrend(n=80):
    return [{"open": 1 + i * .01, "high": 1.005 + i * .01, "low": .995 + i * .01,
             "close": 1.008 + i * .01, "time": i} for i in range(n)]


def downtrend(n=80):
    return [{"open": 2 - i * .01, "high": 2.005 - i * .01, "low": 1.995 - i * .01,
             "close": 1.992 - i * .01, "time": i} for i in range(n)]


def test_strategies_contract():
    for candles in (flat(), uptrend(), downtrend(), flat(3)):
        for sid, s in STRATEGIES.items():
            r = s["fn"](candles)
            assert r["signal"] in ("call", "put", None), sid
            assert isinstance(r["confidence"], (int, float)), sid
            assert isinstance(r["reasons"], list), sid
            assert r["risk"] in ("LOW", "MEDIUM", "HIGH"), sid


def test_voters_exist():
    for k in VOTERS:
        assert k in STRATEGIES, f"votante {k} em falta"
        assert STRATEGIES[k]["min_candles"] > 0


def test_scan_pipeline():
    """O scan corre sem erros com dados sintéticos e respeita o contrato."""
    eng = SignalEngine()
    now = int(time.time()) // 60 * 60
    cds = uptrend(120)
    for i, c in enumerate(cds):
        c["time"] = now - (len(cds) - i) * 60
    eng.cache["V100"] = {"ts": time.time(), "candles": cds, "src": "test"}
    import asyncio
    out = asyncio.run(eng.scan(min_conf=0))
    assert "signals" in out and out["scanned"] >= 1
    for s in out["signals"]:
        assert s["signal"] in ("call", "put")
        assert 0 <= s["confidence"] <= 100
        assert s["sig_id"].startswith(s["asset"])


def test_sig_evaluate_win_loss():
    """Avaliação 60s: primeira vela fechada >= due decide WIN/LOSS."""
    eng = SignalEngine()
    now = time.time()
    eng.cache["V100"] = {"ts": now, "src": "test", "candles": [
        {"time": now - 60, "open": 99, "high": 101, "low": 99, "close": 100.5},
        {"time": now, "open": 100.5, "high": 103, "low": 100, "close": 102},
    ]}
    # due = open da segunda vela → a avaliação usa o close dessa vela (102)
    eng.sig_track.append({"id": "t1", "asset": "V100", "signal": "call",
                          "entry": 100.0, "conf": 60, "watch": False,
                          "cls": "synthetic", "voters": ["ai_turbo"],
                          "ctx5": None, "emit_ts": now - 90, "due": now,
                          "result": "pending", "exit": None, "eval_ts": None})
    done = eng._sig_evaluate()
    assert len(done) == 1 and done[0]["result"] == "win" and done[0]["exit"] == 102


def test_strat_weight_neutral_small_n():
    eng = SignalEngine()
    assert eng.strat_weight("ai_turbo") == 1.0
