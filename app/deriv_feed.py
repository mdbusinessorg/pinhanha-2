"""Adaptador Deriv — WebSocket público (sem token) para candles e ticks.

A API oficial da Deriv corre sobre WebSocket (JSON). Este módulo:

- Roda entre vários hosts oficiais (ws.derivws.com, ws.binaryws.com e
  mirrors blue/green/red) porque algumas redes/IPs são filtradas pela
  Cloudflare — tenta o próximo da lista a cada falha.
- Faz o bootstrap das velas 1m com `ticks_history style=candles`.
- Subscreve ticks por símbolo para actualizar a vela em formação a cada
  ~1-2s; quando a subscrição não é aceite pelo silo (InvalidSymbol), o
  símbolo fica em modo "poll" — refresh periódico de `ticks_history`.
- Reconecta com backoff exponencial e envia ping de keepalive.
- Não executa ordens — PINHANHA 2.0 é uma plataforma de sinais.

Validação: timestamps normalizados para segundos; velas/ticks com preços
não positivos são descartados.
"""

import asyncio
import collections
import json
import logging
import time

log = logging.getLogger("deriv")

WS_HOSTS = [
    # red.derivws.com primeiro — os outros silos dão 520 em algumas edges
    "wss://red.derivws.com/websockets/v3?app_id={app}",
    "wss://ws.derivws.com/websockets/v3?app_id={app}",
    "wss://ws.binaryws.com/websockets/v3?app_id={app}",
    "wss://blue.derivws.com/websockets/v3?app_id={app}",
    "wss://green.derivws.com/websockets/v3?app_id={app}",
]

PING_EVERY = 25          # keepalive
POLL_EVERY = 14          # refresh dos símbolos sem stream
RESYNC_EVERY = 300       # re-sincroniza velas 1m a cada 5 min
BOOTSTRAP_DELAY = 0.25   # espaçamento no bootstrap (rate limit 220/min)


def _to_candle(raw):
    """Normaliza vela Deriv {epoch,open,high,low,close} → formato interno."""
    try:
        t = int(float(raw.get("epoch") or raw.get("time")))
        o, h, l, c = (float(raw[k]) for k in ("open", "high", "low", "close"))
    except (TypeError, ValueError, KeyError):
        return None
    if not (o > 0 and h > 0 and l > 0 and c > 0) or h < l or t <= 0:
        return None
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


