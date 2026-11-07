"""Descarga las tablas de compras de OpenInsider y las guarda en data/.

Salida:
  data/latest.json   - todas las filas de la última ejecución
  data/latest.md     - las mismas tablas en Markdown, fáciles de leer
  data/status.json   - estado de cada fuente (ok / error)
  data/archive/AAAA-MM-DD.json - copia diaria
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "http://openinsider.com"
SOURCES = {
    "top_purchases_of_the_day": f"{BASE}/top-insider-purchases-of-the-day",
    "latest_cluster_buys": f"{BASE}/latest-cluster-buys",
    "purchases_25k": f"{BASE}/insider-purchases-25k",
    # Compras (P) publicadas en los últimos 3 días, >= 100k $, precio >= 3 $,
    # solo directivos y consejeros.
    "screener_officers_100k": (
        f"{BASE}/screener?s=&o=&pl=3&ph=&ll=&lh=&fd=3&fdr=&td=0&tdr="
        "&fdlyl=&fdlyh=&daysago=&xp=1&vl=100&vh=&ocl=&och=&sic1=-1&sicl=100"
        "&sich=9999&isofficer=1&iscob=1&isceo=1&ispres=1&iscoo=1&iscfo=1"
        "&isgc=1&isvp=1&isdirector=1&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh="
        "&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=500&page=1"
    ),
}
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
        rows.append(row)
    return rows


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


def to_markdown(name: str, url: str, rows: list[dict]) -> str:
    out = [f"## {name}", "", f"Fuente: {url}", ""]
    if not rows:
        return "\n".join(out + ["(sin filas)", ""])
    cols = [c for c in rows[0].keys() if c not in {"1d", "1w", "1m", "6m"}]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "---|" * len(cols))
    for row in rows:
        out.append("| " + " | ".join(row.get(c, "").replace("|", "/") for c in cols) + " |")
    return "\n".join(out + [""])


FINVIZ_FIELDS = [
    "Market Cap", "Shs Outstand", "Shs Float", "Insider Own", "Inst Own",
    "Short Float", "Avg Volume", "Price", "Earnings", "Perf Week", "Perf Month",
]


def parse_money(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def candidate_tickers(results: dict, now: datetime, min_value: int = 50_000) -> list[str]:
    """Tickers con compras (P) publicadas en los últimos 4 días y valor mínimo."""
    tickers = []
    for source in results.values():
        for row in source["rows"]:
            ticker = row.get("Ticker", "")
            filed = row.get("Filing Date", "")[:10]
            if not ticker or not row.get("Trade Type", "").startswith("P"):
                continue
            try:
                age = (now.date() - datetime.strptime(filed, "%Y-%m-%d").date()).days
            except ValueError:
                continue
            if age <= 4 and parse_money(row.get("Value", "")) >= min_value and ticker not in tickers:
                tickers.append(ticker)
    return tickers[:80]


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
    return data, errors


def fundamentals_markdown(data: dict, errors: dict) -> str:
    out = [
        "## float_y_datos (Finviz)",
        "",
        "Float % = Shs Float / Shs Outstand. Fuente: https://finviz.com/quote.ashx?t=TICKER",
        "",
    ]
    cols = ["Float %"] + FINVIZ_FIELDS
    out.append("| Ticker | " + " | ".join(cols) + " |")
    out.append("|" + "---|" * (len(cols) + 1))
    for ticker, d in data.items():
        out.append(f"| {ticker} | " + " | ".join(d.get(c, "") for c in cols) + " |")
    if errors:
        out += ["", "Sin datos: " + ", ".join(f"{t} ({e})" for t, e in errors.items())]
    return "\n".join(out + [""])


def historical(date_str: str) -> int:
    """Compras (P) publicadas el día hábil anterior y el día indicado (AAAA-MM-DD)."""
    from datetime import timedelta
    from urllib.parse import quote

    day = datetime.strptime(date_str, "%Y-%m-%d")
    prev = day - timedelta(days=3 if day.weekday() == 0 else 1)
    fdr = quote(f"{prev:%m/%d/%Y} - {day:%m/%d/%Y}")
    url = (
        f"{BASE}/screener?s=&o=&pl=&ph=&ll=&lh=&fd=-1&fdr={fdr}&td=0&tdr="
        "&fdlyl=&fdlyh=&daysago=&xp=1&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999"
        "&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=1000&page=1"
    )
    rows = parse_table(fetch(url))
    results = {"historical_screener": {"url": url, "rows": rows}}
    tickers = []
    for r in rows:
        t = r.get("Ticker", "")
        if t and parse_money(r.get("Value", "")) >= 50_000 and t not in tickers:
            tickers.append(t)
    fundamentals, errors = fetch_fundamentals(tickers[:80])

    out = Path(__file__).resolve().parent.parent / "data" / "historico"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date_str,
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Float, precio y volumen de Finviz son ACTUALES, no de la fecha histórica.",
        "sources": results,
        "fundamentals": fundamentals,
        "missing": errors,
    }
    (out / f"{date_str}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (out / f"{date_str}.md").write_text(
        f"# Histórico {date_str}\n\n{payload['note']}\n\n"
        + to_markdown("historical_screener", url, rows)
        + "\n" + fundamentals_markdown(fundamentals, errors)
    )
    print(f"{len(rows)} filas, {len(fundamentals)} con datos de Finviz")
    return 0 if rows else 1


def main() -> int:
    import os
    if os.environ.get("HIST_DATE"):
        return historical(os.environ["HIST_DATE"])
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    (data_dir / "archive").mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    results, status, md = {}, {}, [
        "# OpenInsider – compras",
        "",
        f"Actualizado: {now.isoformat(timespec='minutes')} (UTC)",
        "",
    ]
    for name, url in SOURCES.items():
        try:
            rows = parse_table(fetch(url))
            results[name] = {"url": url, "rows": rows}
            status[name] = {"ok": True, "rows": len(rows)}
            md.append(to_markdown(name, url, rows))
        except Exception as e:  # noqa: BLE001 - registrar cualquier fallo
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            md.append(f"## {name}\n\nERROR: {status[name]['error']}\n")
        time.sleep(3)

    tickers = candidate_tickers(results, now)
    fundamentals, fund_errors = fetch_fundamentals(tickers)
    status["finviz_fundamentals"] = {
        "ok": bool(fundamentals) or not tickers,
        "rows": len(fundamentals),
        "missing": list(fund_errors),
    }
    md.append(fundamentals_markdown(fundamentals, fund_errors))

    payload = {
        "updated_utc": now.isoformat(timespec="seconds"),
        "sources": results,
        "fundamentals": fundamentals,
    }
    (data_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (data_dir / "latest.md").write_text("\n".join(md))
    (data_dir / "status.json").write_text(
        json.dumps({"updated_utc": payload["updated_utc"], "sources": status}, indent=1)
    )
    (data_dir / "archive" / f"{now:%Y-%m-%d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1)
    )
    print(json.dumps(status, indent=1))
    return 0 if any(s["ok"] for s in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
