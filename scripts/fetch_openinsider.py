"""Funciones compartidas de descarga y lectura: OpenInsider, Form 4 de la SEC, Finviz y TradingView.

No se ejecuta solo: lo importan scripts/signals.py y scripts/sec_edgar.py.
"""
import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "http://openinsider.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tinytable")
    if table is None:
        raise ValueError("no se encontró la tabla 'tinytable'")
    header_row = table.find("thead").find("tr") if table.find("thead") else table.find("tr")
    headers = [clean(th.get_text()) for th in header_row.find_all(["th", "td"])]
    body = table.find("tbody") or table
    rows = []
    for tr in body.find_all("tr"):
        cells = [clean(td.get_text()) for td in tr.find_all("td")]
        if not cells or len(cells) != len(headers):
            continue
        row = {h: c for h, c in zip(headers, cells) if h}
        row.pop("X", None)
        for a in tr.find_all("a", href=True):
            if "sec.gov/Archives" in a["href"]:
                row["SEC Link"] = a["href"]
                break
        rows.append(row)
    return rows


# La SEC exige identificarse con un correo de contacto real.
# Se toma del secreto de GitHub SEC_CONTACT; si falta, no se revisan los Form 4.
SEC_CONTACT = __import__("os").environ.get("SEC_CONTACT", "").strip()
SEC_HEADERS = {
    "User-Agent": f"stonks-data {SEC_CONTACT}",
    "Accept-Encoding": "gzip, deflate",
}


def sec_xml_url(link: str) -> str:
    """Convierte el enlace de OpenInsider (versión con estilo) en el XML original."""
    link = link.replace("http://", "https://")
    return re.sub(r"/xslF345X\d+/", "/", link)


def parse_form4(xml: str) -> dict:
    soup = BeautifulSoup(xml, "html.parser")
    codes = sorted({c.get_text(strip=True) for c in soup.find_all("transactioncode")})
    flag = soup.find("aff10b5one")
    flag_val = flag.get_text(strip=True).lower() if flag else ""
    notes = " ".join(f.get_text(" ", strip=True) for f in soup.find_all("footnote"))
    # "non-Rule 10b5-1 trading arrangement" significa justo lo contrario: no es un plan.
    cleaned = re.sub(r"non[- ](rule[- ])?10b5-?1", "", notes, flags=re.I)
    plan_in_notes = bool(re.search(r"10b5-?1", cleaned, re.I))
    return {
        "codes": codes,
        "plan_10b5_1": flag_val in {"1", "true"} or plan_in_notes,
        "plan_checkbox": flag_val or "no consta",
        "notes": notes[:400],
    }


def fetch(url: str) -> str:
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last_error = e
            time.sleep(5 * (attempt + 1))
    raise last_error


FINVIZ_FIELDS = [
    "Market Cap", "Shs Outstand", "Shs Float", "Insider Own", "Inst Own",
    "Short Float", "Avg Volume", "Price", "Earnings", "Perf Week", "Perf Month",
]


def parse_money(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def parse_finviz(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    cells = [clean(td.get_text()) for td in soup.find_all("td")]
    wanted = {re.sub(r"[^a-z]", "", k.lower()): k for k in FINVIZ_FIELDS}
    data = {k: "" for k in FINVIZ_FIELDS}
    for i, text in enumerate(cells[:-1]):
        key = wanted.get(re.sub(r"[^a-z]", "", text.lower()))
        if key and not data[key]:
            data[key] = cells[i + 1]
    if not data["Market Cap"]:
        raise ValueError("no se encontraron los datos de Finviz")
    out_raw, float_raw = data["Shs Outstand"], data["Shs Float"]

    def to_number(text: str) -> float | None:
        m = re.match(r"^([\d.]+)([KMBT]?)$", text)
        if not m:
            return None
        mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]
        return float(m.group(1)) * mult

    outstanding, floating = to_number(out_raw), to_number(float_raw)
    data["Float %"] = (
        f"{100 * floating / outstanding:.1f}%" if outstanding and floating else "sin dato"
    )
    return data


def fetch_fundamentals(tickers: list[str]) -> tuple[dict, dict]:
    data, errors = {}, {}
    for ticker in tickers:
        url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
        try:
            data[ticker] = parse_finviz(fetch(url))
        except Exception as e:  # noqa: BLE001
            errors[ticker] = f"{type(e).__name__}: {e}"
        time.sleep(1.5)
    try:
        tv = fetch_tradingview(tickers)
    except Exception as e:  # noqa: BLE001
        tv = {}
        errors["_tradingview"] = f"{type(e).__name__}: {e}"
    for ticker, t in tv.items():
        entry = data.setdefault(ticker, {k: "" for k in FINVIZ_FIELDS} | {"Float %": "sin dato"})
        entry.update(t)
        errors.pop(ticker, None)
    return data, errors


TV_COLUMNS = ["name", "exchange", "float_shares_outstanding", "total_shares_outstanding", "market_cap_basic"]


def fetch_tradingview(tickers: list[str]) -> dict:
    """Float y acciones en circulación del scanner público de TradingView (2ª fuente)."""
    if not tickers:
        return {}
    body = {
        "filter": [{"left": "name", "operation": "in_range", "right": tickers}],
        "columns": TV_COLUMNS,
        "range": [0, 500],
    }
    r = requests.post(
        "https://scanner.tradingview.com/america/scan",
        json=body,
        headers={**HEADERS, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        row = dict(zip(TV_COLUMNS, item.get("d", [])))
        name, flt, total = row.get("name"), row.get("float_shares_outstanding"), row.get("total_shares_outstanding")
        if not name or name in out:
            continue
        out[name] = {
            "TV Exchange": row.get("exchange") or "",
            "TV Shs Float": f"{flt / 1e6:.2f}M" if flt else "",
            "TV Shs Outstand": f"{total / 1e6:.2f}M" if total else "",
            "TV Float %": f"{100 * flt / total:.1f}%" if flt and total else "sin dato",
            "TV Market Cap": row.get("market_cap_basic") or 0,
        }
    return out
