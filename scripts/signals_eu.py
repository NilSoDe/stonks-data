#!/usr/bin/env python3
"""Stonks Europa, fase 1: compras de directivos en Alemania (BaFin), Suiza (SIX) y Francia (AMF).

Salida: data/eu/signals.json (y signals.md). Con HIST_DATE=AAAA-MM-DD genera la señal de ese día
en data/eu/historico/. Mismos campos que la señal de EE. UU. para reutilizar la plantilla del mail.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "eu"
MADRID = ZoneInfo("Europe/Madrid")

# Umbrales (en euros; se convierten a la divisa de cada valor).
MIN_INSIDER_EUR = 50_000
MIN_CLUSTER_EUR = 100_000
MIN_MARKET_CAP_EUR = 45e6
MIN_FLOAT_PCT = 30.0
MIN_DAILY_EUR = 400_000
PENNY_PRICE = 5.0
MAX_TRADE_AGE_DAYS = 30
REPEAT_DAYS = 30
CHASE_FLAG_PCT = 15
MAX_CHASE_PCT = 30
BIG_BUY_EUR = 250_000

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stonks-data)"}
TV_MARKET = {"DE": "germany", "CH": "switzerland", "FR": "france"}
COUNTRY_ES = {"DE": "Alemania", "CH": "Suiza", "FR": "Francia"}

FX_FALLBACK = {"EUR": 1.0, "CHF": 0.93, "GBP": 0.84, "USD": 1.11, "HKD": 8.7, "SEK": 11.0,
               "NOK": 11.5, "DKK": 7.46, "PLN": 4.3}

PLAN_RE = re.compile(
    r"employee|mitarbeiter|\bESAP\b|\bRSU|\bPSU|option|optionen|call-option|exercis|aus[uü]bung|vesting|"
    r"kaufangebot|tender|andienung|bezug von|zuteilung|participation|beteiligung|trading plan|"
    r"gratuit|attribution|plan d|stock.?settled|sale.?to.?cover|net settlement|exersale|happy",
    re.I)
OFFMARKET_RE = re.compile(r"au[ßs]erb[öo]rslich|\bOTC\b|outside|hors march|hors plateforme", re.I)
ENTITY_RE = re.compile(
    r"\b(GmbH|AG|SE|KG|UG|mbH|Holding|Beteiligungs?|Verm[öo]gens|Invest|Capital|Partners|Fonds?|Fund|"
    r"Stiftung|Trust|S\.?A\.?S?\.?|SARL|SCI|Ltd|Limited|LLC|B\.V\.)\b", re.I)
CEO_CFO_RE = re.compile(
    r"\b(CEO|CFO|PDG|DG|DAF|Directeur G[ée]n[ée]ral|Directeur Financier|Chief Executive|Chief Financial|"
    r"Pr[ée]sident[- ]Directeur|Vorstandsvorsitzende[rn]?|Finanzvorstand|CEO/CFO)\b", re.I)


# --------------------------------------------------------------------------- utilidades

def get(url: str, session: requests.Session | None = None, retries: int = 3, **kw):
    err = None
    for i in range(retries):
        try:
            r = (session or requests).get(url, headers=HEADERS, timeout=45, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (i + 1))
    raise err  # type: ignore[misc]


def window_for(mail_date: date) -> tuple[date, date]:
    end = mail_date - timedelta(days=1)
    start = mail_date - timedelta(days=1)
    while start.weekday() >= 5:
        start -= timedelta(days=1)
    return start, end


def fx_rates() -> dict:
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=EUR", timeout=20)
        r.raise_for_status()
        rates = r.json()["rates"]
        rates["EUR"] = 1.0
        return rates
    except Exception:  # noqa: BLE001
        return dict(FX_FALLBACK)


def to_eur(amount: float, cur: str, fx: dict) -> float:
    rate = fx.get(cur.upper()) or FX_FALLBACK.get(cur.upper())
    return amount / rate if rate else amount


def num_de(text: str) -> tuple[float | None, str]:
    """'20.277,30 EUR' -> (20277.3, 'EUR')."""
    m = re.match(r"^\s*([\d.,]+)\s*([A-Za-z]{3})?", text or "")
    if not m:
        return None, ""
    n = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(n), (m.group(2) or "").upper()
    except ValueError:
        return None, ""


def fmt_date(d: date | str) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


# --------------------------------------------------------------------------- Alemania (BaFin)

BAFIN = "https://portal.mvp.bafin.de/database/DealingsInfo"


def fetch_de(start: date, end: date) -> tuple[list[dict], dict, dict]:
    """Compras publicadas en BaFin entre start y end (fecha de publicación). Devuelve (compras, historial, estado)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    get(f"{BAFIN}/sucheForm.do?locale=de_DE", s)
    # El filtro de BaFin es por fecha del negocio: se amplía 7 días y se filtra luego por publicación.
    frm, to = start - timedelta(days=7), end
    form = {"emittentIsin": "", "emittentName": "", "meldepflichtigerName": "", "zeitraum": "3",
            "zeitraumVon": f"{frm:%d.%m.%Y}", "zeitraumBis": f"{to:%d.%m.%Y}",
            "cmd": "search", "methodSuche": "Suche"}
    r = s.post(f"{BAFIN}/sucheForm.do?locale=de_DE", data=form, headers=HEADERS, timeout=45)
    r.raise_for_status()
    ids: dict[str, str] = {}
    html = r.text
    for page in range(1, 15):
        found = re.findall(r"meldepflichtigerId=(\d+)\">([^<]+)</a>", html)
        new = {i: n for i, n in found if i not in ids}
        if not new:
            break
        ids.update(new)
        nxt = (f"{BAFIN}/sucheForm.do?meldepflichtigerName=&zeitraum=3&emittentName=&zeitraumVon={frm:%d.%m.%Y}"
               f"&d-5010980-p={page + 1}&emittentIsin=&cmd=search&methodSuche=Suche&locale=de_DE"
               f"&zeitraumBis={to:%d.%m.%Y}")
        try:
            html = get(nxt, s).text
        except Exception:  # noqa: BLE001
            break

    def person_rows(pid: str) -> list[dict]:
        url = (f"{BAFIN}/ergebnisListe.do?cmd=loadEmittentenAction&6578706f7274=1&locale=de_DE"
               f"&d-4000784-e=1&meldepflichtigerId={pid}")
        text = get(url, s).content.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text), delimiter=";"))

    buys, history, failed = [], {}, 0
    for pid in ids:
        try:
            rows = person_rows(pid)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        time.sleep(0.4)
        for row in rows:
            isin = row.get("ISIN", "").strip()
            name = row.get("Meldepflichtiger", "").strip()
            kind = row.get("Art des Geschäfts", "").strip()
            instr = row.get("Art des Instruments", "").strip()
            try:
                trade = datetime.strptime(row.get("Datum des Geschäfts", ""), "%d.%m.%Y").date()
            except ValueError:
                continue
            if kind == "Kauf" and instr.lower().startswith("aktie"):
                history.setdefault((isin, name), []).append(trade)
            act = row.get("Datum der Aktivierung", "")[:10]
            try:
                pub = datetime.strptime(act, "%d.%m.%Y").date()
            except ValueError:
                continue
            if not (start <= pub <= end) or kind != "Kauf" or not instr.lower().startswith("aktie"):
                continue
            value, cur = num_de(row.get("Aggregiertes Volumen", ""))
            price, pcur = num_de(row.get("Durchschnittspreis", ""))
            if not value:
                continue
            pos = row.get("Position / Status", "").strip()
            buys.append({
                "country": "DE", "isin": isin, "company": row.get("Emittent", "").strip(), "insider": name,
                "role_raw": pos, "price": price, "currency": cur or pcur or "EUR", "value": value,
                "trade_date": trade, "pub_date": pub, "place": row.get("Ort des Geschäfts", "").strip(),
                "conditions": "", "source": "BaFin", "link_note": ""})
    status = {"ok": True, "persons": len(ids), "failed": failed, "purchases": len(buys)}
    return buys, history, status


