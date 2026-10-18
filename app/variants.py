"""Gerador + ranking de variantes de estratégias (missão: 1000+ variantes).

Cada variante é uma parametrização REAL de uma família — parâmetros,
períodos, limiares e confirmações diferentes — não nomes repetidos.

`generate()` devolve todas as variantes com id único e regra de entrada.
`evaluate(variant, candles)` devolve {"signal","confidence"} ou None.
`rank(variants, candles, horizon)` mede a acertividade da variante numa
série de velas (sinal → acertou se fechou na direção após `horizon` velas).
"""

import itertools

from . import indicators as ind


def _r(sig, conf):
    return {"signal": sig, "confidence": conf}


# ---------------------------------------------------------------- builders
def _mk_rsi(period, ob, os_, confirm):
    def f(c):
        r = ind.rsi([x["close"] for x in c], period)
        if r is None:
            return None
        if confirm and len(c) > 1:
            r0 = ind.rsi([x["close"] for x in c[:-1]], period)
            if r < os_ and (r0 is not None and r0 < r):
                return _r("call", int(os_ - r + 50))
            if r > ob and (r0 is not None and r0 > r):
                return _r("put", int(r - ob + 50))
            return None
        if r < os_:
            return _r("call", int(os_ - r + 50))
        if r > ob:
            return _r("put", int(r - ob + 50))
        return None
    return f


def _mk_ema(fast, slow, cross):
    def f(c):
        e1, e2 = ind.ema([x["close"] for x in c], fast), ind.ema([x["close"] for x in c], slow)
        if not e1 or not e2:
            return None
        if not cross:
            return _r("call" if e1 > e2 else "put", 55)
        e10 = ind.ema([x["close"] for x in c[:-1]], fast)
        e20 = ind.ema([x["close"] for x in c[:-1]], slow)
        if not e10 or not e20:
            return None
        if e10 <= e20 and e1 > e2:
            return _r("call", 60)
        if e10 >= e20 and e1 < e2:
            return _r("put", 60)
        return None
    return f


def _mk_macd(fast, slow, sig):
    def f(c):
        m = ind.macd([x["close"] for x in c], fast, slow, sig)
        if not m:
            return None
        h = m["hist"]
        if h > 0:
            return _r("call", int(min(90, 50 + abs(h) * 1e4)))
        if h < 0:
            return _r("put", int(min(90, 50 + abs(h) * 1e4)))
        return None
    return f


def _mk_boll(period, mult, mode):
    def f(c):
        b = ind.bollinger([x["close"] for x in c], period, mult)
        if not b:
            return None
        px = c[-1]["close"]
        if mode == "rev":
            if px < b["lower"]:
                return _r("call", 58)
            if px > b["upper"]:
                return _r("put", 58)
        else:
            if px > b["upper"]:
                return _r("call", 56)
            if px < b["lower"]:
                return _r("put", 56)
        return None
    return f


def _mk_mom(n, min_pct, fade):
    def f(c):
        if len(c) < n + 1:
            return None
        chg = (c[-1]["close"] - c[-1 - n]["close"]) / (c[-1 - n]["close"] or 1) * 100
        if abs(chg) < min_pct:
            return None
        if fade:
            return _r("put" if chg > 0 else "call", 55)
        return _r("call" if chg > 0 else "put", int(min(85, 50 + abs(chg) * 20)))
    return f


def _mk_breakout(look, buf):
    def f(c):
        if len(c) < look + 2:
            return None
        hh = max(x["high"] for x in c[-look - 1:-1])
        ll = min(x["low"] for x in c[-look - 1:-1])
        px = c[-1]["close"]
        if px > hh * (1 + buf):
            return _r("call", 57)
        if px < ll * (1 - buf):
            return _r("put", 57)
        return None
    return f


def _mk_stoch(k_p, d_p, ob, os_):
    def f(c):
        s = ind.stochastic(c, k_p, d_p)
        if not s:
            return None
        k, d = s["k"], s["d"]
        if k < os_ and k > d:
            return _r("call", 56)
        if k > ob and k < d:
            return _r("put", 56)
        return None
    return f


def _mk_streak(n, fade):
    def f(c):
        if len(c) < n + 1:
            return None
        seg = c[-n:]
        up = all(x["close"] >= x["open"] for x in seg)
        dn = all(x["close"] <= x["open"] for x in seg)
        if fade:
            if up:
                return _r("put", int(50 + 4 * n))
            if dn:
                return _r("call", int(50 + 4 * n))
        else:
            if up:
                return _r("call", int(50 + 4 * n))
            if dn:
                return _r("put", int(50 + 4 * n))
        return None
    return f


def _mk_doji(n, then):
    def f(c):
        if len(c) < 2:
            return None
        if not ind.is_doji(c[-1], n):
            return None
        prev = c[-2]
        s = "call" if ind.is_bull(prev) else "put"
        if then == "cont":
            s = "put" if s == "call" else "call"
        return _r(s, 54)
    return f


