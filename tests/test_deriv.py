"""Testes do adaptador Deriv: normalização, mapeamento, merges e rotação."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import ASSETS, SCAN_ASSETS, deriv_symbol, asset_cls, is_247
from app.deriv_feed import DerivFeed, _to_candle, WS_HOSTS


def test_candle_normalization():
    c = _to_candle({"epoch": "1792184100", "open": "1.1239", "high": "1.1240",
                    "low": "1.1238", "close": "1.12389"})
    assert c == {"time": 1792184100, "open": 1.1239, "high": 1.124,
                 "low": 1.1238, "close": 1.12389}


def test_candle_rejects_bad():
    assert _to_candle({"epoch": 0, "open": 1, "high": 1, "low": 1, "close": 1}) is None
    assert _to_candle({"epoch": 5, "open": -1, "high": 1, "low": 1, "close": 1}) is None
    assert _to_candle({"epoch": 5, "open": 2, "high": 1, "low": 1.5, "close": 1.8}) is None  # high<low
    assert _to_candle({"foo": "bar"}) is None


def test_symbol_mapping():
    assert deriv_symbol("EURUSD") == "frxEURUSD"
    assert deriv_symbol("BTCUSD") == "cryBTCUSD"
    assert deriv_symbol("V100") == "R_100"
    assert deriv_symbol("INEXISTENTE") is None
    for a in SCAN_ASSETS:
        assert deriv_symbol(a), a
        assert asset_cls(a) in ("forex", "crypto", "synthetic")
    assert is_247("V100") and not is_247("EURUSD")


def test_hosts_have_app_id():
    f = DerivFeed(["R_100"], app_id=4242)
    assert len(WS_HOSTS) >= 3
    for h in WS_HOSTS:
        assert "{app}" in h and "wss://" in h


def test_merge_candles_orders():
    f = DerivFeed(["R_100"])
    f._merge_candles("R_100", [
        {"time": 120, "open": 1, "high": 2, "low": 1, "close": 2},
        {"time": 60, "open": 1, "high": 1, "low": 0.5, "close": 1}])
    assert [c["time"] for c in f.candles["R_100"]] == [60, 120]
    f._merge_candles("R_100", [
        {"time": 120, "open": 1, "high": 9, "low": 1, "close": 8}])
    assert f.candles["R_100"][-1]["close"] == 8   # actualiza a mesma vela


def test_tick_merges_into_candle():
    f = DerivFeed(["R_100"], granularity=60)
    # epoch em segundos (formato real Deriv)
    f._on_deriv_tick({"symbol": "R_100", "epoch": 1_792_000_000, "quote": 689.5,
                      "pip_size": 2})
    cds = f.candles["R_100"]
    assert len(cds) == 1 and cds[-1]["time"] == 1_792_000_000 - 1_792_000_000 % 60 \
        and cds[-1]["close"] == 689.5
    f._on_deriv_tick({"symbol": "R_100", "epoch": 1_792_000_015, "quote": 700.0})
    assert cds[-1]["high"] == 700.0
    # epoch em milissegundos → normalizado para segundos
    f._on_deriv_tick({"symbol": "R_100", "epoch": 1_792_000_061_000,
                      "quote": 690.0})
    assert len(cds) == 2 and cds[-1]["time"] == 1_792_000_060 - 1_792_000_060 % 60
    assert f.ticks["R_100"] and f.ticks["R_100"][-1]["quote"] == 690.0


def test_tick_rejects_garbage():
    f = DerivFeed(["R_100"])
    f._on_deriv_tick({"symbol": "R_100", "epoch": "x", "quote": "y"})
    f._on_deriv_tick({"symbol": "R_100", "epoch": 100, "quote": -5})
    f._on_deriv_tick({"epoch": 100, "quote": 5})
    assert not f.candles


def test_status_shape():
    f = DerivFeed(["R_100", "frxEURUSD"], app_id=1)
    st = f.status()
    assert st["connected"] is False and st["symbols"] == 2
    assert st["app_id"] == 1 and "per_symbol" in st
