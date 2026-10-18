"""Análise de ticks — especialização em gráficos de 5 ticks (missão §2).

Analisa janelas de ticks reais: direção, magnitude, sequências,
aceleração, falsos rompimentos, mudança de regime, incerteza.

Não assume que um movimento anterior determina o próximo — reporta
medidas, não "padrões garantidos".
"""

import math
from collections import Counter

WINDOWS = (5, 10, 20, 50, 100, 250, 500, 1000)


def _rets(ticks):
    q = [t["quote"] for t in ticks]
    return [q[i] - q[i - 1] for i in range(1, len(q))]


def window_stats(ticks, n):
    """Estatísticas dos últimos `n` ticks (lista [{quote,epoch}...])."""
    seg = ticks[-(n + 1):]
    if len(seg) < 3:
        return {"n": 0, "available": False}
    r = _rets(seg)
    up = sum(1 for x in r if x > 0)
    dn = sum(1 for x in r if x < 0)
    tot = up + dn or 1
    # maior sequência de sinais iguais
    best = cur = 1
    for i in range(1, len(r)):
        s1 = r[i] > 0
        s0 = r[i - 1] > 0
        cur = cur + 1 if s1 == s0 else 1
        best = max(best, cur)
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / len(r)
    sd = math.sqrt(var)
    # aceleração: segunda metade vs primeira
    h = len(r) // 2
    accel = (sum(r[h:]) / max(1, len(r) - h)) - (sum(r[:h]) / max(1, h))
    p_up = up / tot
    # incerteza: erro-padrão da proporção
    se = math.sqrt(p_up * (1 - p_up) / tot)
    return {
        "n": len(r), "available": True,
        "up": up, "down": dn, "flat": len(r) - up - dn,
        "p_up": round(p_up, 3), "se": round(se, 3),
        "net": round(sum(r), 8), "sd": round(sd, 8),
        "max_streak": best, "accel": round(accel, 8),
        "last_px": seg[-1]["quote"],
    }


def regime_change(ticks):
    """Compara janelas curta (50) vs longa (250): mudança de regime se a
    distribuição de retornos mudou significativamente."""
    if len(ticks) < 300:
        return {"detected": False, "n": len(ticks)}
    a = _rets(ticks[-51:])
    b = _rets(ticks[-301:-51])
    pu = sum(1 for x in a if x > 0) / max(1, len(a))
    pb = sum(1 for x in b if x > 0) / max(1, len(b))
    va = sum(x * x for x in a) / len(a)
    vb = sum(x * x for x in b) / len(b)
    return {"detected": abs(pu - pb) > 0.18 or (vb and va / vb > 2.5),
            "p_up_short": round(pu, 3), "p_up_long": round(pb, 3),
            "vol_ratio": round(va / vb, 2) if vb else None}


def false_breakout(ticks, look=60):
    """Falso rompimento: preço rompeu o extremo recente e voltou."""
    if len(ticks) < look + 5:
        return {"up": False, "down": False}
    hist = ticks[-look - 5:-5]
    hh = max(t["quote"] for t in hist)
    ll = min(t["quote"] for t in hist)
    last = [t["quote"] for t in ticks[-5:]]
    return {"up": max(last) > hh and last[-1] < hh,
            "down": min(last) < ll and last[-1] > ll}


def analyze(ticks):
    """Resumo completo para o painel."""
    ticks = list(ticks or [])
    out = {"n_ticks": len(ticks), "windows": {}}
    for w in WINDOWS:
        if len(ticks) >= w:
            out["windows"][str(w)] = window_stats(ticks, w)
    out["regime"] = regime_change(ticks)
    out["false_break"] = false_breakout(ticks)
    return out
