"""Persistência de trades reais e config sensível — PINHANHA 2.0.

Tabelas (projecto Supabase separado do PINHANHA 1):
  pinhanha2_trades — uma linha por oportunidade/contrato, com estados
                     explícitos (spec §6).
  pinhanha2_config — chave/valor: token API Deriv, conta seleccionada,
                     limites de risco, estado do autotrade.

O token Deriv NUNCA sai do backend: não é devolvido por nenhum endpoint,
não aparece em logs nem no frontend. Best-effort como o sigstore.
"""

import asyncio
import json
import logging
import os
import urllib.request
import urllib.error

log = logging.getLogger("tradestore")
URL = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TRADES = "pinhanha2_trades"
CONFIG = "pinhanha2_config"


def enabled():
    return bool(URL and KEY)


def _req(method, path, body=None, prefer=None):
    hdr = {"apikey": KEY, "Authorization": f"Bearer {KEY}",
           "Content-Type": "application/json"}
    if prefer:
        hdr["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data,
                                 headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=12) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


async def _run(fn, *a):
    if not enabled():
        return None
    try:
        return await asyncio.to_thread(fn, *a)
    except Exception as e:
        log.warning("tradestore: %s", e)
        return None


# ---------------------------------------------------------------- trades

async def upsert_trade(t):
    """Cria/actualiza uma linha de trade (idempotente por id)."""
    await _run(_req, "POST", f"{TRADES}?on_conflict=id", [t],
               "resolution=merge-duplicates,return=minimal")


async def list_trades(limit=200, state=None):
    q = f"{TRADES}?select=*&order=emit_ts.desc&limit={limit}"
    if state:
        q += f"&state=eq.{state}"
    return await _run(_req, "GET", q) or []


async def open_trades():
    return await _run(_req, "GET",
                      f"{TRADES}?select=*&state=in.(buy_sent,confirmed,open)")


async def pending_reconcile():
    """Estados que sobrevivem a restarts e precisam de reconciliação."""
    return await _run(_req, "GET",
                      f"{TRADES}?select=*&state=in.(validated,proposal,buy_sent,"
                      f"confirmed,open)")


async def day_stats(ts_start):
    rows = await _run(
        _req, "GET",
        f"{TRADES}?select=result,profit,stake,state&emit_ts=gte.{ts_start}")
    return rows or []


# ---------------------------------------------------------------- config

async def get_config(key, default=None):
    rows = await _run(_req, "GET", f"{CONFIG}?key=eq.{key}&select=value")
    if rows:
        return rows[0].get("value")
    return default


async def set_config(key, value):
    await _run(_req, "POST", f"{CONFIG}?on_conflict=key",
               [{"key": key, "value": value}],
               "resolution=merge-duplicates,return=minimal")


async def del_config(key):
    await _run(_req, "DELETE", f"{CONFIG}?key=eq.{key}")