def role_de(raw: str) -> tuple[str, str, bool]:
    r = raw.lower()
    if "aufsicht" in r or "verwaltungsrat" in r:
        return "Consejero (consejo de supervisión)", "director", False
    if "vorstand" in r or "geschäftsführ" in r or "geschaeftsfuehr" in r:
        return "Directivo (consejo ejecutivo)", "officer", bool(CEO_CFO_RE.search(raw))
    return "Otro directivo", "other", False


# --------------------------------------------------------------------------- Suiza (SIX)

SIX = "https://www.ser-ag.com/sheldon/management_transactions/v1/overview.json"


def fetch_ch(start: date, end: date, historical: bool, seen: dict, mail_date: date) -> tuple[list[dict], dict, set]:
    frm = (start - timedelta(days=10)).strftime("%Y%m%d")
    r = get(f"{SIX}?pageSize=1000&pageNumber=0&sortAttribute=byDate&fromDate={frm}")
    items = r.json().get("itemList", [])
    # Primera ejecución: todos los avisos vistos son de hoy (o no hay ninguno). Los avisos vistos hoy cuentan como nuevos.
    today = mail_date.isoformat()
    first_run = not any(v != today for v in seen.values())
    buys = []
    for it in items:
        if it.get("buySellIndicator") != "1" or str(it.get("securityTypeCode")) != "7":
            continue
        nid = it.get("notificationId", "")
        try:
            td = datetime.strptime(str(it["transactionDate"]), "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        # SIX no publica la fecha de notificación: en producción se usan los avisos aún no vistos;
        # en histórico (o primera ejecución) se usa la fecha de la operación.
        if historical or first_run:
            if not (start <= td <= end):
                continue
        elif (nid in seen and seen[nid] != today) or td > end:
            continue
        value = it.get("transactionAmountCHF") or 0
        if not value:
            continue
        fn = str(it.get("obligorFunctionCode"))
        buys.append({
            "country": "CH", "isin": it.get("ISIN", ""), "company": it.get("notificationSubmitter", ""),
            "insider": "Directivo sin nombre (SIX no lo publica)", "role_raw": "ejecutivo" if fn == "2" else "consejero",
            "price": it.get("transactionAmountPerSecurityCHF"), "currency": "CHF", "value": float(value),
            "trade_date": td, "pub_date": None, "place": "", "conditions": it.get("transactionConditions", "") or "",
            "related": bool(it.get("obligorRelatedPartyInd")), "nid": nid, "source": "SIX"})
    all_ids = {it.get("notificationId") for it in items if it.get("notificationId")}
    status = {"ok": True, "notifications": len(items), "purchases": len(buys), "first_run": first_run}
    return buys, status, all_ids


def role_ch(raw: str) -> tuple[str, str, bool]:
    if raw == "ejecutivo":
        return "Directivo (comité ejecutivo)", "officer", False
    return "Consejero (consejo de administración)", "director", False


# --------------------------------------------------------------------------- Francia (AMF)

AMF = "https://bdif.amf-france.org/back/api/v1"
FR_MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
             "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}


def pdf_text(data: bytes) -> str:
    try:
        out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def fr_number(text: str) -> float | None:
    t = re.sub(r"[\s  ]", "", text or "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_amf(text: str) -> list[dict]:
    t = re.sub(r"\s+", " ", text)
    isin = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", t)
    decl = re.search(r"ETROITEMENT LIEE\s*:\s*(.+?)\s*NOTIFICATION (?:INITIALE|MODIFICATIVE|CORRECTIVE)|"
                     r"ETROITEMENT LIEE\s*:\s*(.+?)\s*COORDONN", t)
    issuer = re.search(r"COORDONN\S* DE L.EMETTEUR NOM\s*:\s*(.+?)\s*LEI", t)
    if not (isin and decl):
        return []
    declarant = (decl.group(1) or decl.group(2)).strip()
    out = []
    for block in t.split("DETAIL DE LA TRANSACTION")[1:]:
        d = re.search(r"DATE DE LA TRANSACTION\s*:\s*(\d{1,2})\s+(\S+)\s+(\d{4})", block)
        nature = re.search(r"NATURE DE LA TRANSACTION\s*:\s*(.+?)\s*DESCRIPTION", block)
        instr = re.search(r"DESCRIPTION DE L.INSTRUMENT FINANCIER\s*:\s*(.+?)\s*CODE D.IDENT", block)
        place = re.search(r"LIEU DE LA TRANSACTION\s*:\s*(.+?)\s*NATURE", block)
        agg = re.search(r"INFORMATIONS AGREGEES PRIX\s*:\s*([\d\s.,]+?)\s*([A-Za-z]+)\s+VOLUME\s*:\s*([\d\s.,]+?)(?= [A-Z]|$)",
                        block)
        price = volume = cur = None
        if agg:
            price, cur, volume = fr_number(agg.group(1)), agg.group(2), fr_number(agg.group(3))
        else:
            pairs = re.findall(r"PRIX UNITAIRE\s*:\s*([\d\s.,]+?)\s*([A-Za-z]+)\s+VOLUME\s*:\s*([\d\s.,]+?)(?= [A-Z]|$)", block)
            if pairs:
                vols = [fr_number(p[2]) or 0 for p in pairs]
                prices = [fr_number(p[0]) or 0 for p in pairs]
                volume = sum(vols)
                price = sum(p * v for p, v in zip(prices, vols)) / volume if volume else None
                cur = pairs[0][1]
        linked = re.search(r"EXERCICE DE PROGRAMMES D.OPTIONS.*?:\s*(OUI|NON)", block)
        comment = re.search(r"COMMENTAIRES\s*:\s*(.+?)(?:\"Les donn|$)", block)
        if not (d and nature and price and volume):
            continue
        month = FR_MONTHS.get(d.group(2).lower())
        if not month:
            continue
        out.append({
            "isin": isin.group(1), "declarant": declarant, "company": issuer.group(1).strip() if issuer else "",
            "trade_date": date(int(d.group(3)), month, int(d.group(1))), "nature": nature.group(1).strip(),
            "instrument": instr.group(1).strip() if instr else "", "place": place.group(1).strip() if place else "",
            "price": price, "volume": volume, "currency": {"euro": "EUR"}.get((cur or "").lower(), (cur or "EUR").upper()),
            "linked_to_plans": bool(linked and linked.group(1) == "OUI"),
            "comment": (comment.group(1).strip() if comment else "")[:300]})
    return out


def fetch_fr(start: date, end: date) -> tuple[list[dict], dict]:
    docs, offset = [], 0
    while offset < 3000:
        r = get(f"{AMF}/informations?TypesInformation=DD&From={offset}&Size=100&sort=-datePublication")
        res = r.json().get("result", [])
        if not res:
            break
        oldest = None
        for it in res:
            pub = datetime.fromisoformat(it["datePublication"][:10]).date()
            oldest = pub if oldest is None else min(oldest, pub)
            if start <= pub <= end and it.get("documents"):
                docs.append((it, pub))
        if oldest and oldest < start:
            break
        offset += 100

    def work(item):
        it, pub = item
        path = it["documents"][0]["path"]
        try:
            pdf = get(f"{AMF}/documents/{path}").content
            return it, pub, parse_amf(pdf_text(pdf)), None
        except Exception as e:  # noqa: BLE001
            return it, pub, [], str(e)[:80]

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(work, docs))
    buys, unparsed, errors = [], 0, 0
    for it, pub, recs, err in results:
        if err:
            errors += 1
            continue
        if not recs:
            unparsed += 1
            continue
        for rec in recs:
            if not rec["nature"].lower().startswith("acquisition") or not rec["instrument"].lower().startswith("action"):
                continue
            decl = rec["declarant"]
            name, _, role = decl.rpartition(",")
            if not name:
                name, role = decl, ""
            buys.append({
                "country": "FR", "isin": rec["isin"], "company": rec["company"], "insider": name.strip(),
                "role_raw": role.strip(), "price": rec["price"], "currency": rec["currency"],
                "value": rec["price"] * rec["volume"], "trade_date": rec["trade_date"], "pub_date": pub,
                "place": rec["place"], "conditions": ("Vinculada a opciones/acciones gratuitas. " if rec["linked_to_plans"] else "")
                + rec["comment"], "source": "AMF",
                "link": f"https://bdif.amf-france.org/Resultat-de-recherche?docId={it['numero']}"})
    status = {"ok": errors < max(3, len(docs) / 4), "documents": len(docs), "errors": errors,
              "unparsed": unparsed, "purchases": len(buys)}
    return buys, status


def role_fr(raw: str) -> tuple[str, str, bool]:
    r = raw.lower()
    ceo = bool(CEO_CFO_RE.search(raw))
    if any(k in r for k in ("administrat", "conseil", "censeur", "président du c", "pca")):
        return f"Consejero ({raw})" if raw else "Consejero", "director", ceo
    if any(k in r for k in ("dg", "directeur", "pdg", "gérant", "gerant", "dirigeant", "daf", "président")):
        return f"Directivo ({raw})", "officer", ceo
    return (f"Otro cargo ({raw})" if raw else "Cargo no indicado"), "other", ceo


# --------------------------------------------------------------------------- mercado (TradingView)

TV_COLS = ["name", "description", "exchange", "isin", "close", "currency", "float_shares_outstanding",
           "total_shares_outstanding", "market_cap_basic", "average_volume_30d_calc", "type", "Perf.1M",
           "earnings_release_next_date"]


def tv_lookup(isins: list[str], market: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(0, len(isins), 40):
        chunk = isins[i:i + 40]
        body = {"filter": [{"left": "isin", "operation": "in_range", "right": chunk}], "columns": TV_COLS,
                "range": [0, 500]}
        r = requests.post(f"https://scanner.tradingview.com/{market}/scan", json=body,
                          headers={**HEADERS, "Content-Type": "application/json"}, timeout=40)
        r.raise_for_status()
        for item in r.json().get("data", []):
            row = dict(zip(TV_COLS, item["d"]))
            row["symbol"] = item["s"]
            out.setdefault(row["isin"], []).append(row)
    return out


def market_data(isins_by_country: dict[str, list[str]], fx: dict) -> tuple[dict, list[str]]:
    data, errors = {}, []
    for cc, isins in isins_by_country.items():
        if not isins:
            continue
        try:
            found = tv_lookup(isins, TV_MARKET[cc])
            missing = [i for i in isins if i not in found]
            if missing:
                found.update(tv_lookup(missing, "global"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"TradingView {COUNTRY_ES[cc]}: {str(e)[:80]}")
            continue
        for isin, rows in found.items():
            def liq(r):
                return (r.get("average_volume_30d_calc") or 0) * (r.get("close") or 0) / (fx.get(r.get("currency") or "EUR") or 1)
            primary = max(rows, key=liq)
            total_liq = sum(liq(r) for r in rows if (r.get("currency") == primary.get("currency")))
            flt, tot = primary.get("float_shares_outstanding"), primary.get("total_shares_outstanding")
            nxt = primary.get("earnings_release_next_date")
            data[isin] = {
                "ticker": primary["name"], "exchange": primary["exchange"], "description": primary.get("description", ""),
                "price": primary.get("close"), "currency": primary.get("currency") or "EUR",
                "float_pct": round(100 * flt / tot, 1) if flt and tot else None,
                "shs_float": flt, "shs_total": tot, "market_cap": primary.get("market_cap_basic"),
                "daily_eur": total_liq, "type": primary.get("type"), "perf_month": primary.get("Perf.1M"),
                "earnings": datetime.fromtimestamp(nxt, timezone.utc).date().isoformat() if nxt else "",
            }
    return data, errors


# --------------------------------------------------------------------------- patrón y cluster

def de_pattern(history: dict, isin: str, name: str, trade: date) -> str:
    past = [d for d in history.get((isin, name), []) if d < trade - timedelta(days=60)]
    if not any(d.year >= trade.year - 3 for d in past):
        return "Primera compra en 3 años"
    months = {(d.year, d.month) for d in past}
    if all((trade.year - k, trade.month) in months for k in (1, 2, 3)):
        return "Rutinario"
    return "Oportunista"


# --------------------------------------------------------------------------- construcción

def build(mail_date: date, historical: bool) -> int:
    start, end = window_for(mail_date)
    alerts: list[str] = []
    status: dict = {}
    fx = fx_rates()
    DATA.mkdir(parents=True, exist_ok=True)

    seen_path = DATA / "seen_ch.json"
    try:
        seen = json.loads(seen_path.read_text())
        if not isinstance(seen, dict):
            seen = {}
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {}

    trades: list[dict] = []
    history: dict = {}
    for cc, fn in (("DE", lambda: fetch_de(start, end)), ("CH", lambda: fetch_ch(start, end, historical, seen, mail_date)),
                   ("FR", lambda: fetch_fr(start, end))):
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            status[cc] = {"ok": False, "error": str(e)[:120]}
            alerts.append(f"{COUNTRY_ES[cc]}: la fuente oficial ha fallado ({str(e)[:80]}). Sin datos de este país hoy.")
            continue
        if cc == "DE":
            trades += res[0]
            history = res[1]
            status[cc] = res[2]
        elif cc == "CH":
            trades += res[0]
            status[cc] = res[1]
            all_ch_ids = res[2]
        else:
            trades += res[0]
            status[cc] = res[1]
            if not res[1]["ok"]:
                alerts.append(f"Francia: {res[1]['errors']} de {res[1]['documents']} PDF de la AMF no se han podido leer.")
            elif res[1]["unparsed"] > res[1]["documents"] * 0.4 and res[1]["documents"] >= 10:
                alerts.append(f"Francia: {res[1]['unparsed']} de {res[1]['documents']} notificaciones con formato no reconocido.")

    # Filtros de operación.
    discarded: list[dict] = []
    groups: dict[str, dict] = {}
    cutoff = mail_date - timedelta(days=MAX_TRADE_AGE_DAYS)
    roles = {"DE": role_de, "CH": role_ch, "FR": role_fr}
    for t in trades:
        reason = None
        if t["trade_date"] < cutoff:
            reason = f"Operación del {t['trade_date']:%d/%m} (más de {MAX_TRADE_AGE_DAYS} días)"
        elif PLAN_RE.search(t.get("conditions", "")):
            reason = "Plan de empleados, opciones u oferta pública (no es una compra de mercado)"
        elif OFFMARKET_RE.search(t.get("place", "")):
            reason = "Operación fuera de mercado"
        title, klass, ceo = roles[t["country"]](t["role_raw"])
        value_eur = to_eur(t["value"], t["currency"], fx)
        t.update(title=title, klass=klass, ceo_cfo=ceo, value_eur=value_eur)
        if reason:
            discarded.append({"ticker": t["isin"], "company": t["company"], "value": round(value_eur),
                              "insider": t["insider"], "float": "", "reason": reason, "country": t["country"]})
            continue
        g = groups.setdefault(t["isin"], {"isin": t["isin"], "company": t["company"], "country": t["country"], "trades": []})
        g["trades"].append(t)

    # Insiders agregados por ISIN.
    candidates: dict[str, dict] = {}
    for isin, g in groups.items():
        by_name: dict[str, dict] = {}
        for t in g["trades"]:
            key = t["insider"] if g["country"] != "CH" else f"{t['insider']}#{t.get('nid', '')}"
            i = by_name.setdefault(key, {"name": t["insider"], "title": t["title"], "klass": t["klass"],
                                         "ceo_cfo": t["ceo_cfo"], "value": 0.0, "value_local": 0.0, "shares": 0.0,
                                         "currency": t["currency"], "trade_date": t["trade_date"].isoformat(),
                                         "tipo": "Entidad vinculada" if (g["country"] != "CH" and (
                                             "," not in t["insider"] or ENTITY_RE.search(t["insider"]))
                                             or t.get("related")) else "Persona",
                                         "conditions": t.get("conditions", ""), "source": t["source"]})
            i["value"] += t["value_eur"]
            i["value_local"] += t["value"]
            i["shares"] += t["value"] / t["price"] if t["price"] else 0
            i["trade_date"] = max(i["trade_date"], t["trade_date"].isoformat())
        insiders = list(by_name.values())
        total = sum(i["value"] for i in insiders)
        if all(i["value"] < MIN_INSIDER_EUR for i in insiders) and total < MIN_CLUSTER_EUR:
            discarded.append({"ticker": isin, "company": g["company"], "value": round(total),
                              "insider": ", ".join(i["name"] for i in insiders), "float": "", "country": g["country"],
                              "reason": "Bajo el mínimo (50.000 € por directivo o 100.000 € en cluster)"})
            continue
        g.update(insiders=insiders, total_value=total)
        candidates[isin] = g

    # Mercado.
    by_country: dict[str, list[str]] = {}
    for isin, g in candidates.items():
        by_country.setdefault(g["country"], []).append(isin)
    mkt, mkt_err = market_data(by_country, fx) if candidates else ({}, [])
    alerts += mkt_err
    status["tradingview"] = {"ok": not mkt_err, "rows": len(mkt)}

    log_path = DATA / "trades_log.json"
    try:
        log = json.loads(log_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log = {}

    opportunities = []
    for isin, g in candidates.items():
        m = mkt.get(isin, {})
        cur_local = g["insiders"][0]["currency"]
        price = m.get("price")
        mcur = m.get("currency", cur_local)
        cap_eur = to_eur(m["market_cap"], mcur, fx) if m.get("market_cap") else None
        daily = m.get("daily_eur")
        # Precio medio de compra en la divisa de cotización (solo con insiders que operaron en esa divisa).
        den = sum(i["value_local"] for i in g["insiders"] if i["currency"] == mcur)
        shares = sum(i["shares"] for i in g["insiders"] if i["currency"] == mcur)
        buy_price = den / shares if den and shares and price else None
        chase = 100 * (price / buy_price - 1) if price and buy_price else None
        float_pct = m.get("float_pct")
        float_txt = f"{float_pct:.1f}%" if float_pct is not None else "Sin dato"
        names = ", ".join(i["name"] for i in g["insiders"])

        reason = None
        if not m:
            reason = "Sin datos de mercado en TradingView (float sin verificar)"
        elif m.get("type") not in ("stock", "dr"):
            reason = "Fondo cotizado o instrumento que no es una acción ordinaria"
        elif float_pct is None:
            reason = "Float sin verificar (TradingView sin dato)"
        elif float_pct < MIN_FLOAT_PCT:
            reason = f"Float bajo el {MIN_FLOAT_PCT:.0f}%"
        elif cap_eur is not None and cap_eur < MIN_MARKET_CAP_EUR:
            reason = f"Capitalización de {cap_eur / 1e6:.0f} M€ (mínimo 45 M€)"
        elif daily is not None and daily < MIN_DAILY_EUR:
            reason = f"Liquidez baja: {daily / 1e3:.0f} mil € negociados al día (mínimo 400.000 €)"
        elif chase is not None and chase > MAX_CHASE_PCT:
            reason = f"Ya cotiza un {chase:.0f}% por encima del precio del directivo"
        if reason:
            discarded.append({"ticker": m.get("ticker") or isin, "company": g["company"], "value": round(g["total_value"]),
                              "insider": names, "float": float_txt, "reason": reason, "country": g["country"]})
            continue

        price_eur = to_eur(price, mcur, fx) if price else None
        penny = bool(price_eur and price_eur < PENNY_PRICE)
        perf = m.get("perf_month")
        flags, positives = [], []
        if penny:
            flags.append("Penny stock: alta volatilidad y diferencial amplio")
        if daily is None or not daily:
            flags.append("Liquidez sin verificar")
        if perf is not None and perf > 20:
            flags.append(f"Ya sube un {perf:.0f}% en un mes")
        if chase is not None and chase > CHASE_FLAG_PCT:
            flags.append(f"Cotiza un {chase:.0f}% por encima del precio del directivo")
        if chase is not None and chase < -10:
            positives.append(f"Cotiza un {-chase:.0f}% por debajo del precio del directivo")
        if g["country"] == "CH":
            flags.append("Suiza no publica el nombre del directivo: no se sabe si varias compras son de la misma persona")
        if any(i["tipo"] == "Entidad vinculada" for i in g["insiders"]):
            flags.append("Compra a través de una entidad o persona vinculada, no del directivo directamente")
        if g["total_value"] >= BIG_BUY_EUR:
            positives.append(f"Compra grande: {g['total_value']:,.0f} €".replace(",", "."))

        names_set = {i["name"] for i in g["insiders"]} if g["country"] != "CH" else {f"ch{n}" for n in range(len(g["insiders"]))}
        prior = [e for e in log.get(isin, []) if (mail_date - date.fromisoformat(e["date"])).days <= 30
                 and e["date"] < mail_date.isoformat()] if not historical else []
        cluster = len(names_set | {e["name"] for e in prior})
        if cluster >= 2:
            positives.append(f"Cluster: {cluster} directivos comprando en 30 días")
        previous = [e["date"] for e in log.get(isin, []) if e["date"] < mail_date.isoformat()
                    and (mail_date - date.fromisoformat(e["date"])).days <= REPEAT_DAYS] if not historical else []

        patterns = []
        for i in g["insiders"]:
            if g["country"] == "DE":
                i["pattern"] = de_pattern(history, isin, i["name"], date.fromisoformat(i["trade_date"]))
            else:
                i["pattern"] = "Sin historial"
            patterns.append(i["pattern"])
        if patterns and all(p == "Rutinario" for p in patterns):
            pattern = "Rutinario"
            flags.append("Directivo rutinario: compra el mismo mes cada año (señal más débil)")
        elif "Primera compra en 3 años" in patterns:
            pattern = "Primera compra en 3 años"
            positives.append("Primera compra en mercado en 3 años (oportunista)")
        elif "Oportunista" in patterns:
            pattern = "Oportunista"
            positives.append("Compra oportunista, fuera de su patrón habitual")
        else:
            pattern = "Sin historial"
        if g["country"] != "DE":
            flags.append("Sin historial del directivo en esta fuente (no se puede medir si es rutinario)")

        for i in g["insiders"]:
            i["value"] = round(i["value"])
            i["value_local"] = round(i["value_local"])
            i.pop("shares", None)
        opportunities.append({
            "ticker": m["ticker"], "company": g["company"], "country": g["country"], "country_es": COUNTRY_ES[g["country"]],
            "isin": isin, "exchange": m["exchange"], "currency": mcur,
            "tipo": "Persona" if any(i["tipo"] == "Persona" for i in g["insiders"]) else "Entidad vinculada",
            "insiders": g["insiders"], "n_insiders": len(g["insiders"]), "cluster_30d": cluster,
            "total_value": round(g["total_value"]), "avg_buy_price": round(buy_price, 3) if buy_price else None,
            "has_ceo_cfo": any(i["ceo_cfo"] for i in g["insiders"]),
            "float_tradingview": float_pct, "float_text": float_txt,
            "shs_float": f"{m['shs_float'] / 1e6:.2f}M" if m.get("shs_float") else "",
            "market_cap": round(cap_eur) if cap_eur else None, "price": price, "avg_volume": None,
            "dollar_volume": round(daily) if daily else None, "earnings": m.get("earnings", ""),
            "perf_month": f"{perf:.1f}%" if perf is not None else "", "short_float_pct": None, "max_delta_own": None,
            "chase_pct": round(chase, 1) if chase is not None else None, "pattern": pattern,
            "penny": penny, "flags": flags, "positives": positives, "ya_salio": previous[-1] if previous else "",
        })

    opportunities.sort(key=lambda o: (o["tipo"] != "Persona", o["pattern"] == "Rutinario", -o["cluster_30d"],
                                      not o["has_ceo_cfo"], -o["total_value"]))
    merged: dict[tuple, dict] = {}
    for d in discarded:
        k = (d["country"], d["company"], d["reason"])
        if k in merged:
            merged[k]["value"] += d["value"]
            if d["insider"] not in merged[k]["insider"]:
                merged[k]["insider"] += ", " + d["insider"]
        else:
            merged[k] = dict(d)
    discarded = sorted(merged.values(), key=lambda d: -d["value"])

    payload = {
        "mail_date": mail_date.isoformat(),
        "window": {"from": start.isoformat(), "to": end.isoformat(),
                   "note": "Fecha de publicación del registro (BaFin y AMF); SIX no da fecha de publicación y se usa la fecha de la operación o los avisos nuevos"},
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thresholds": {"min_insider_eur": MIN_INSIDER_EUR, "min_cluster_eur": MIN_CLUSTER_EUR, "min_float_pct": MIN_FLOAT_PCT,
                       "min_market_cap_eur": MIN_MARKET_CAP_EUR, "min_daily_eur": MIN_DAILY_EUR},
        "fx_eur": {k: v for k, v in fx.items() if k in ("CHF", "GBP", "USD")},
        "alerts": alerts, "status": status, "opportunities": opportunities, "discarded": discarded,
    }

    md = [f"# Señales Stonks Europa – mail del {mail_date:%d/%m/%Y}", "",
          f"Periodo: registros publicados del {start:%d/%m} al {end:%d/%m}. Alemania (BaFin), Suiza (SIX), Francia (AMF).", ""]
    if alerts:
        md += ["## AVISOS", ""] + [f"- {a}" for a in alerts] + [""]
    md += ["## Oportunidades", "", "| País | Ticker | Empresa | Insiders | Importe | Float | Precio | Cluster | Patrón | A favor | Avisos |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for o in opportunities:
        md.append(f"| {o['country']} | {o['ticker']} | {o['company']} | "
                  f"{', '.join(i['name'] + ' (' + i['title'] + ')' for i in o['insiders'])} | {o['total_value']:,} € | "
                  f"{o['float_text']} | {o['price']} {o['currency']} | {o['cluster_30d']} | {o['pattern']} | "
                  f"{'; '.join(o['positives'])} | {'; '.join(o['flags'])} |")
    md += ["", "## Descartadas", "", "| País | Valor | Importe | Float | Motivo |", "|---|---|---|---|---|"]
    md += [f"| {d['country']} | {d['company']} | {d['value']:,} € | {d['float'] or '-'} | {d['reason']} |" for d in discarded]

    if historical:
        out = DATA / "historico"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"signals-{mail_date}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        (out / f"signals-{mail_date}.md").write_text("\n".join(md))
    else:
        (DATA / "signals.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        (DATA / "signals.md").write_text("\n".join(md))
        for o in opportunities:
            for i in o["insiders"]:
                entries = log.setdefault(o["isin"], [])
                e = {"name": i["name"] if o["country"] != "CH" else f"ch-{i['trade_date']}-{i['value']}",
                     "date": mail_date.isoformat(), "value_eur": i["value"]}
                if e not in entries:
                    entries.append(e)
        for isin in list(log):
            log[isin] = [e for e in log[isin] if (mail_date - date.fromisoformat(e["date"])).days <= 120]
            if not log[isin]:
                del log[isin]
        log_path.write_text(json.dumps(log, indent=1, sort_keys=True, ensure_ascii=False))
        if status.get("CH", {}).get("ok"):
            today = mail_date.isoformat()
            new_seen = {k: v for k, v in seen.items() if (mail_date - date.fromisoformat(v)).days <= 30}
            for nid in all_ch_ids:
                new_seen.setdefault(nid, today)
            seen_path.write_text(json.dumps(new_seen, sort_keys=True))
    print(f"{len(trades)} compras, {len(opportunities)} oportunidades, {len(discarded)} descartadas, {len(alerts)} avisos")
    return 0


def main() -> int:
    hist = os.environ.get("HIST_DATE", "").strip()
    if hist:
        return build(date.fromisoformat(hist), historical=True)
    return build(datetime.now(MADRID).date(), historical=False)


if __name__ == "__main__":
    sys.exit(main())
