# PINHANHA 2.0 — Plataforma de Sinais Deriv

Plataforma independente de análise e sinais, alimentada pela API pública da
Deriv (WebSocket). O motor de estratégias é uma cópia directa do PINHANHA
original (15 votantes + calibração por acertividade medida + Groq) — ver
`docs/AUDIT_PINHANHA.md`.

**Execução automática opcional**: a fase de sinais funciona sem conta. Para
executar na tua conta Deriv, liga um API token (app.deriv.com → Settings →
API token, scope **Trade**) na página **Execução**, escolhe DEMO ou REAL e
activa — o RiskManager verifica os limites antes de cada compra e o botão de
emergência bloqueia novas entradas de imediato. Sem martingale, stake fixa,
idempotente por sinal. REAL exige confirmação explícita.

## Arranque

```bash
pip install -r requirements.txt
BOT_PASSWORD=a-tua-password python -m app.main    # http://localhost:8000
```

Login do painel: `BOT_PASSWORD` (defeito `devin`).

## Arquitetura

| Módulo | Função |
|---|---|
| `app/deriv_feed.py` | Ligação Deriv: rotação de hosts WS, `ticks_history` (bootstrap + polling), stream de ticks, reconexão/backoff, ping |
| `app/assets.py` | Catálogo: forex `frx*`, cripto `cry*`, Volatility `R_*`/`1HZ*`/`stpRNG` 24/7 |
| `app/engine.py` | Pipeline de sinais (VOTERS, `strat_weight`, tracking WIN/LOSS a 60s, calibração) + Start/Pause/Stop |
| `app/strategies.py` | Motor de estratégias (cópia 1:1 do original) |
| `app/indicators.py`, `patterns.py`, `regime.py`, `analytics.py`, `calib.py` | Inteligência portada sem alterações |
| `app/ai.py` | Insight via Groq (env `GROQ_API_KEY`) |
| `app/sigstore.py` | Histórico em Supabase `pinhanha2_signals` (independente; `source=live|import`) |
| `app/backtest.py` | Backtest sobre velas reais da Deriv |
| `static/` | Painel SPA: Dashboard / Estratégias / Histórico / Análise / Definições |

## Diferenças Quotex ↔ Deriv documentadas

- Pares `_otc` da Quotex ↔ índices sintéticos `R_*`/`1HZ*`/`stpRNG` (24/7).
- Forex Deriv (`frx*`) fecha ao fim de semana — os sinais nesses ativos
  ficam parados até reabrir (marcado na UI).
- Classe `synthetic` nova na calibração (antes só forex/cripto).
- Não assumimos equivalência de preço entre OTC Quotex e Volatility Deriv —
  a acertividade é medida de raiz nesta instalação; o histórico importado
  do PINHANHA 1 fica marcado `source=import` e não alimenta a calibração.

## Env vars

`BOT_PASSWORD` · `DERIV_APP_ID` (defeito 1089 público — regista o teu em
api.deriv.com) · `GROQ_API_KEY` · `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` ·
`SIG_TABLE` (defeito `pinhanha2_signals`) · `PORT`

## API

`POST /api/auth/login` · `GET /api/status` · `GET /api/deriv/status` ·
`POST /api/engine/start|pause|resume|stop` · `GET /api/signals` ·
`GET /api/sigstats` · `GET /api/sigdiag` · `GET /api/assets` ·
`GET /api/candles/{a}` · `GET /api/patterns/{a}` · `GET /api/analysis/{a}` ·
`GET /api/ai/insight/{a}` · `GET /api/digits/{a}` · `GET /api/strategies` ·
`POST /api/backtest` · `GET /api/history/signals` · `GET /healthz`

## Testes

```bash
pytest tests/
```

> Plataforma de análise. Nenhuma estratégia garante lucro; a taxa de acerto
> apresentada é medida ao vivo (saída 60s após o sinal) e não é promessa.
