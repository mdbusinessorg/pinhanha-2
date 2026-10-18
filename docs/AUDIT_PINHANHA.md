# Auditoria — PINHANHA original (quotex-bot) → PINHANHA 2.0

## O que foi portado 1:1 (motor intacto)

| Ficheiro | Conteúdo |
|---|---|
| `app/strategies.py` | Todas as estratégias + `STRATEGIES` dict (15 votantes + restantes) |
| `app/indicators.py` | EMA, RSI, MACD, ATR, BB, Stochastic, ADX, regime helpers |
| `app/patterns.py` | ~40 padrões de velas (engulfing, pinbar, doji, …) |
| `app/calib.py` | Calibração: BREAKEVEN=0.535, PRIOR=0.50, shrinkage, `grade_set`, `no_trade_exact` |
| `app/regime.py`, `app/analytics.py`, `app/knowledge.py` | Regime de mercado, estatísticas, knowledge |
| `app/ai.py` | Insight Groq (`openai/gpt-oss-120b`, env `GROQ_API_KEY`) |

O pipeline de scan em `app/engine.py` replica o original: votantes ponderados
por `strat_weight` (acertividade medida), consenso ≥3, `calib.estimate`,
`watch` (OBSERVAÇÃO abaixo do corte), tracking WIN/LOSS a 60 s, restore do
histórico no arranque.

## Substituído (adaptador de corretora)

| Original (Quotex) | PINHANHA 2.0 (Deriv) |
|---|---|
| `broker.py` pyquotex + SSID | `deriv_feed.py` — WS público Deriv |
| Yahoo Finance fallback | `ticks_history` (velas oficiais Deriv) |
| Pares `_otc` Quotex | Sintéticos `R_*`/`1HZ*`/`stpRNG` + cripto 24/7 |
| Execução de ordens | **Removida** — sinais apenas |

## Incompatibilidades documentadas

- Forex Deriv (`frx*`) fecha ao fim de semana — velas param na sexta.
- Sintéticos Deriv ≠ OTC Quotex — preços não equivalentes; acertividade é
  medida de raiz nesta instalação (histórico importado marcado `source=import`,
  não alimenta a calibração live).
- `ws.derivws.com` pode ser bloqueado por algumas redes — o feed roda
  hosts (derivws → binaryws → red/blue/green) e cai em polling de velas
  (14 s) se `subscribe` falhar. Estado visível em `/api/deriv/status`.
- DERIV_APP_ID=1089 é o app_id público de demonstração — registar um próprio
  em api.deriv.com para produção.

## O que NÃO foi alterado

- Repositório `mdbusinessorg/quotex-bot` — nenhum ficheiro tocado.
- Serviços Render `pinhanhabot`, `quotex-bot-eu`, `quotex-bot2` — intactos.
- Projecto Supabase "A MÁQUINA" (`pinhanha_signals`) — só leitura (import).
