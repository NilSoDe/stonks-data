"""Ficha ampliada de cada oportunidad (TradingView): valoración, calidad, crecimiento, momento y analistas.

Es información de apoyo: si TradingView falla, las oportunidades salen igual, sin la ficha.
Uso desde signals.py / signals_eu.py:  add_extra(opportunities, "us" | "eu")  ->  añade o["extra"].
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402

URL = "https://scanner.tradingview.com/{market}/scan"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stonks-data)", "Content-Type": "application/json"}

COLS = ["name", "exchange", "close", "currency", "fundamental_currency_code", "sector", "industry",
        "price_earnings_ttm", "price_book_fq", "enterprise_value_ebitda_ttm", "dividends_yield_current",
        "net_margin", "return_on_equity", "debt_to_equity",
        "total_revenue_yoy_growth_ttm", "earnings_per_share_diluted_yoy_growth_ttm",
        "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD", "Perf.Y",
        "price_52_week_high", "price_52_week_low", "RSI", "beta_1_year",
        "price_target_average", "recommendation_mark", "recommendation_total", "number_of_employees"]
US_EXCHANGES = ["NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "CBOE", "OTC"]


def _num(v):
    return v if isinstance(v, (int, float)) and v == v else None  # descarta None y NaN


# Sin "price_conversion", TradingView devuelve el precio objetivo (y la capitalización) en la divisa que le parece,
# a veces USD aunque la acción cotice en EUR, CHF o GBX: el 17/11 AMV0 salió con "objetivo +80 %" al comparar un
# objetivo en dólares con un precio en euros. Con to_symbol todo viene en la divisa de cotización del símbolo.
PRICE_CONVERSION = {"to_symbol": True}


def _scan(market: str, body: dict) -> list[dict]:
    r = net.request("POST", URL.format(market=market),
                    json={**body, "columns": COLS, "range": [0, 500], "price_conversion": PRICE_CONVERSION},
                    headers=HEADERS, timeout=40)
    return [dict(zip(COLS, it["d"]), symbol=it["s"]) for it in r.json().get("data", [])]


def _extra(row: dict) -> dict:
    close = _num(row.get("close"))
    target = _num(row.get("price_target_average"))
    fcur = row.get("fundamental_currency_code")
    # Objetivo y precio en la misma divisa (la de cotización, también peniques en Londres). Si TradingView no lo
    # garantiza, o el resultado es absurdo, mejor sin dato que un porcentaje falso.
    same = not fcur or fcur == row.get("currency")
    upside = 100 * (target / close - 1) if target and close and same else None
    if upside is not None and not -80 < upside < 400:
        upside = None
    hi, lo = _num(row.get("price_52_week_high")), _num(row.get("price_52_week_low"))
    out = {
        "sector": row.get("sector") or "", "industry": row.get("industry") or "",
        "pe": _num(row.get("price_earnings_ttm")), "pb": _num(row.get("price_book_fq")),
        "ev_ebitda": _num(row.get("enterprise_value_ebitda_ttm")), "div_yield": _num(row.get("dividends_yield_current")),
        "net_margin": _num(row.get("net_margin")), "roe": _num(row.get("return_on_equity")),
        "debt_eq": _num(row.get("debt_to_equity")),
        "rev_growth": _num(row.get("total_revenue_yoy_growth_ttm")),
        "eps_growth": _num(row.get("earnings_per_share_diluted_yoy_growth_ttm")),
        "perf_w": _num(row.get("Perf.W")), "perf_1m": _num(row.get("Perf.1M")), "perf_3m": _num(row.get("Perf.3M")),
        "perf_ytd": _num(row.get("Perf.YTD")), "perf_y": _num(row.get("Perf.Y")),
        "from_high": 100 * (close / hi - 1) if close and hi else None,
        "from_low": 100 * (close / lo - 1) if close and lo else None,
        "rsi": _num(row.get("RSI")), "beta": _num(row.get("beta_1_year")),
        "target_upside": upside,
        # Objetivo en la divisa de cotización (Londres: de peniques a libras, como el precio del mail).
        "target_price": (target / 100 if row.get("currency") == "GBX" else target) if upside is not None else None,
        "rec_mark": _num(row.get("recommendation_mark")),
        "rec_total": _num(row.get("recommendation_total")), "employees": _num(row.get("number_of_employees")),
        "tv_symbol": row.get("symbol", ""), "tv_currency": "GBP" if row.get("currency") == "GBX" else (row.get("currency") or ""),
    }
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


def add_extra(opportunities: list[dict], kind: str) -> str | None:
    """Añade o["extra"] a cada oportunidad. Devuelve un texto de error (o None) para las notas."""
    if not opportunities:
        return None
    try:
        if kind == "us":
            rows = _scan("america", {"filter": [{"left": "name", "operation": "in_range",
                                                 "right": [o["ticker"] for o in opportunities]}]})
            best: dict[str, dict] = {}
            rank = lambda r: US_EXCHANGES.index(r["exchange"]) if r.get("exchange") in US_EXCHANGES else 99  # noqa: E731
            for r in rows:
                if r["name"] not in best or rank(r) < rank(best[r["name"]]):
                    best[r["name"]] = r
            for o in opportunities:
                if o["ticker"] in best:
                    o["extra"] = _extra(best[o["ticker"]])
        else:
            syms = [f"{o['exchange']}:{o['ticker']}" for o in opportunities if o.get("exchange")]
            rows = {r["symbol"]: r for r in _scan("global", {"symbols": {"tickers": syms}})}
            for o in opportunities:
                r = rows.get(f"{o.get('exchange')}:{o['ticker']}")
                if r:
                    o["extra"] = _extra(r)
    except Exception as e:  # noqa: BLE001
        return f"Ficha ampliada (TradingView) no disponible: {str(e)[:80]}"
    missing = [o["ticker"] for o in opportunities if "extra" not in o]
    return f"Ficha ampliada sin datos para {', '.join(missing[:8])}" if missing else None
