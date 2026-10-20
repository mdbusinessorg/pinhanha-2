"""Fallback de feed via Supabase edge fn `deriv-relay`.

A edge da Deriv devolve 520 a vários IPs (GitHub Actions incluído); o
egresso do Supabase liga. Quando o WS directo está morto o worker chama
o relay, que devolve velas 1m reais e injecta-as na cache do feed —
o resto do pipeline (scan, sinais, painel) funciona igual.
"""

import asyncio
import json
import logging
import os
import time
import urllib.request

from .assets import deriv_symbol

log = logging.getLogger("relay")

_URL = os.getenv("SUPABASE_URL", "").rstrip("/") + "/functions/v1/deriv-relay"
_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


async def pull(feed, timeout=45):
    """Devolve (n_símbolos_injectados, erro). Actualiza feed.candles/ticks."""
    if not _KEY:
        return 0, "sem SUPABASE_SERVICE_KEY"

    def _call():
        req = urllib.request.Request(
            _URL, method="POST",
            headers={"Authorization": f"Bearer {_KEY}",
                     "Content-Type": "application/json"},
            data=b"{}")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    try:
        d = await asyncio.to_thread(_call)
    except Exception as e:
        return 0, str(e)[:120]
    if not d.get("ok"):
        return 0, str(d.get("error"))[:160]

    now = time.time()
    n = 0
    for asset, cds in (d.get("candles") or {}).items():
        sym = deriv_symbol(asset)
        if not sym or not cds:
            continue
        try:
            feed._merge_candles(sym, cds)
            st = feed.state.setdefault(sym, {})
            st["mode"] = "poll"
            st["last_msg"] = now
            st["n"] = len(feed.candles.get(sym) or [])
            st["err"] = None
            n += 1
        except Exception:
            continue
    for asset, p in (d.get("prices") or {}).items():
        sym = deriv_symbol(asset)
        if not sym or not p:
            continue
        try:
            feed._on_deriv_tick(
                {"symbol": sym, "epoch": now, "quote": float(p)})
        except Exception:
            continue

    if n:
        # estado honesto: dados a chegar via relay, não via WS directo
        feed.connected = True
        feed.mode = "relay"
        feed.host = d.get("host") or "supabase-relay"
        feed.last_error = None
        feed.last_msg_ts = now
    return n, None
