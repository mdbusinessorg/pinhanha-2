"""Módulo de sequências (missão §6) — investiga sequências de acertos.

Regista maior sequência de vitórias/derrotas, frequência, probabilidade
estimada de uma sequência de 20 acertos, com intervalos de confiança.

Não fabrica sequências — mede o que há.
"""

import math


def sequences(results):
    """results: lista cronológica de 'win'/'loss'."""
    best_w = best_l = cur_w = cur_l = 0
    freq_w = freq_l = 0
    for r in results:
        if r == "win":
            cur_w += 1
            if cur_l:
                freq_l += 1
            cur_l = 0
        elif r == "loss":
            cur_l += 1
            if cur_w:
                freq_w += 1
            cur_w = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return {"max_win_streak": best_w, "max_loss_streak": best_l,
            "win_runs": freq_w + (1 if cur_w else 0),
            "loss_runs": freq_l + (1 if cur_l else 0),
            "current": {"dir": "win" if cur_w else "loss",
                        "n": max(cur_w, cur_l)}}


def p_streak(wr, n_trials, k=20):
    """P(pelo menos uma sequência de k vitórias em n_trials) — aproximação
    por cadeia de Markov simplificada: soma de janelas."""
    if wr <= 0 or n_trials < k:
        return 0.0
    return round(min(1.0, (n_trials - k + 1) * wr ** k), 6)


def report(rows):
    """rows: registos pinhanha2_signals com 'result' e 'cls'."""
    done = [r for r in rows if r.get("result") in ("win", "loss")]
    done.sort(key=lambda r: r.get("eval_ts") or r.get("emit_ts") or 0)
    res = [r["result"] for r in done]
    n = len(res)
    w = res.count("win")
    wr = w / n if n else 0
    se = math.sqrt(wr * (1 - wr) / n) if n else 0
    by_cls = {}
    for r in done:
        c = by_cls.setdefault(r.get("cls") or "?", [])
        c.append(r["result"])
    per = {}
    for c, rr in by_cls.items():
        nw = rr.count("win")
        per[c] = {"n": len(rr), "wr": round(nw / len(rr), 3),
                  **sequences(rr)}
    return {
        "n": n, "wins": w, "wr": round(wr, 3),
        "wr_ci95": [round(max(0, wr - 1.96 * se), 3),
                    round(min(1, wr + 1.96 * se), 3)],
        **sequences(res),
        "p20_given_wr": p_streak(wr, n, 20),
        "per_class": per,
        "note": "P(20 seguidas) usa a acertividade medida; backtest ≠ futuro.",
    }
