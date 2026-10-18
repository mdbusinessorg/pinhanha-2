"""Testes da camada de execução — SEM chamadas reais à Deriv (WS mockado)."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.execution import AutoTrader, RiskManager, DEFAULT_LIMITS


def sig(conf=60, asset="V100", sid="s1", voters=("a", "b")):
    return {"sig_id": sid, "asset": asset, "signal": "call",
            "confidence": conf, "watch": False, "price": 100.0,
            "voters": list(voters)}


def mk_trader():
    t = AutoTrader.__new__(AutoTrader)
    t.engine = None
    t.risk = RiskManager()
    t.ws = AsyncMock()
    t.enabled = True
    t.emergency = False
    t.mode = "demo"
    t.trades = {}
    t._day_rows_cache = (0, [])
    t._busy = set()
    t._rec_task = None
    t.last_error = None
    t.last_order = None
    t.last_signal = None
    t.ws.account = {"loginid": "VRT123", "currency": "USD", "is_virtual": True,
                    "balance": 9999}
    return t


# ------------------------------------------------------------- RiskManager

def test_risk_defaults_pass():
    r = RiskManager()
    ok, _ = r.check(sig(), open_count=0, day_rows=[])
    assert ok


def test_risk_blocks_limits():
    r = RiskManager()
    # conf baixa
    ok, why = r.check(sig(conf=10), 0, [])
    assert not ok and "conf" in why
    # max abertas
    ok, why = r.check(sig(), open_count=3, day_rows=[])
    assert not ok and "abertas" in why
    # perda diária
    day = [{"result": "loss", "profit": -4, "stake": 1, "state": "settled"} for _ in range(4)]
    ok, why = r.check(sig(), 0, day)
    assert not ok and "diária" in why
    # ops/dia
    day = [{"result": "win", "profit": 1, "stake": 1, "state": "settled"} for _ in range(30)]
    ok, why = r.check(sig(), 0, day)
    assert not ok and "dia" in why
    # exposição
    day = [{"result": "win", "profit": 1, "stake": 20, "state": "settled"} for _ in range(3)]
    ok, why = r.check(sig(), 0, day)
    assert not ok and "exposição" in why
    # perdas seguidas
    r.consec_losses = 5
    ok, why = r.check(sig(), 0, [])
    assert not ok and "seguidas" in why
    # falhas seguidas
    r2 = RiskManager()
    r2.err_streak = 3
    ok, why = r2.check(sig(), 0, [])
    assert not ok and "falhas" in why


def test_no_martingale():
    """Stake NUNCA aumenta após perdas ou com confiança alta."""
    r = RiskManager()
    assert r.limits["stake"] == DEFAULT_LIMITS["stake"]
    for _ in range(3):
        r.on_result(-1.0)
    assert r.limits["stake"] == DEFAULT_LIMITS["stake"]   # inalterado
    ok, _ = r.check(sig(conf=99), 0, [])
    assert r.limits["stake"] == DEFAULT_LIMITS["stake"]


# ------------------------------------------------------------- dedup/estados

def test_dedup_same_sig():
    t = mk_trader()
    t.trades["s1"] = {"id": "s1", "state": "open"}
    asyncio.run(t.on_signal(sig(sid="s1")))
    assert t.trades["s1"]["state"] == "open"      # não re-processa


def test_consensus_blocks():
    """Sinal sem consenso (<2 votantes) nunca chega à execução."""
    t = mk_trader()
    asyncio.run(t.on_signal(sig(voters=("a",))))
    assert "s1" not in t.trades
    assert t.last_signal["why"] == "consenso <2 votantes"


def test_non_synthetic_skipped():
    """Forex/crypto não executável (Deriv não oferece Rise/Fall 60s)."""
    t = mk_trader()
    asyncio.run(t.on_signal(sig(asset="EURUSD")))
    assert "s1" not in t.trades
    assert "sintéticos" in t.last_signal["why"]


def test_disabled_blocks():
    t = mk_trader()
    t.enabled = False
    asyncio.run(t.on_signal(sig()))
    assert "s1" not in t.trades


def test_emergency_blocks():
    t = mk_trader()
    t.emergency = True
    asyncio.run(t.on_signal(sig()))
    assert "s1" not in t.trades


def test_no_real_without_confirm():
    """set_enabled exige ligação+modo; emergência bloqueia activação."""
    t = mk_trader()
    t.ws = None
    ok, err = asyncio.run(t.set_enabled(True))
    assert not ok and "liga" in err
    t2 = mk_trader()
    t2.emergency = True
    ok, err = asyncio.run(t2.set_enabled(True))
    assert not ok and "emergência" in err


def test_set_emergency_stops_entries():
    t = mk_trader()
    t.enabled = True
    asyncio.run(t.set_emergency(True))
    assert t.emergency and not t.enabled


# ------------------------------------------------------------- pipeline mock

def test_pipeline_buy_flow():
    """candidate→validated→proposal→buy_sent→confirmed→open (mock)."""
    t = mk_trader()
    t.ws.proposal = AsyncMock(return_value={"id": "p1", "ask_price": 1.0,
                                            "payout": 1.92})
    t.ws.buy = AsyncMock(return_value={"contract_id": 555, "buy_price": 1.0})
    t.ws.watch_contract = AsyncMock()
    # tradestore é chamado via asyncio.create_task — substituir por no-op
    import app.execution as ex
    orig = ex.tradestore.upsert_trade
    ex.tradestore.upsert_trade = AsyncMock()
    ex.tradestore.list_trades = AsyncMock(return_value=[])
    try:
        asyncio.run(t._pipeline(sig()))
    finally:
        ex.tradestore.upsert_trade = orig
    tr = t.trades["s1"]
    assert tr["state"] == "open" and tr["contract_id"] == "555"
    t.ws.buy.assert_awaited_once()


def test_pipeline_reject_risk():
    t = mk_trader()
    t.risk.consec_losses = 99
    import app.execution as ex
    ex.tradestore.upsert_trade = AsyncMock()
    ex.tradestore.list_trades = AsyncMock(return_value=[])
    asyncio.run(t._pipeline(sig()))
    assert t.trades["s1"]["state"] == "rejected"
    assert "seguidas" in t.trades["s1"]["error"]
    t.ws.buy.assert_not_called()


def test_pipeline_buy_timeout_no_retry():
    """Buy com timeout → fica buy_sent + reconcile consulta portfolio,
    nunca repete a compra automaticamente."""
    t = mk_trader()
    t.ws.proposal = AsyncMock(return_value={"id": "p1", "ask_price": 1.0,
                                            "payout": 1.9})
    t.ws.buy = AsyncMock(side_effect=TimeoutError("sem resposta"))
    t.ws.portfolio = AsyncMock(return_value={"contracts": []})
    import app.execution as ex
    ex.tradestore.upsert_trade = AsyncMock()
    ex.tradestore.list_trades = AsyncMock(return_value=[])
    asyncio.run(t._pipeline(sig()))
    tr = t.trades["s1"]
    assert tr["state"] == "rejected"          # reconciliou e não achou contrato
    t.ws.buy.assert_awaited_once()            # UMA tentativa, sem retry


def test_starts_stopped():
    """§1: estado inicial sempre PARADO."""
    assert AutoTrader(None).enabled is False


def test_restore_stays_stopped():
    """§5: estado ON guardado na BD não reactiva após reinício."""
    import app.execution as ex
    t = mk_trader()
    t.enabled = False
    t.ws = None
    t.connect = AsyncMock(return_value=(True, None))
    t.select_account = AsyncMock(return_value=(True, None))
    t.reconcile = AsyncMock()
    t.risk.load = AsyncMock()
    async def cfg(k):
        return {"deriv_token": "x", "deriv_loginid": "VRT123",
                "autotrade_state": {"enabled": True,
                                    "emergency": False}}.get(k)
    orig = ex.tradestore.get_config
    ex.tradestore.get_config = cfg
    ex.tradestore.set_config = AsyncMock()
    try:
        asyncio.run(t.restore())
    finally:
        ex.tradestore.get_config = orig
    assert t.enabled is False          # PARADO mesmo com ON guardado


def test_stop_mid_pipeline():
    """§1/§5: PARAR durante a proposta aborta antes do buy."""
    t = mk_trader()
    async def prop(*a, **kw):
        t.enabled = False                       # PARAR a meio
        return {"id": "p1", "ask_price": 1.0, "payout": 1.92}
    t.ws.proposal = prop
    t.ws.buy = AsyncMock()
    import app.execution as ex
    ex.tradestore.upsert_trade = AsyncMock()
    ex.tradestore.list_trades = AsyncMock(return_value=[])
    asyncio.run(t._pipeline(sig()))
    assert t.trades["s1"]["state"] == "rejected"
    assert "parado" in t.trades["s1"]["error"]
    t.ws.buy.assert_not_called()


def test_poc_settles():
    t = mk_trader()
    t.trades["s1"] = {"id": "s1", "contract_id": "777", "state": "open"}
    t._on_poc({"contract_id": 777, "profit": 0.92, "is_sold": True})
    tr = t.trades["s1"]
    assert tr["state"] == "settled" and tr["result"] == "win"
    assert tr["profit"] == 0.92 and t.risk.consec_losses == 0
    t._on_poc2 = None
    t.trades["s2"] = {"id": "s2", "contract_id": "778", "state": "open"}
    t._on_poc({"contract_id": 778, "profit": -1.0, "is_sold": True})
    assert t.trades["s2"]["result"] == "loss" and t.risk.consec_losses == 1