def _mk_body(n_bars, min_ratio, fade):
    def f(c):
        if len(c) < n_bars + 1:
            return None
        rng = sum(x["high"] - x["low"] for x in c[-n_bars - 1:-1]) / n_bars
        b = ind.body(c[-1])
        if rng <= 0 or b < rng * min_ratio:
            return None
        s = "call" if ind.is_bull(c[-1]) else "put"
        if fade:
            s = "put" if s == "call" else "call"
        return _r(s, 55)
    return f


def _mk_confluence(period, need):
    def f(c):
        closes = [x["close"] for x in c]
        r = ind.rsi(closes, period)
        e1, e2 = ind.ema(closes, 9), ind.ema(closes, 21)
        m = ind.macd(closes)
        sc = 0
        if r is not None:
            sc += 1 if r < 40 else -1 if r > 60 else 0
        if e1 and e2:
            sc += 1 if e1 > e2 else -1
        if m:
            sc += 1 if m["hist"] > 0 else -1
        if sc >= need:
            return _r("call", 55 + 5 * need)
        if sc <= -need:
            return _r("put", 55 + 5 * need)
        return None
    return f


def _mk_adx(period, th, di_mode):
    def f(c):
        a = ind.adx(c, period)
        if not a or a["adx"] < th:
            return None
        if di_mode:
            return _r("call" if a["pdi"] > a["mdi"] else "put", int(min(80, 45 + a["adx"])))
        return None
    return f


def _mk_range(n, touch, fade):
    def f(c):
        if len(c) < n + 1:
            return None
        hh = max(x["high"] for x in c[-n:])
        ll = min(x["low"] for x in c[-n:])
        px = c[-1]["close"]
        span = hh - ll or 1
        if fade:
            if px > hh - span * touch:
                return _r("put", 54)
            if px < ll + span * touch:
                return _r("call", 54)
        else:
            mid = (hh + ll) / 2
            if px > mid + span * touch:
                return _r("call", 52)
            if px < mid - span * touch:
                return _r("put", 52)
        return None
    return f


def _mk_accel(n1, n2, mode):
    def f(c):
        if len(c) < n1 + n2 + 1:
            return None
        def ret(k):
            return (c[-1]["close"] - c[-1 - k]["close"]) / (c[-1 - k]["close"] or 1)
        v1, v2 = ret(n1), ret(n2)
        a = v1 - v2
        if mode == "accel":
            if a > 0 and v1 > 0:
                return _r("call", 53)
            if a < 0 and v1 < 0:
                return _r("put", 53)
        else:
            if v1 > 0 and a < 0:
                return _r("put", 52)
            if v1 < 0 and a > 0:
                return _r("call", 52)
        return None
    return f


def _mk_atr(period, hi, lo_, mode):
    def f(c):
        a = ind.atr(c, period)
        if a is None:
            return None
        rng = sum(x["high"] - x["low"] for x in c[-20:]) / 20
        if rng <= 0:
            return None
        ratio = a / rng
        if mode == "hi" and ratio > hi:
            s = "call" if ind.is_bull(c[-1]) else "put"
            return _r(s, 52)
        if mode == "lo" and ratio < lo_:
            s = "call" if ind.is_bull(c[-1]) else "put"
            return _r(s, 50)
        return None
    return f


