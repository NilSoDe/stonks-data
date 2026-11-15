"""Prueba cada fuente real con una petición representativa y saca una tabla: estado, latencia, tamaño y si
el contenido trae lo que los lectores esperan. Uso: python scripts/check_sources.py  (workflow "Comprobar fuentes").
No reintenta: mide la fuente tal cual responde.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402
from fetch_openinsider import BASE, HEADERS, SEC_CONTACT, SEC_HEADERS, parse_finviz, parse_table  # noqa: E402
from signals import screener_url  # noqa: E402

EU = {"User-Agent": "Mozilla/5.0 (compatible; stonks-data)"}
d1 = date.today() - timedelta(days=1)
while d1.weekday() >= 5:
    d1 -= timedelta(days=1)
d0 = d1 - timedelta(days=4)
TV_BODY = {"filter": [{"left": "name", "operation": "in_range", "right": ["AAPL", "MTDR"]}],
           "columns": ["name", "float_shares_outstanding", "total_shares_outstanding", "market_cap_basic"], "range": [0, 10]}
TV_EU = {"filter": [{"left": "isin", "operation": "in_range", "right": ["DE0007030009", "GB0007980591"]}],
         "columns": ["name", "isin", "float_shares_outstanding", "total_shares_outstanding", "average_volume_30d_calc"],
         "range": [0, 20]}


def oi(t):
    rows = parse_table(t)
    need = {"Filing Date", "Trade Date", "Ticker", "Insider Name", "Title", "Trade Type", "Price", "Qty", "Owned", "Value"}
    miss = need - set(rows[0]) if rows else need
    return f"{len(rows)} filas" + (f"; FALTAN {sorted(miss)}" if miss else "") + (
        "" if rows and rows[0].get("SEC Link") else "; sin enlace al Form 4")


def finviz(t):
    f = parse_finviz(t)
    empty = [k for k, v in f.items() if not v or v == "sin dato"]
    return f"float {f['Float %']}, cap {f['Market Cap']}" + (f"; vacíos {empty}" if empty else "")


def tv(t):
    rows = json.loads(t).get("data", [])
    nulls = sum(1 for r in rows for v in r["d"] if v is None)
    return f"{len(rows)} valores" + (f"; {nulls} campos nulos" if nulls else "")


def count(pattern, what):
    return lambda t: f"{len(re.findall(pattern, t))} {what}"


def six(t):
    items = json.loads(t).get("itemList", [])
    keys = set(items[0]) if items else set()
    miss = {"ISIN", "buySellIndicator", "transactionDate", "transactionAmountCHF", "notificationId"} - keys
    return f"{len(items)} avisos; sin nombre ni fecha de publicación" + (f"; FALTAN {sorted(miss)}" if miss else "")


def amf(t):
    res = json.loads(t).get("result", [])
    return f"{len(res)} notificaciones; {sum(1 for r in res if r.get('documents'))} con PDF"


def fx(t):
    r = json.loads(t)["rates"]
    return f"{len(r)} divisas; CHF {r.get('CHF')}, GBP {r.get('GBP')}"


q = (d1.month - 1) // 3 + 1
CHECKS = [
    ("OpenInsider · screener (compras del periodo)", "GET", screener_url(d0, d1), HEADERS, None, oi),
    ("OpenInsider · top del día", "GET", f"{BASE}/top-insider-purchases-of-the-day", HEADERS, None, oi),
    ("OpenInsider · historial de un ticker (4 años)", "GET", screener_url(d1 - timedelta(days=1460), d1, "MTDR"), HEADERS, None, oi),
    ("Finviz · ficha (float, cap.)", "GET", "https://finviz.com/quote.ashx?t=MTDR&p=d", HEADERS, None, finviz),
    ("TradingView · scanner EE. UU.", "POST", "https://scanner.tradingview.com/america/scan", {**HEADERS, "Content-Type": "application/json"}, TV_BODY, tv),
    ("TradingView · scanner por ISIN (Europa)", "POST", "https://scanner.tradingview.com/global/scan", {**EU, "Content-Type": "application/json"}, TV_EU, tv),
    ("SEC EDGAR · índice diario (respaldo)", "GET", f"https://www.sec.gov/Archives/edgar/daily-index/{d1.year}/QTR{q}/form.{d1:%Y%m%d}.idx", SEC_HEADERS, None, count(r"(?m)^4 ", "Form 4")),
    ("SEC EDGAR · últimas presentaciones (respaldo)", "GET", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb=&owner=include&count=100&output=atom&start=0", SEC_HEADERS, None, count(r"<entry>", "entradas")),
    ("BaFin (Alemania) · buscador", "GET", "https://portal.mvp.bafin.de/database/DealingsInfo/sucheForm.do?locale=de_DE", EU, None, count(r"zeitraumVon", "campo de fechas")),
    ("SIX (Suiza) · JSON", "GET", f"https://www.ser-ag.com/sheldon/management_transactions/v1/overview.json?pageSize=1000&pageNumber=0&sortAttribute=byDate&fromDate={d0 - timedelta(days=10):%Y%m%d}", EU, None, six),
    ("AMF (Francia) · API", "GET", "https://bdif.amf-france.org/back/api/v1/informations?TypesInformation=DD&From=0&Size=100&sort=-datePublication", EU, None, amf),
    ("CNMV (España) · listado", "GET", f"https://internet.cnmv.es/portal/consultas/directivos-resultado.aspx?fechad={d0:%d/%m/%Y}&fechah={d1:%d/%m/%Y}&page=0", EU, None, count(r"elementoPrimerNivel", "notificaciones")),
    ("eMarket Storage (Italia) · listado", "GET", f"https://www.emarketstorage.it/it/comunicati-finanziari?categoria=110&data_from={d0}&data_to={d1}&page=0", EU, None, count(r'class="views-row"', "avisos")),
    ("Investegate (Reino Unido) · RNS", "GET", f"https://www.investegate.co.uk/category/directors-dealings?date={d1}&page=1", EU, None, count(r"/announcement/", "anuncios")),
    ("Frankfurter · tipos de cambio", "GET", "https://api.frankfurter.app/latest?from=EUR", EU, None, fx),
    ("GitHub Pages · informe web", "GET", "https://nilsode.github.io/stonks-data/", EU, None, count(r"noindex", "noindex")),
]


def main() -> int:
    print(f"## Fuentes, {date.today():%d/%m/%Y} (periodo de prueba {d0:%d/%m} a {d1:%d/%m})\n")
    print("| Fuente | Estado | Latencia | Tamaño | Tipo | Contenido |")
    print("|---|---|---|---|---|---|")
    bad = 0
    for name, method, url, headers, body, check in CHECKS:
        if "sec.gov" in url and not SEC_CONTACT:
            print(f"| {name} | sin SEC_CONTACT | - | - | - | no se puede probar |")
            continue
        t0 = time.monotonic()
        try:
            r = net.request(method, url, headers=headers, json=body, retries=1, timeout=60, ok=(403, 404))
            ms = (time.monotonic() - t0) * 1000
            ctype = r.headers.get("content-type", "?").split(";")[0]
            charset = (re.search(r"charset=([\w-]+)", r.headers.get("content-type", "")) or [None, "sin declarar"])[1]
            try:
                info = check(net.response_text(r)) if r.status_code == 200 else "sin contenido útil"
            except Exception as e:  # noqa: BLE001
                info = f"FORMATO INESPERADO: {type(e).__name__}: {str(e)[:80]}"
            st = "OK" if r.status_code == 200 and "INESPERADO" not in info and "FALTAN" not in info else f"FALLO ({r.status_code})"
            print(f"| {name} | {st} | {ms:.0f} ms | {len(r.content) / 1024:.0f} KB | {ctype} ({charset}) | {info} |")
        except Exception as e:  # noqa: BLE001
            st = "FALLO"
            print(f"| {name} | FALLO | {(time.monotonic() - t0) * 1000:.0f} ms | - | - | {type(e).__name__}: {str(e)[:110]} |")
        bad += st != "OK"
        time.sleep(1)
    print(f"\n{len(CHECKS) - bad} de {len(CHECKS)} fuentes responden bien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
