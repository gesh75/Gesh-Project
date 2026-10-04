"""Static reference data for the free Portfolio X-Ray (no network, no live quotes).

Sectors follow common GICS-style groupings for well-known US tickers. This is a
convenience map, not a data feed: any ticker not listed is shown as "Unclassified"
rather than guessed.
"""

from __future__ import annotations

_GROUPS: dict[str, tuple[str, ...]] = {
    "Technology": (
        "AAPL MSFT NVDA AVGO ORCL CRM ADBE AMD INTC CSCO QCOM TXN IBM NOW INTU AMAT MU LRCX "
        "KLAC PANW SNPS CDNS ANET PLTR SHOP SNOW NET CRWD DDOG MDB TSM ASML ARM SMCI DELL HPQ "
        "XLK VGT SMH SOXX"
    ),
    "Communication Services": (
        "GOOGL GOOG META NFLX DIS CMCSA T VZ TMUS SPOT RBLX EA TTWO PINS SNAP XLC"
    ),
    "Consumer Discretionary": (
        "AMZN TSLA HD MCD NKE SBUX LOW BKNG TJX ABNB UBER LULU F GM CMG ROST ORLY MAR XLY"
    ),
    "Consumer Staples": "WMT COST PG KO PEP PM MO CL MDLZ KHC GIS KR TGT XLP",
    "Health Care": (
        "UNH JNJ LLY ABBV MRK PFE TMO ABT DHR AMGN ISRG GILD MDT BMY CVS MRNA VRTX REGN XLV"
    ),
    "Financials": (
        "JPM BAC WFC GS MS C SCHW BLK AXP V MA PYPL COF USB PNC BRK.B BRK-B HOOD COIN SOFI XLF"
    ),
    "Energy": "XOM CVX COP SLB EOG OXY PSX MPC VLO KMI WMB XLE",
    "Industrials": "CAT DE BA HON GE RTX LMT UPS UNP FDX MMM ETN EMR XLI",
    "Utilities": "NEE DUK SO D AEP SRE XLU",
    "Real Estate": "PLD AMT EQIX O SPG PSA WELL XLRE VNQ",
    "Materials": "LIN SHW FCX NEM APD ECL DOW NUE XLB",
    "Diversified funds": (
        "SPY VOO IVV SPLG VTI ITOT SCHB QQQ QQQM DIA IWM VT VXUS EFA EEM AGG BND TLT GLD SLV "
        "SCHD VIG VYM"
    ),
}

SECTORS: dict[str, str] = {t: sector for sector, ts in _GROUPS.items() for t in ts.split()}

# Funds that track the same index hold (almost) the same stocks.
OVERLAP: dict[str, object] = {
    "groups": {
        "S&P 500": ["SPY", "VOO", "IVV", "SPLG"],
        "total US market": ["VTI", "ITOT", "SCHB"],
        "Nasdaq-100": ["QQQ", "QQQM"],
    },
    # Index funds in which the mega-cap names below are large constituents.
    "broad": ["S&P 500", "total US market", "Nasdaq-100"],
    "megacaps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META"],
}
