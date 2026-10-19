"""Pure-Python technical indicators. No external dependencies.

All functions take a list of candle dicts: {"open","high","low","close","time"}
ordered oldest -> newest, and return the latest indicator value(s) (or full
series when useful). Classic formulas per Wilder, Bollinger, Lane.
"""


def sma(values, period):
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def sma_series(values, period):
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - period:i + 1]) / period)
    return out


def ema_series(values, period):
    if period <= 0:
        return []
    k = 2 / (period + 1)
    out = []
    prev = None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[: period]) / period
            out.append(prev)
            continue
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def ema(values, period):
    s = ema_series(values, period)
    return s[-1] if s else None


def rsi(closes, period=14):
    """Wilder's RSI."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def bollinger(closes, period=20, mult=2.0):
    """Returns (lower, middle, upper) latest values."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    return mid - mult * sd, mid, mid + mult * sd


def macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return None
    f = ema_series(closes, fast)
    s = ema_series(closes, slow)
    line = [None if (fv is None or sv is None) else fv - sv for fv, sv in zip(f, s)]
    valid = [v for v in line if v is not None]
    sig = ema_series(valid, signal)
    if not sig or sig[-1] is None or line[-1] is None:
        return None
    prev_line = line[-2]
    prev_sig = sig[-2]
    return {
        "macd": line[-1],
        "signal": sig[-1],
        "hist": line[-1] - sig[-1],
        "prev_hist": None if prev_line is None or prev_sig is None else prev_line - prev_sig,
    }


def stochastic(candles, k_period=14, d_period=3):
    """Stochastic %K and %D (George Lane)."""
    if len(candles) < k_period + d_period:
        return None
    ks = []
    for i in range(len(candles) - d_period - 1, len(candles)):
        window = candles[max(0, i - k_period + 1): i + 1]
        hh = max(c["high"] for c in window)
        ll = min(c["low"] for c in window)
        k = 50.0 if hh == ll else (candles[i]["close"] - ll) / (hh - ll) * 100
        ks.append(k)
    return {"k": ks[-1], "d": sum(ks[-d_period:]) / d_period}


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def adx(candles, period=14):
    """Wilder's ADX with +DI / -DI."""
    if len(candles) < period * 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        up = candles[i]["high"] - candles[i - 1]["high"]
        dn = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def wilder(vals, n):
        v = sum(vals[:n])
        out = [v]
        for x in vals[n:]:
            v = v - v / n + x
            out.append(v)
        return out

    atr_s = wilder(trs, period)
    pdi_s = [100 * (p / a) if a else 0 for p, a in zip(wilder(plus_dm, period), atr_s)]
    mdi_s = [100 * (m / a) if a else 0 for m, a in zip(wilder(minus_dm, period), atr_s)]
    dxs = [100 * abs(p - m) / (p + m) if (p + m) else 0 for p, m in zip(pdi_s, mdi_s)]
    if len(dxs) < period:
        return None
    adx_val = sum(dxs[-period:]) / period
    return {"adx": adx_val, "plus_di": pdi_s[-1], "minus_di": mdi_s[-1]}


def cci(candles, period=20):
    """Commodity Channel Index — desvio típico do preço vs média (Lambert).
    Devolve o CCI do último período."""
    if len(candles) < period + 1:
        return None
    tp = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    win = tp[-period:]
    m = sum(win) / period
    md = sum(abs(x - m) for x in win) / period
    if md == 0:
        return 0.0
    return (tp[-1] - m) / (0.015 * md)


def cci_series(candles, period=20):
    if len(candles) < period + 1:
        return []
    tp = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    out = []
    for i in range(period, len(tp) + 1):
        win = tp[i - period:i]
        m = sum(win) / period
        md = sum(abs(x - m) for x in win) / period
        out.append(0.0 if md == 0 else (tp[i - 1] - m) / (0.015 * md))
    return out


def willr(candles, period=14):
    """Williams %R — -20 sobrecomprado, -80 sobrevendido (Larry Williams)."""
    if len(candles) < period:
        return None
    win = candles[-period:]
    hh = max(c["high"] for c in win)
    ll = min(c["low"] for c in win)
    if hh == ll:
        return -50.0
    return (hh - candles[-1]["close"]) / (hh - ll) * -100


def realized_vol(closes, period=20):
    """Volatilidade realizada (desvio dos retornos) — proxy do regime vol."""
    if len(closes) < period + 1:
        return None
    import math
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - period, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < period - 2:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))


def body(c):
    return c["close"] - c["open"]


def is_bull(c):
    return c["close"] > c["open"]


def is_bear(c):
    return c["close"] < c["open"]


def is_doji(c, tol=0.1):
    rng = c["high"] - c["low"]
    return rng > 0 and abs(body(c)) / rng <= tol
