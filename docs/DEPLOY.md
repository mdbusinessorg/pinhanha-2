# Deploy — PINHANHA 2.0

## Render

- Serviço: `srv-dba8barrjlhs739a3fug` (web_service, free, Frankfurt)
- URL: https://pinhanha-2.onrender.com
- Repo: https://github.com/mdbusinessorg/pinhanha-2 (branch `main`, autoDeploy)
- Health check: `/healthz`
- Build: `pip install -r requirements.txt` · Start: `python -m app.main`
- Conta Render: `matiasdomingos70@gmail.com` (team `myws`) — a mesma conta
  que hospeda o PINHANHA original; o serviço `pinhanha-2` é separado e não
  partilha estado com `pinhanhabot`.

## Supabase

- Projecto: `mdbusinessorg's Project` (`stqhksrmcysuuigwzaoa`, eu-west-1)
  — separado de "A MÁQUINA" (`noywnuafpxvxvmfkjtbh`) do PINHANHA 1.
- Tabela: `public.pinhanha2_signals` (RLS on; acesso via service_role).
- Histórico: 23 304 sinais importados de `pinhanha_signals` com
  `source='import'` — visíveis no Histórico, excluídos da calibração live.
- Nota: free tier Supabase limita a 2 projectos activos — a BD independente
  foi criada como tabela dedicada noutro projecto existente.

## Execução automática (fase 2)

- `app/execution.py`: `DerivExecWS` (WS autorizado próprio — authorize →
  proposal → buy → proposal_open_contract), `RiskManager`, `AutoTrader`,
  reconciliação via `portfolio`.
- Tabelas: `pinhanha2_trades` (estados candidate→…→settled/rejected),
  `pinhanha2_config` (token Deriv, conta, limites, estado — service_role only;
  o token nunca sai do backend nem aparece em logs).
- Endpoints: `/api/deriv/connect|select|disconnect`,
  `/api/autotrade/status|enable|emergency|limits`, `/api/trades`.
- Auth: API token do utilizador (app.deriv.com → Settings → API token,
  scope Trade). OAuth exige app próprio registado — pendente de o user
  registar um `app_id` em api.deriv.com se quiser OAuth.
- REAL bloqueado por `confirm_real` + conta `is_virtual=false`.

## Env vars configuradas

`BOT_PASSWORD` (aleatória), `GROQ_API_KEY`, `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY`, `SIG_TABLE=pinhanha2_signals`, `DERIV_APP_ID=1089`,
`PYTHON_VERSION=3.12.7`

## Rede Deriv

O feed roda hosts WS (ws.derivws.com → ws.binaryws.com → red → blue → green)
com backoff e cai em polling se `subscribe` falhar. Em Frankfurt o host
primário deve funcionar em modo `stream`.
