"""Backtesting — corre uma estratégia sobre candles reais da Deriv.

Diferença do original: os dados vêm do histórico da Deriv (ticks_history),
não de um feed sintético — os resultados reflectem o instrumento real.
Breakeven típico de payouts binários ≈ 53% (mantido PAYOUT=0.92 do
PINHANHA original para métricas comparáveis).
"""

from .assets import deriv_symbol
from .strategies import STRATEGIES

PAYOUT = 0.92


def run_backtest(strategy_id, candles, asset="", period=60,
                 amount=10.0, expiry_candles=5):
    """Simula trades: sinal na vela i → resultado no fecho da vela i+expiry."""
    if strategy_id not in STRATEGIES:
        return {"ok": False, "error": "estrategia desconhecida"}
    if not candles or len(candles) < 60:
        return {"ok": False, "error": "dados insuficientes"}
    s = STRATEGIES[strategy_id]
    lookback = s["min_candles"]
    trades = []
    eq = 0.0
    curve = []
    i = lookback
    while i < len(candles) - expiry_candles:
        window = candles[max(0, i - lookback - 30): i + 1]
        r = s["fn"](window)
        if r["signal"]:
            entry = candles[i + 1]["open"]
            exit_ = candles[i + expiry_candles]["close"]
            diff = (exit_ - entry) if r["signal"] == "call" else (entry - exit_)
            pnl = round(amount * PAYOUT, 2) if diff > 0 else \
                (-amount if diff < 0 else 0)
            eq += pnl
            trades.append({"i": i, "signal": r["signal"], "entry": entry,
                           "exit": exit_, "pnl": pnl,
                           "confidence": r["confidence"]})
            i += expiry_candles
            continue
        i += 1
        curve.append(round(eq, 2))
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "ok": True, "strategy": strategy_id, "asset": asset,
        "period_analyzed": f"{len(candles)} candles de {period}s (Deriv)",
        "n_candles": len(candles), "trades": len(trades), "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(100 * wins / len(trades), 1) if trades else 0,
        "pnl": round(eq, 2), "curve": curve[-200:],
        "trades_sample": trades[-20:],
        "disclaimer": "Backtest sobre dados reais da Deriv — desempenho "
                      "passado não garante resultados futuros.",
    }
