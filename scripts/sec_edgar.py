"""Respaldo de OpenInsider: compras de insiders leídas directamente de la SEC (EDGAR).

Devuelve filas con el mismo formato que la tabla de OpenInsider, para que el resto
del script no cambie. Fuentes:
  - Índice diario de formularios: https://www.sec.gov/Archives/edgar/daily-index/AAAA/QTRn/form.AAAAMMDD.idx
  - "Latest filings" (Atom) para lo publicado después del último índice disponible.
Cada Form 4 se descarga una vez (.txt completo, que incluye el XML) y se filtra por
fecha y hora de aceptación en Nueva York, igual que la "Filing Date" de OpenInsider.
"""
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests

from fetch_openinsider import SEC_CONTACT, SEC_HEADERS, parse_form4

ARCHIVES = "https://www.sec.gov/Archives/"
CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb=&owner=include"
           "&count=100&output=atom&start={start}")
TITLE_ABBR = [
    (r"chief executive officer", "CEO"), (r"chief financial officer", "CFO"), (r"chief operating officer", "COO"),
    (r"chief technology officer", "CTO"), (r"chief accounting officer", "CAO"), (r"chairman of the board", "COB"),
    (r"executive vice president", "EVP"), (r"senior vice president", "SVP"), (r"vice president", "VP"),
    (r"general counsel", "GC"), (r"president", "Pres"), (r"secretary", "Secy"), (r"treasurer", "Treas"),
]


class RateLimiter:
    """La SEC permite 10 peticiones por segundo; nos quedamos en unas 8."""

    def __init__(self, per_second: float = 8.0):
        self.interval = 1.0 / per_second
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next:
                time.sleep(self.next - now)
            self.next = max(now, self.next) + self.interval


LIMIT = RateLimiter()