class DerivFeed:
    """Gestor da ligação Deriv + cache de velas por símbolo."""

    def __init__(self, symbols, app_id=1089, granularity=60, n_candles=100):
        # symbols: lista de símbolos Deriv (frxEURUSD, R_100, cryBTCUSD…)
        self.symbols = list(symbols)
        self.app_id = app_id
        self.granularity = granularity
        self.n_candles = n_candles
        self.candles = {}            # symbol -> [velas internas]
        self.ticks = collections.defaultdict(
            lambda: collections.deque(maxlen=600))  # symbol -> ticks recentes
        self.pip_size = {}           # symbol -> casas decimais do pip
        self.state = {}              # symbol -> {mode, last_msg, err}
        self.connected = False
        self.host = None
        self.host_idx = 0
        self.last_msg_ts = 0.0
        self.last_error = None
        self.mode = "off"            # off|stream|poll|mixed
        self._ws = None
        self._req_id = 0
        self._pending = {}           # req_id -> Future
        self._subs = {}              # symbol -> subscription_id
        self._stop = asyncio.Event()
        self._on_tick = None         # callback(symbol, price, epoch)
        self._task = None

    # ------------------------------------------------------------ público

    def set_tick_callback(self, fn):
        self._on_tick = fn

    def status(self):
        return {
            "connected": self.connected,
            "host": self.host,
            "mode": self.mode,
            "app_id": self.app_id,
            "symbols": len(self.symbols),
            "streamed": sum(1 for s in self.state.values()
                            if s.get("mode") == "stream"),
            "polled": sum(1 for s in self.state.values()
                          if s.get("mode") == "poll"),
            "last_msg_ts": self.last_msg_ts,
            "last_error": self.last_error,
            "per_symbol": {s: dict(st) for s, st in self.state.items()},
        }

    async def start(self):
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            self._task = None
        await self._close_ws()

    async def candles_for(self, symbol, n=100):
        """Devolve velas da cache (ou pede fetch directo se vazio)."""
        cds = self.candles.get(symbol) or []
        if not cds and self._ws is not None:
            cds = await self._history(symbol, n)
        return cds[-n:]

    # ------------------------------------------------------------ ciclo

    async def _run(self):
        backoff = 5
        while not self._stop.is_set():
            try:
                await self._connect_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("deriv feed: %s", self.last_error)
            self.connected = False
            await self._close_ws()
            try:
                await asyncio.wait_for(self._stop.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(60, backoff * 2)

    async def _connect_loop(self):
        import websockets
        url = WS_HOSTS[self.host_idx % len(WS_HOSTS)].format(app=self.app_id)
        host = url.split("//")[1].split("/")[0]
        log.info("deriv: a ligar a %s (app_id=%s)", host, self.app_id)
        async with websockets.connect(url, open_timeout=15, ping_interval=None,
                                      max_size=2_000_000) as ws:
            self._ws = ws
            self.connected = True
            self.host = host
            self.last_error = None
            self._pending.clear()
            self._subs.clear()
            reader = asyncio.create_task(self._reader(ws))
            try:
                await self._bootstrap()
                await asyncio.gather(
                    self._keepalive(), self._poller(), self._resync())
            finally:
                reader.cancel()
                self._ws = None

    async def _reader(self, ws):
        async for raw in ws:
            self.last_msg_ts = time.time()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg):
        if "error" in msg:
            rid = msg.get("req_id")
            fut = self._pending.pop(rid, None)
            err = (msg["error"] or {}).get("message") or str(msg["error"])
            # subscrição recusada → símbolo passa a poll
            sym = (msg.get("echo_req") or {}).get("ticks") or \
                  (msg.get("echo_req") or {}).get("ticks_history")
            if sym:
                st = self.state.setdefault(sym, {})
                if st.get("mode") != "stream":
                    st["mode"] = "poll"
                st["err"] = err
            if fut and not fut.done():
                fut.set_exception(RuntimeError(err))
            self.last_error = err
            return
        mt = msg.get("msg_type")
        if mt == "tick" and msg.get("tick"):
            self._on_deriv_tick(msg["tick"])
        elif mt == "ohlc" and msg.get("ohlc"):
            self._on_deriv_ohlc(msg["ohlc"])
        rid = msg.get("req_id")
        if rid in self._pending:
            fut = self._pending.pop(rid)
            if not fut.done():
                fut.set_result(msg)

    async def _send(self, payload, timeout=20):
        """Envia pedido com req_id e espera a resposta correlacionada."""
        if self._ws is None:
            raise RuntimeError("ws desligado")
        self._req_id += 1
        rid = self._req_id
        payload["req_id"] = rid
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    # ------------------------------------------------------------ dados

    async def _history(self, symbol, n=None):
        """ticks_history style=candles → velas normalizadas (mais novas últimas)."""
        resp = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": int(n or self.n_candles),
            "end": "latest",
            "granularity": self.granularity,
            "style": "candles",
        })
        if resp.get("pip_size") is not None:
            try:
                self.pip_size[symbol] = int(resp["pip_size"])
            except (TypeError, ValueError):
                pass
        out = []
        for c in resp.get("candles") or []:
            cd = _to_candle(c)
            if cd:
                out.append(cd)
        out.sort(key=lambda c: c["time"])
        return out

    async def _bootstrap(self):
        ok = 0
        for sym in self.symbols:
            if self._stop.is_set():
                return
            st = self.state.setdefault(sym, {})
            try:
                cds = await self._history(sym)
            except Exception as e:
                st["mode"] = "off"
                st["err"] = str(e)[:120]
                await asyncio.sleep(BOOTSTRAP_DELAY)
                continue
            if cds:
                self.candles[sym] = cds[-self.n_candles:]
                st["n"] = len(cds)
                ok += 1
            else:
                st["mode"] = "off"
                st["err"] = "sem dados (mercado fechado?)"
            await asyncio.sleep(BOOTSTRAP_DELAY)
        # stream de ticks nos símbolos com dados
        for sym in self.symbols:
            if self._stop.is_set() or self._ws is None:
                return
            if sym not in self.candles:
                continue
            try:
                resp = await self._send({"ticks": sym, "subscribe": 1})
                sub = resp.get("subscription") or {}
                self._subs[sym] = sub.get("id")
                self.state[sym]["mode"] = "stream"
            except Exception as e:
                self.state.setdefault(sym, {})["mode"] = "poll"
                self.state[sym]["err"] = str(e)[:120]
            await asyncio.sleep(0.05)
        streamed = sum(1 for s in self.state.values() if s.get("mode") == "stream")
        polled = sum(1 for s in self.state.values() if s.get("mode") == "poll")
        self.mode = "stream" if streamed and not polled else \
                    "mixed" if streamed else "poll" if polled else "off"
        log.info("deriv: %d símbolos ok (%d stream / %d poll)", ok, streamed, polled)

    async def _keepalive(self):
        while not self._stop.is_set() and self._ws is not None:
            try:
                await self._send({"ping": 1}, timeout=15)
            except Exception as e:
                raise RuntimeError(f"ping falhou: {e}")
            await asyncio.sleep(PING_EVERY)

    async def _poller(self):
        """Refresh de velas dos símbolos sem stream (fallback)."""
        while not self._stop.is_set() and self._ws is not None:
            await asyncio.sleep(POLL_EVERY)
            poller_syms = [s for s in self.symbols
                           if self.state.get(s, {}).get("mode") == "poll"]
            for sym in poller_syms:
                if self._ws is None:
                    return
                try:
                    cds = await self._history(sym, 4)
                    if cds:
                        self._merge_candles(sym, cds)
                        self.state[sym]["last_msg"] = time.time()
                except Exception as e:
                    self.state[sym]["err"] = str(e)[:120]
                await asyncio.sleep(0.3)

    async def _resync(self):
        """Re-sincroniza todas as velas periodicamente (recupera furos)."""
        while not self._stop.is_set() and self._ws is not None:
            await asyncio.sleep(RESYNC_EVERY)
            for sym in self.symbols:
                if self._ws is None or self._stop.is_set():
                    return
                try:
                    cds = await self._history(sym, 30)
                    if cds:
                        self._merge_candles(sym, cds)
                        if self.state.get(sym, {}).get("mode") == "off":
                            self.state[sym].update({"mode": "poll", "err": None})
                except Exception:
                    pass
                await asyncio.sleep(0.4)

    # ------------------------------------------------------------ merges

    def _merge_candles(self, symbol, new_candles):
        """Junta velas novas/actualizadas à cache do símbolo."""
        cds = self.candles.setdefault(symbol, [])
        by_t = {int(c["time"]): c for c in cds}
        for c in new_candles:
            by_t[int(c["time"])] = c
        merged = [by_t[t] for t in sorted(by_t)]
        self.candles[symbol] = merged[-self.n_candles:]

    def _on_deriv_tick(self, tick):
        """tick = {epoch, quote, symbol, pip_size?, id?} — vela 1m em formação."""
        sym = tick.get("symbol")
        try:
            t = int(float(tick["epoch"]))
            if t > 10_000_000_000:   # ms → s
                t //= 1000
            p = float(tick["quote"])
        except (TypeError, ValueError, KeyError):
            return
        if not sym or p <= 0 or t <= 0:
            return
        if tick.get("pip_size") is not None:
            try:
                self.pip_size[sym] = int(tick["pip_size"])
            except (TypeError, ValueError):
                pass
        self.ticks[sym].append({"epoch": t, "quote": p})
        st = self.state.setdefault(sym, {})
        st["last_msg"] = time.time()
        b = t - t % self.granularity
        cds = self.candles.setdefault(sym, [])
        if cds and int(cds[-1]["time"]) == b:
            c = cds[-1]
            c["close"] = p
            c["high"] = max(c["high"], p)
            c["low"] = min(c["low"], p)
        elif not cds or b > int(cds[-1]["time"]):
            cds.append({"time": b, "open": p, "high": p, "low": p, "close": p})
            del cds[:-self.n_candles]
        if self._on_tick:
            try:
                self._on_tick(sym, p, t)
            except Exception:
                pass

    def _on_deriv_ohlc(self, ohlc):
        """ohlc = {symbol, open_time, open, high, low, close} — vela do stream."""
        sym = ohlc.get("symbol")
        c = _to_candle({"epoch": ohlc.get("open_time") or ohlc.get("epoch"),
                        "open": ohlc.get("open"), "high": ohlc.get("high"),
                        "low": ohlc.get("low"), "close": ohlc.get("close")})
        if not sym or not c:
            return
        self.state.setdefault(sym, {})["last_msg"] = time.time()
        self._merge_candles(sym, [c])
        if self._on_tick:
            try:
                self._on_tick(sym, c["close"], int(time.time()))
            except Exception:
                pass

    async def _close_ws(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self.host_idx += 1
