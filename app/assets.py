"""Catálogo de ativos Deriv — mapeamento nome-painel ↔ símbolo Deriv.

Substitui os pares Quotex: forex `frx*`, cripto `cry*`, índices sintéticos
Volatility/R_*/1HZ*/stpRNG (24/7 — o equivalente aos pares OTC da Quotex:
mercado sempre aberto, incluindo fins de semana).

`cls` alimenta a calibração por classe (forex / crypto / synthetic) —
classes distintas calibram em separado (calib.build usa r["cls"]).
"""

# nome-painel -> (símbolo Deriv, classe, nome legível, nota)
ASSETS = {
    # ---- Sintéticos 24/7 (o "OTC" da Deriv — sempre aberto) ----
    "V10":      ("R_10",     "synthetic", "Volatility 10 Index",      "24/7"),
    "V25":      ("R_25",     "synthetic", "Volatility 25 Index",      "24/7"),
    "V50":      ("R_50",     "synthetic", "Volatility 50 Index",      "24/7"),
    "V75":      ("R_75",     "synthetic", "Volatility 75 Index",      "24/7"),
    "V100":     ("R_100",    "synthetic", "Volatility 100 Index",     "24/7"),
    "V10(1s)":  ("1HZ10V",   "synthetic", "Volatility 10 (1s) Index", "24/7"),
    "V25(1s)":  ("1HZ25V",   "synthetic", "Volatility 25 (1s) Index", "24/7"),
    "V50(1s)":  ("1HZ50V",   "synthetic", "Volatility 50 (1s) Index", "24/7"),
    "V75(1s)":  ("1HZ75V",   "synthetic", "Volatility 75 (1s) Index", "24/7"),
    "V100(1s)": ("1HZ100V",  "synthetic", "Volatility 100 (1s) Index","24/7"),
    "STEP":     ("stpRNG",   "synthetic", "Step Index",               "24/7"),
    # ---- Cripto 24/7 ----
    "BTCUSD":   ("cryBTCUSD", "crypto", "Bitcoin",       "24/7"),
    "ETHUSD":   ("cryETHUSD", "crypto", "Ethereum",      "24/7"),
    "LTCUSD":   ("cryLTCUSD", "crypto", "Litecoin",      "24/7"),
    "SOLUSD":   ("crySOLUSD", "crypto", "Solana",        "24/7"),
    "XRPUSD":   ("cryXRPUSD", "crypto", "XRP",           "24/7"),
    # ---- Forex (fecha ao fim de semana) ----
    "EURUSD":   ("frxEURUSD", "forex", "Euro / Dólar",        "seg-sex"),
    "GBPUSD":   ("frxGBPUSD", "forex", "Libra / Dólar",       "seg-sex"),
    "USDJPY":   ("frxUSDJPY", "forex", "Dólar / Iene",        "seg-sex"),
    "AUDUSD":   ("frxAUDUSD", "forex", "Dólar Australiano",   "seg-sex"),
    "USDCAD":   ("frxUSDCAD", "forex", "Dólar / Dólar Canad.", "seg-sex"),
    "USDCHF":   ("frxUSDCHF", "forex", "Dólar / Franco",      "seg-sex"),
    "EURGBP":   ("frxEURGBP", "forex", "Euro / Libra",        "seg-sex"),
    "EURJPY":   ("frxEURJPY", "forex", "Euro / Iene",         "seg-sex"),
    "GBPJPY":   ("frxGBPJPY", "forex", "Libra / Iene",        "seg-sex"),
    "NZDUSD":   ("frxNZDUSD", "forex", "Dólar N.Z. / Dólar",  "seg-sex"),
    "AUDCAD":   ("frxAUDCAD", "forex", "AUD / CAD",           "seg-sex"),
    "AUDCHF":   ("frxAUDCHF", "forex", "AUD / CHF",           "seg-sex"),
    "XAUUSD":   ("frxXAUUSD", "forex", "Ouro / Dólar",        "seg-sex"),
}

# ativos na varredura de sinais — sintéticos (o "OTC" da Deriv, 24/7)
# + forex. Cripto fora (acertividade medida fraca ~50%).
SCAN_ASSETS = [a for a, (_, c, _, _) in ASSETS.items() if c != "crypto"]


def deriv_symbol(asset):
    a = ASSETS.get(asset)
    return a[0] if a else None


def asset_cls(asset):
    a = ASSETS.get(asset)
    return a[1] if a else "forex"


def asset_label(asset):
    a = ASSETS.get(asset)
    return a[2] if a else asset


def is_247(asset):
    return asset_cls(asset) in ("synthetic", "crypto")