def sec_get(url: str, retries: int = 3) -> requests.Response:
    last = None
    for attempt in range(retries):
        LIMIT.wait()
        try:
            r = requests.get(url, headers=SEC_HEADERS, timeout=30)
            if r.status_code == 404:
                return r
            if r.status_code in (429, 503):
                raise requests.HTTPError(f"{r.status_code} (límite de la SEC)")
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def daily_index(day: date) -> list[str] | None:
    """Rutas .txt de los Form 4 del índice diario, o None si aún no está publicado."""
    q = (day.month - 1) // 3 + 1
    r = sec_get(f"{ARCHIVES}edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    if r.status_code == 404:
        return None
    paths = []
    for line in r.text.splitlines():
        if line.startswith("4 "):
            m = re.search(r"(edgar/data/\d+/[\d-]+\.txt)\s*$", line)
            if m:
                paths.append(m.group(1))
    return paths


def current_filings(since: datetime) -> list[str]:
    """Form 4 de "Latest filings" aceptados a partir de 'since' (hora de Nueva York, sin zona)."""
    paths = []
    for start in range(0, 2000, 100):
        r = sec_get(CURRENT.format(start=start))
        entries = re.findall(r"<entry>(.*?)</entry>", r.text, re.S)
        if not entries:
            break
        oldest = None
        for e in entries:
            link = re.search(r'href="([^"]+-index\.htm)"', e)
            upd = re.search(r"<updated>([\d-]+T[\d:]+)", e)
            if not (link and upd):
                continue
            when = datetime.fromisoformat(upd.group(1))
            oldest = when if oldest is None or when < oldest else oldest
            if when >= since:
                m = re.search(r"Archives/(edgar/data/\d+)/\d+/([\d-]+)-index\.htm", link.group(1))
                if m:
                    paths.append(f"{m.group(1)}/{m.group(2)}.txt")
        if oldest is not None and oldest < since:
            break
    return paths


def _text(node, path: str) -> str:
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def _num(txt: str) -> float:
    try:
        return float(txt.replace(",", ""))
    except ValueError:
        return 0.0


def build_title(rel) -> str:
    parts = []
    if rel is None:
        return ""
    if _text(rel, "isOfficer") in ("1", "true"):
        t = _text(rel, "officerTitle")
        for pat, abbr in TITLE_ABBR:
            t = re.sub(pat, abbr, t, flags=re.I)
        parts.append(t or "Officer")
    if _text(rel, "isDirector") in ("1", "true"):
        parts.append("Dir")
    if _text(rel, "isTenPercentOwner") in ("1", "true"):
        parts.append("10%")
    if _text(rel, "isOther") in ("1", "true") and not parts:
        parts.append(_text(rel, "otherText") or "Other")
    return ", ".join(parts)


def parse_filing(txt: str, path: str) -> tuple[dict | None, str]:
    """Convierte un Form 4 (.txt completo) en una fila al estilo de OpenInsider si contiene compras P."""
    acc = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", txt)
    accepted = datetime.strptime(acc.group(1), "%Y%m%d%H%M%S") if acc else None
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", txt, re.S)
    if not m:
        return None, ""
    xml = m.group(1)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None, xml
    if _text(root, "documentType") != "4":
        return None, xml
    ticker = _text(root, "issuer/issuerTradingSymbol").upper()
    if not ticker or ticker in ("NONE", "N/A", "NA"):
        return None, xml
    owner = root.find("reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else ""
    title = build_title(owner.find("reportingOwnerRelationship") if owner is not None else None)
    qty = value = 0.0
    owned = None
    trade_dates = []
    for tr in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _text(tr, "transactionCoding/transactionCode") != "P":
            continue
        if _text(tr, "transactionAmounts/transactionAcquiredDisposedCode/value") == "D":
            continue
        sh = _num(_text(tr, "transactionAmounts/transactionShares/value"))
        pr = _num(_text(tr, "transactionAmounts/transactionPricePerShare/value"))
        qty += sh
        value += sh * pr
        own = _text(tr, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        if own:
            owned = _num(own)
        d = _text(tr, "transactionDate/value")[:10]
        if d:
            trade_dates.append(d)
    if qty <= 0:
        return None, xml
    price = value / qty
    if owned is None:
        delta = ""
    elif owned - qty <= 0:
        delta = "New"
    else:
        p = 100 * qty / (owned - qty)
        delta = ">999%" if p > 999 else f"+{p:.0f}%"
    m2 = re.match(r"edgar/data/(\d+)/([\d-]+)\.txt", path)
    link = (f"https://www.sec.gov/Archives/edgar/data/{m2.group(1)}/{m2.group(2).replace('-', '')}/"
            f"{m2.group(2)}-index.htm") if m2 else ARCHIVES + path
    row = {
        "Filing Date": accepted.strftime("%Y-%m-%d %H:%M:%S") if accepted else "",
        "Trade Date": min(trade_dates) if trade_dates else "",
        "Ticker": ticker,
        "Company Name": _text(root, "issuer/issuerName"),
        "Insider Name": name,
        "Title": title,
        "Trade Type": "P - Purchase",
        "Price": f"${price:,.2f}",
        "Qty": f"+{qty:,.0f}",
        "Owned": f"{owned:,.0f}" if owned is not None else "",
        "ΔOwn": delta,
        "Value": f"+${value:,.0f}",
        "SEC Link": link,
        "_accepted": accepted,
    }
    return row, xml


def fetch_edgar_purchases(start: date, end: date) -> tuple[list[dict], dict, dict]:
    """Compras P publicadas (hora de Nueva York) entre start y end, ambos incluidos.

    Devuelve (filas al estilo OpenInsider, Form 4 ya revisados por enlace, estado)."""
    if not SEC_CONTACT:
        raise RuntimeError("falta el secreto SEC_CONTACT en GitHub")
    paths: list[str] = []
    missing_from = None
    day = start
    # Índices diarios: desde start hasta el siguiente día hábil después de end
    # (las publicaciones después de las 17:30 llevan fecha del día hábil siguiente).
    last = end + timedelta(days=1)
    while last.weekday() >= 5:
        last += timedelta(days=1)
    while day <= last:
        if day.weekday() < 5:
            idx = daily_index(day)
            if idx is None:
                missing_from = missing_from or day
            else:
                paths += idx
        day += timedelta(days=1)
    used_current = False
    if missing_from is not None:
        since = datetime.combine(min(missing_from, end), datetime.min.time())
        if missing_from > end:
            # Solo falta el índice del día hábil siguiente: basta con lo publicado tras las 17:00
            # del último día hábil del periodo (lo posterior lleva fecha del día siguiente).
            lb = end
            while lb.weekday() >= 5:
                lb -= timedelta(days=1)
            since = datetime.combine(lb, datetime.min.time()) + timedelta(hours=17)
        paths += current_filings(since)
        used_current = True
    paths = list(dict.fromkeys(paths))

    def work(path):
        try:
            r = sec_get(ARCHIVES + path)
            if r.status_code == 404:
                return path, None, "", "404"
            row, xml = parse_filing(r.text, path)
            return path, row, xml, None
        except Exception as e:  # noqa: BLE001
            return path, None, "", f"{type(e).__name__}: {e}"

    rows, form4, errors = [], {}, 0
    with ThreadPoolExecutor(4) as ex:
        for path, row, xml, err in ex.map(work, paths):
            if err:
                errors += 1
                continue
            if not row or not row["_accepted"]:
                continue
            if not (start <= row["_accepted"].date() <= end):
                continue
            row.pop("_accepted")
            rows.append(row)
            try:
                form4[row["SEC Link"]] = parse_form4(xml)
            except Exception:  # noqa: BLE001
                pass
    rows.sort(key=lambda r: r["Filing Date"], reverse=True)
    status = {"ok": errors <= max(10, len(paths) * 0.05), "filings": len(paths), "errors": errors,
              "rows": len(rows), "latest_feed": used_current}
    return rows, form4, status