# ---------------------------------------------------------------- generate
def generate():
    V = []

    def add(fam, name, params, fn):
        vid = f"{name}-" + "-".join(str(p).replace(".", "p") for p in params)
        V.append({"id": vid, "family": fam, "params": list(params), "fn": fn})

    for p, ob, os_, cf in itertools.product(
            (3, 5, 7, 9, 11, 14, 21, 28), (58, 60, 65, 70, 75, 80, 85),
            (15, 20, 25, 30, 35, 40, 42), (0, 1)):
        if ob <= os_ + 15:
            continue
        add("osciladores", f"rsi{p}ob{ob}os{os_}cf{cf}", (p, ob, os_, cf), _mk_rsi(p, ob, os_, cf))
    for f_, s, x in itertools.product(
            (2, 3, 5, 8, 12, 20, 30), (10, 21, 34, 50, 89, 144), (0, 1)):
        if f_ < s:
            add("tendencia", f"ema{f_}x{s}x{x}", (f_, s, x), _mk_ema(f_, s, x))
    for p, mode in itertools.product((9, 14, 20, 50, 100), ("cross", "side")):
        add("tendencia", f"sma{p}{mode}", (p, mode),
            (lambda pp, mm: (lambda c: (
                _r("call" if ind.sma([x["close"] for x in c], pp)
                    and c[-1]["close"] > ind.sma([x["close"] for x in c], pp)
                    else "put", 52) if mm == "side" else
                (_r("call", 55) if ind.sma([x["close"] for x in c], pp)
                    and c[-1]["close"] > ind.sma([x["close"] for x in c], pp)
                    and c[-2]["close"] <= ind.sma([x["close"] for x in c[:-1]], pp)
                 else _r("put", 55) if ind.sma([x["close"] for x in c], pp)
                    and c[-1]["close"] < ind.sma([x["close"] for x in c], pp)
                    and c[-2]["close"] >= ind.sma([x["close"] for x in c[:-1]], pp)
                 else None))))(p, mode))
    for f_, s, g in itertools.product((5, 8, 12, 16), (21, 26, 35, 50), (5, 9, 14)):
        if f_ < s:
            add("osciladores", f"macd{f_}s{s}g{g}", (f_, s, g), _mk_macd(f_, s, g))
    for p, m, md in itertools.product((10, 14, 20, 28, 40), (1.5, 2.0, 2.5, 3.0), ("rev", "brk")):
        add("bandas", f"boll{p}m{m}{md}", (p, m, md), _mk_boll(p, m, md))
    for n, mp, fd in itertools.product((1, 2, 3, 5, 8, 13, 21), (0, 0.02, 0.05, 0.1, 0.2), (0, 1)):
        add("momentum", f"mom{n}p{mp}f{fd}", (n, mp, fd), _mk_mom(n, mp, fd))
    for lb, bf in itertools.product((5, 10, 15, 20, 30, 50, 80), (0, 0.0002, 0.0005, 0.001)):
        add("rompimento", f"brk{lb}b{bf}", (lb, bf), _mk_breakout(lb, bf))
    for k, d, ob, os_ in itertools.product(
            (5, 9, 14, 21), (3, 5), (70, 80), (20, 30)):
        add("osciladores", f"stoch{k}d{d}ob{ob}os{os_}", (k, d, ob, os_), _mk_stoch(k, d, ob, os_))
    for n, fd in itertools.product((2, 3, 4, 5, 6, 8), (0, 1)):
        add("microestrutura", f"streak{n}f{fd}", (n, fd), _mk_streak(n, fd))
    for tol, md in itertools.product((0.05, 0.1, 0.2, 0.3), ("rev", "cont")):
        add("velas", f"doji{tol}{md}", (tol, md), _mk_doji(tol, md))
    for nb, mr, fd in itertools.product((3, 5, 10), (0.5, 0.8, 1.2), (0, 1)):
        add("velas", f"body{nb}r{mr}f{fd}", (nb, mr, fd), _mk_body(nb, mr, fd))
    for p, nd in itertools.product((7, 14, 21), (2, 3)):
        add("confluencia", f"confl{p}n{nd}", (p, nd), _mk_confluence(p, nd))
    for p, th, dm in itertools.product((10, 14, 21), (15, 20, 25, 30), (1,)):
        add("tendencia", f"adx{p}t{th}d{dm}", (p, th, dm), _mk_adx(p, th, dm))
    for n, t, fd in itertools.product((10, 20, 40, 80), (0.1, 0.2, 0.3), (1, 0)):
        add("reversao", f"range{n}t{t}f{fd}", (n, t, fd), _mk_range(n, t, fd))
    for n1, n2, md in itertools.product((3, 5, 10), (10, 20, 40), ("accel", "decel")):
        add("velocidade", f"acc{n1}v{n2}{md}", (n1, n2, md), _mk_accel(n1, n2, md))
    for p, hi, lo_, md in itertools.product((7, 14, 21), (1.2, 1.5, 2.0), (0.5, 0.7), ("hi", "lo")):
        add("volatilidade", f"atr{p}h{hi}l{lo_}{md}", (p, hi, lo_, md), _mk_atr(p, hi, lo_, md))

    for v in V:
        v["version"] = 1
        v["markets"] = ["synthetic", "forex"]
        v["state"] = "candidate"
    return V


VARIANTS = generate()
BY_ID = {v["id"]: v for v in VARIANTS}


def evaluate(v, candles):
    try:
        return v["fn"](candles[-160:])
    except Exception:
        return None


def rank(v, candles, horizon=1, max_eval=400):
    """Acertividade da variante: sinal → acertou se a vela `horizon` à frente
    fechou na direção. Devolve (n, wins, wr)."""
    wins = n = 0
    lo = max(30, len(candles) - max_eval)
    for i in range(lo, len(candles) - horizon):
        r = evaluate(v, candles[:i + 1])
        if not r or not r["signal"]:
            continue
        n += 1
        fwd = candles[i + horizon]["close"] - candles[i]["close"]
        if (r["signal"] == "call" and fwd > 0) or (r["signal"] == "put" and fwd < 0):
            wins += 1
    return n, wins, (wins / n if n else 0.0)
