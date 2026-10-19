"""Knowledge Base — metadados e resumos próprios de fontes legítimas.
Não copia conteúdo protegido; guarda referências e resumos."""

KNOWLEDGE = [
    {
        "category": "Technical Analysis",
        "items": [
            {"title": "Technical Analysis of the Financial Markets", "author": "John J. Murphy",
             "summary": "Referência base de AT: tendências, médias, padrões gráficos e indicadores. Conceito-chave: 'the trend is your friend'.",
             "source": "Livro (1999), Prentice Hall"},
            {"title": "New Concepts in Technical Trading Systems", "author": "J. Welles Wilder",
             "summary": "Origem do RSI, ADX, ATR e Parabolic SAR. Fórmulas e interpretação originais usadas neste projeto.",
             "source": "Livro (1978), Trend Research"},
        ],
    },
    {
        "category": "Price Action",
        "items": [
            {"title": "Japanese Candlestick Charting Techniques", "author": "Steve Nison",
             "summary": "Padrões de velas: engolfo, martelo, estrela, doji, três soldados/corvos. Base das estratégias de price action.",
             "source": "Livro (1991), NYIF"},
            {"title": "Encyclopedia of Chart Patterns", "author": "Thomas Bulkowski",
             "summary": "Catálogo estatístico de padrões gráficos com taxas de sucesso medidas.",
             "source": "Livro (2000), Wiley"},
        ],
    },
    {
        "category": "Risk Management",
        "items": [
            {"title": "Trade Your Way to Financial Freedom", "author": "Van K. Tharp",
             "summary": "Position sizing, R-multiples e expectativa. Base do Risk Manager: limites diários e exposição.",
             "source": "Livro (1998), McGraw-Hill"},
        ],
    },
    {
        "category": "Trading Psychology",
        "items": [
            {"title": "Trading in the Zone", "author": "Mark Douglas",
             "summary": "Pensamento probabilístico: cada trade é um evento aleatório numa série — nenhuma estratégia garante lucro.",
             "source": "Livro (2000), Prentice Hall"},
        ],
    },
    {
        "category": "Market Structure",
        "items": [
            {"title": "Support & Resistance", "author": "Conceito clássico",
             "summary": "Níveis onde a pressão compradora/vendedora historicamente inverteu o preço; usados na estratégia S&R.",
             "source": "Domínio público / literatura de AT"},
        ],
    },
    {
        "category": "Indicators",
        "items": [
            {"title": "Bollinger on Bollinger Bands", "author": "John Bollinger",
             "summary": "Bandas de desvio padrão: reversão à média e squeezes de volatilidade.",
             "source": "Livro (2001), McGraw-Hill"},
            {"title": "Stochastic Oscillator", "author": "George Lane",
             "summary": "%K/%D medem a posição do fecho dentro do range recente; extremos marcam reversões.",
             "source": "Conferências de Lane (anos 50-60), amplamente documentado"},
        ],
    },
    {
        "category": "Probability",
        "items": [
            {"title": "Expectativa matemática em trading", "author": "Conceito estatístico",
             "summary": "EV = win_rate × ganho médio − loss_rate × perda média. Com payout <100%, a win rate necessária > 50%.",
             "source": "Domínio público"},
        ],
    },
    {
        "category": "Deep Search — Sintéticos & Forex 1m (2026-10-18)",
        "items": [
            {"title": "V75 Scalping Strategy (synthetics.info)",
             "summary": "Confluência obrigatória: Stoch(14)≥80/≤20 + RSI(14) 70/30 + histograma MACD em pico/fundo + toque na Bollinger + rejeição da vela. Implementado como votante 'v75_confluence'.",
             "source": "https://synthetics.info/v75-scalping-trading-strategy/"},
            {"title": "Tick vs Candle duration (derivsharks)",
             "summary": "1-5 ticks = ruído puro; 5-20 ticks = micro-momentum legível (sweet spot de bots, PF 1.4-1.8 em testes). O nosso contrato 60s ≈ 30 ticks em V* — está na banda certa.",
             "source": "https://www.derivsharks.com/blog/deriv-tick-vs-candle-trading"},
            {"title": "Mean reversion nos Volatility Indices (Deriv Academy)",
             "summary": "O algoritmo dos sintéticos equilibra a vol realizada para o alvo do índice: vol comprimida → expansão (breakout), vol em pico → compressão (fade nos extremos). Votante 'vol_regime'.",
             "source": "https://traders-academy.deriv.com/lessons/understanding-mean-reversion"},
            {"title": "TriO 60s (bingood)",
             "summary": "Williams %R(-20/-80) + RSI(7) 30/70 + CCI ±100: entrada quando os três cruzam nível na mesma direcção; declarado ~70%. Votante 'trio_osc'.",
             "source": "https://bingood.ru/strategii/strategiya-binarnyh-optsionov-na-60-sekund.html"},
            {"title": "FX Prime M1 (CCI dual)",
             "summary": "CCI170 e CCI34 a cruzar zero na mesma direcção + RSI fora da zona perigosa 45-55. Votante 'cci_dual'.",
             "source": "https://forexmt4indicators.com/fx-prime-forex-scalping-strategy/"},
            {"title": "Impulso de ticks (MQL5)",
             "summary": "Comparar Δ médio dos últimos 5 com os 10 anteriores; se a velocidade fresca excede o limiar, é impulso — entra na direcção. Votante 'tick_impulse'.",
             "source": "https://www.mql5.com/en/forum/397418"},
            {"title": "Pullback EMA20 (scalping clássico)",
             "summary": "Tendência EMA20>EMA50 + retração toca a EMA20 + vela retoma com rejeição ≥60% do range. Votante 'ema_pullback'.",
             "source": "https://pineify.app/resources/blog/the-ultimate-1-minute-scalping-strategy-tradingview-guide-for-fast-paced-traders"},
            {"title": "Digit Over/Under barriers (dtide)",
             "summary": "Payout escala inversamente à probabilidade teórica; edge aparece quando a distribuição recente de dígitos desvia do esperado (histograma rolante).",
             "source": "https://dtide.co.ke/articles/over-under-deriv-strategy"},
        ],
    },
    {
        "category": "Backtesting",
        "items": [
            {"title": "Evidence-Based Technical Analysis", "author": "David Aronson",
             "summary": "Metodologia rigorosa de backtest: evitar data-snooping, overfitting e viés de sobrevivência.",
             "source": "Livro (2006), Wiley"},
        ],
    },
    {
        "category": "Portfolio/Risk Control",
        "items": [
            {"title": "Against the Gods: The Remarkable Story of Risk", "author": "Peter L. Bernstein",
             "summary": "História e fundamentos da gestão de risco aplicável a qualquer mercado.",
             "source": "Livro (1996), Wiley"},
        ],
    },
]
