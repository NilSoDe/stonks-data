"""Genera las señales del mail diario de Stonks con reglas fijas.

Ventana: el mail sale por la mañana (Madrid) del día D y cubre todos los Form 4
publicados (fecha de Nueva York) desde el día hábil anterior a D hasta el día
antes de D. El lunes cubre viernes, sábado y domingo.

Salida:
  data/signals.json  - oportunidades, descartadas, avisos y estado de fuentes
  data/signals.md    - versión legible
  data/latest.json   - datos en bruto (compras, Finviz/TradingView, Form 4)
  data/signals_log.json - historial de oportunidades (para no repetir)
Modo histórico (HIST_DATE=AAAA-MM-DD, fecha del mail):
  data/historico/signals-AAAA-MM-DD.json y .md (no toca el historial)
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_openinsider import (  # noqa: E402
    BASE, SEC_CONTACT, SEC_HEADERS, fetch, fetch_fundamentals, parse_form4,
    parse_money, parse_table, sec_xml_url,
)
from sec_edgar import fetch_edgar_purchases  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MADRID = ZoneInfo("Europe/Madrid")

# --- Reglas -----------------------------------------------------------------
MIN_INSIDER_VALUE = 100_000
MIN_CLUSTER_VALUE = 250_000
MIN_MARKET_CAP = 50e6
MIN_FLOAT_PCT = 50.0
PENNY_PRICE = 5.0
LOW_DOLLAR_VOLUME = 500_000
MAX_TRADE_AGE_DAYS = 30
REPEAT_DAYS = 30
CHASE_FLAG_PCT = 15      # aviso si cotiza un 15% por encima del precio del insider
MAX_CHASE_PCT = 30       # descarte si ya cotiza más de un 30% por encima
SQUEEZE_SHORT = 10.0     # short interest (% del float) que se destaca
SMALL_DELTA = 5.0        # aumento de posición simbólico
BIG_DELTA = 50.0         # aumento de posición fuerte


def delta_own(text) -> float | None:
    """ΔOwn de OpenInsider: 'New' -> infinito, '>999%' -> 999, '+22%' -> 22."""
    s = str(text or "").strip()
    if not s:
        return None
    if s.lower().startswith("new"):
        return float("inf")
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else None


def insider_pattern(insider: dict, hist: list[dict], window_start: date) -> str:
    """Clasificación tipo Cohen, Malloy y Pomorski (2012), solo con compras."""
    try:
        trade = datetime.strptime(insider["trade_date"][:10], "%Y-%m-%d").date()
    except ValueError:
        return "Sin historial"
    past = set()
    for r in hist:
        if r.get("Insider Name") != insider["name"]:
            continue
        try:
            d = datetime.strptime(r.get("Trade Date", "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < trade - timedelta(days=60):
            past.add((d.year, d.month))
    if not any(y >= trade.year - 3 for y, _ in past):
        return "Primera compra en 3 años"
    if all((trade.year - k, trade.month) in past for k in (1, 2, 3)):
        return "Rutinario"
    return "Oportunista"

OFFICER_RE = re.compile(
    r"VicePres|Vice Pres|\b(CEO|CFO|COO|CTO|CAO|CIO|CCO|CMO|Pres|President|VP|EVP|SVP|Chief|COB|Chairman|"
    r"Officer|GC|Treas|Secy|Controller|Exec)\b", re.I)
DIRECTOR_RE = re.compile(r"\bDir\b|Director", re.I)
CEO_CFO_RE = re.compile(r"\b(CEO|CFO)\b", re.I)
ENTITY_RE = re.compile(
    r"\b(LLC|L\.?P\.?|Ltd|Limited|Inc|Corp|Fund|Funds|Partners|Capital|Advisors|Advisers|"
    r"Management|Holdings|Trust|Group|Ventures|Investments|Bros|AG|SA|plc|GmbH)\b", re.I)
SPAC_RE = re.compile(r"Acquisition|\bSPAC\b", re.I)
CLOSED_FUND_RE = re.compile(r"\bFund\b|Income Trust|Municipal", re.I)
LIEU_RE = re.compile(r"in lieu of|retainer|director fees|board fees", re.I)


def pct(text) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*%", str(text or ""))
    return float(m.group(1)) if m else None


def human_number(text) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*([KMBT]?)\s*$", str(text or ""))
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]


def money_float(text) -> float | None:
    m = re.search(r"[\d.]+", str(text or "").replace(",", ""))
    return float(m.group(0)) if m else None


def window_for(mail_date: date) -> tuple[date, date]:
    """Primer y último día (inclusive) de publicación que cubre el mail de mail_date."""
    end = mail_date - timedelta(days=1)
    start = mail_date - timedelta(days=1)
    while start.weekday() >= 5:  # sábado/domingo -> retrocede al viernes
        start -= timedelta(days=1)
    return start, end


def screener_url(start: date, end: date, ticker: str = "") -> str:
    fdr = quote(f"{start:%m/%d/%Y} - {end:%m/%d/%Y}")
    return (
        f"{BASE}/screener?s={ticker}&o=&pl=&ph=&ll=&lh=&fd=-1&fdr={fdr}&td=0&tdr="
        "&fdlyl=&fdlyh=&daysago=&xp=1&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999"
        "&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=1000&page=1"
    )


def fetch_form4_links(links: list[str]) -> tuple[dict, dict]:
    data, errors = {}, {}
    if not SEC_CONTACT:
        return data, {"_config": "Falta el secreto SEC_CONTACT en GitHub"}
    for link in links[:200]:
        try:
            r = requests.get(sec_xml_url(link), headers=SEC_HEADERS, timeout=30)
            r.raise_for_status()
            data[link] = parse_form4(r.text)
        except Exception as e:  # noqa: BLE001
            errors[link] = f"{type(e).__name__}: {e}"
        time.sleep(0.15)
    return data, errors


def classify_row(row: dict, form4: dict) -> tuple[str | None, dict]:
    """Devuelve (motivo de descarte o None, información de la fila)."""
    title, name = row.get("Title", ""), row.get("Insider Name", "")
    company = row.get("Company Name", "")
    value = parse_money(row.get("Value", ""))
    price = money_float(row.get("Price", "")) or 0.0
    is_entity = bool(ENTITY_RE.search(name))
    is_officer = bool(OFFICER_RE.search(title))
    is_director = bool(DIRECTOR_RE.search(title))
    info = {
        "name": name, "title": title, "value": value, "price": price,
        "trade_date": row.get("Trade Date", ""), "filing_date": row.get("Filing Date", ""),
        "delta_own": row.get("ΔOwn", ""), "sec_link": row.get("SEC Link", ""),
        "tipo": "Fondo (consejo)" if is_entity else "Persona",
        "ceo_cfo": bool(CEO_CFO_RE.search(title)),
        "form4": "no revisado",
    }
    if price <= 0:
        return "Precio de 0 $ (no es compra real)", info
    if SPAC_RE.search(company):
        return "SPAC", info
    if CLOSED_FUND_RE.search(company):
        return "Fondo cerrado", info
    try:
        trade = datetime.strptime(info["trade_date"][:10], "%Y-%m-%d").date()
        filed = datetime.strptime(info["filing_date"][:10], "%Y-%m-%d").date()
        if (filed - trade).days > MAX_TRADE_AGE_DAYS:
            return f"Operación del {trade:%d/%m/%Y}, publicada tarde", info
    except ValueError:
        pass
    if not (is_officer or is_director):
        if re.search(r"remarks", title, re.I):
            return "Cargo sin especificar en OpenInsider (See Remarks)", info
        return f"No es directivo ni consejero ({title or 'sin cargo'})", info
    f4 = form4.get(info["sec_link"])
    if f4:
        info["form4"] = "revisado"
        if f4.get("plan_10b5_1"):
            return "Plan 10b5-1 (Form 4)", info
        if LIEU_RE.search(f4.get("notes", "")):
            return "Acciones en lugar de sueldo u honorarios (Form 4)", info
        if "P" not in f4.get("codes", []):
            return f"Form 4 sin código P ({','.join(f4.get('codes', []))})", info
    return None, info


def build(mail_date: date, historical: bool) -> int:
    start, end = window_for(mail_date)
    alerts, status = [], {}

    # 1) Todas las compras del periodo (sin el límite de 100 filas de las listas).
    url = screener_url(start, end)
    notes: list[str] = []
    holiday = False
    source = "openinsider"
    form4: dict = {}
    oi_error = ""
    try:
        if os.environ.get("FORCE_SEC") == "1":  # para probar el respaldo
            raise RuntimeError("prueba del respaldo (FORCE_SEC=1)")
        rows = [r for r in parse_table(fetch(url)) if r.get("Trade Type", "").startswith("P")]
        status["openinsider"] = {"ok": True, "rows": len(rows)}
    except Exception as e:  # noqa: BLE001
        rows = []
        oi_error = f"{type(e).__name__}: {e}"
        status["openinsider"] = {"ok": False, "error": oi_error}

    # 1b) Respaldo: si OpenInsider falla o no devuelve nada, se leen los Form 4 de la SEC (EDGAR).
    if not rows:
        try:
            rows, form4, st = fetch_edgar_purchases(start, end)
            status["sec_edgar"] = st
            source = "sec_edgar"
            why = f"ha fallado ({oi_error})" if oi_error else "no devolvía compras"
            notes.append(f"OpenInsider {why}: las compras se han leído directamente de la SEC (EDGAR), "
                         f"{st['filings']} Form 4 revisados.")
            if not st["ok"]:
                alerts.append(f"SEC (EDGAR): {st['errors']} de {st['filings']} Form 4 no se han podido leer.")
            if st["form4_in_window"] == 0 and st["ok"]:
                holiday = True
                notes[-1] = ("Festivo en EE. UU.: la SEC (EDGAR) no ha publicado ningún Form 4 en el periodo, "
                             "así que no hay compras nuevas.")
            elif not rows:
                alerts.append("Ni OpenInsider ni la SEC muestran compras en el periodo.")
        except Exception as e:  # noqa: BLE001
            status["sec_edgar"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            alerts.append(f"OpenInsider {'ha fallado: ' + oi_error if oi_error else 'no devuelve compras'} "
                          f"y el respaldo de la SEC también: {status['sec_edgar']['error']}")

    # 2) Form 4 de las compras de al menos 10.000 $ (los de la SEC ya vienen revisados).
    links = []
    for r in rows:
        link = r.get("SEC Link")
        if link and link not in links and link not in form4 and parse_money(r.get("Value", "")) >= 10_000:
            links.append(link)
    f4_new, f4_err = fetch_form4_links(links) if links else ({}, {})
    form4.update(f4_new)
    status["sec_form4"] = {"ok": not f4_err or bool(form4), "rows": len(form4), "errors": len(f4_err)}
    if "_config" in f4_err:
        alerts.append("Form 4 sin revisar: falta el secreto SEC_CONTACT en GitHub.")
    elif f4_err and len(f4_err) > len(form4):
        alerts.append(f"La SEC ha fallado en {len(f4_err)} de {len(links)} Form 4: {next(iter(f4_err.values()))}")

    # 3) Filtro por fila y agrupación por ticker.
    by_ticker, discarded = {}, []
    for r in rows:
        reason, info = classify_row(r, form4)
        t = r.get("Ticker", "")
        if reason:
            discarded.append({"ticker": t, "company": r.get("Company Name", ""), "value": info["value"],
                              "insider": info["name"], "float": "", "reason": reason})
            continue
        g = by_ticker.setdefault(t, {"ticker": t, "company": r.get("Company Name", ""), "insiders": []})
        g["insiders"].append(info)

    candidates = {}
    for t, g in by_ticker.items():
        total = sum(i["value"] for i in g["insiders"])
        if max(i["value"] for i in g["insiders"]) >= MIN_INSIDER_VALUE or total >= MIN_CLUSTER_VALUE:
            g["total_value"] = total
            candidates[t] = g
        else:
            discarded.append({"ticker": t, "company": g["company"], "value": total,
                              "insider": ", ".join(i["name"] for i in g["insiders"]), "float": "",
                              "reason": "Bajo el mínimo (100.000 $ por insider o 250.000 $ en cluster)"})

    # 4) Float, capitalización y datos de mercado (Finviz + TradingView).
    fund, fund_err = fetch_fundamentals(list(candidates)) if candidates else ({}, {})
    status["finviz_tradingview"] = {"ok": bool(fund) or not candidates, "rows": len(fund),
                                    "missing": [k for k in fund_err if not k.startswith("_")]}
    if "_tradingview" in fund_err:
        alerts.append(f"TradingView ha fallado: {fund_err['_tradingview']}")
    if candidates and len(fund) < len(candidates) / 2:
        alerts.append(f"Finviz/TradingView sin datos para {len(candidates) - len(fund)} de {len(candidates)} candidatas.")

    log_path = DATA / "signals_log.json"
    try:
        log = json.loads(log_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log = {}

    opportunities = []
    for t, g in candidates.items():
        f = fund.get(t, {})
        fv, tv = pct(f.get("Float %")), pct(f.get("TV Float %"))
        cap = human_number(f.get("Market Cap")) or (f.get("TV Market Cap") or None)
        float_txt = " / ".join(x for x in [f"{fv:.1f}%" if fv is not None else "",
                                           f"{tv:.1f}%" if tv is not None else ""] if x) or "Sin dato"
        reason = None
        if fv is None and tv is None:
            reason = "Float sin verificar (ni Finviz ni TradingView)"
        elif any(x is not None and x < MIN_FLOAT_PCT for x in (fv, tv)):
            reason = "Float bajo el 50%"
        elif cap is not None and cap < MIN_MARKET_CAP:
            reason = f"Capitalización de {cap / 1e6:.0f} M$ (mínimo 50 M$)"

        price = money_float(f.get("Price")) or g["insiders"][0]["price"]
        avg_vol = human_number(f.get("Avg Volume"))
        dollar_vol = avg_vol * price if avg_vol and price else None
        buy_price = sum(i["price"] * i["value"] for i in g["insiders"]) / max(g["total_value"], 1)
        chase = 100 * (price / buy_price - 1) if price and buy_price else None
        # 2.8 Liquidez real en dólares, además del float.
        if not reason and dollar_vol is not None and dollar_vol < LOW_DOLLAR_VOLUME:
            reason = f"Liquidez baja: {dollar_vol / 1e3:.0f} mil $ negociados al día (mínimo 500.000 $)"
        # 2.10 No perseguir el precio.
        if not reason and chase is not None and chase > MAX_CHASE_PCT:
            reason = f"Ya cotiza un {chase:.0f}% por encima del precio del insider"
        if reason:
            discarded.append({"ticker": t, "company": g["company"], "value": g["total_value"],
                              "insider": ", ".join(i["name"] for i in g["insiders"]),
                              "float": float_txt, "reason": reason})
            continue

        m = re.match(r"^\s*([+-]?[\d.]+)\s*%", str(f.get("Perf Month", "")))
        perf_month = float(m.group(1)) if m else None
        short = pct(f.get("Short Float"))
        deltas = [d for d in (delta_own(i["delta_own"]) for i in g["insiders"]) if d is not None]
        max_delta = max(deltas) if deltas else None
        flags, positives = [], []
        if price and price < PENNY_PRICE:
            flags.append("Penny stock: alta volatilidad y diferencial amplio")
        if fv is not None and tv is not None and abs(fv - tv) > 5:
            flags.append(f"Float distinto según la fuente (Finviz {fv:.1f}%, TradingView {tv:.1f}%)")
        if fv is None or tv is None:
            flags.append("Float de una sola fuente")
        if dollar_vol is None:
            flags.append("Liquidez sin verificar")
        if perf_month is not None and perf_month > 20:
            flags.append(f"Ya sube un {perf_month:.0f}% en un mes")
        if chase is not None and chase > CHASE_FLAG_PCT:
            flags.append(f"Cotiza un {chase:.0f}% por encima del precio del insider")
        if any(i["form4"] != "revisado" for i in g["insiders"]):
            flags.append("Form 4 no revisado")
        if not f:
            flags.append("Sin datos de mercado (Finviz/TradingView)")
        # 2.7 Aumento de posición.
        if max_delta is not None:
            if max_delta >= BIG_DELTA:
                positives.append("Posición nueva" if max_delta == float("inf")
                                 else f"Aumenta su posición un {max_delta:.0f}%")
            elif max_delta < SMALL_DELTA and g["total_value"] < 1_000_000:
                flags.append("Aumento de posición simbólico ("
                             + ("menos del 1%" if max_delta < 1 else f"+{max_delta:.0f}%") + ")")
        # 2.9 Short interest alto.
        if short is not None and short >= SQUEEZE_SHORT:
            positives.append(f"Short interest alto ({short:.1f}% del float): posible subida brusca si los bajistas cierran")
        if chase is not None and chase < -10:
            positives.append(f"Cotiza un {-chase:.0f}% por debajo del precio del insider")

        previous = [d for d in log.get(t, []) if d < mail_date.isoformat()
                    and (mail_date - date.fromisoformat(d)).days <= REPEAT_DAYS]
        opportunities.append({
            "ticker": t, "company": g["company"],
            "tipo": "Persona" if any(i["tipo"] == "Persona" for i in g["insiders"]) else "Fondo (consejo)",
            "insiders": g["insiders"], "n_insiders": len({i["name"] for i in g["insiders"]}),
            "total_value": g["total_value"], "avg_buy_price": round(buy_price, 2),
            "has_ceo_cfo": any(i["ceo_cfo"] for i in g["insiders"]),
            "float_finviz": fv, "float_tradingview": tv, "float_text": float_txt,
            "shs_float": f.get("Shs Float") or f.get("TV Shs Float", ""),
            "shs_outstand": f.get("Shs Outstand") or f.get("TV Shs Outstand", ""),
            "market_cap": f.get("Market Cap", ""), "price": price, "avg_volume": f.get("Avg Volume", ""),
            "earnings": f.get("Earnings", ""), "perf_week": f.get("Perf Week", ""),
            "perf_month": f.get("Perf Month", ""), "short_float": f.get("Short Float", ""),
            "insider_own": f.get("Insider Own", ""), "penny": bool(price and price < PENNY_PRICE),
            "flags": flags, "positives": positives, "ya_salio": previous[-1] if previous else "",
            "max_delta_own": "New" if max_delta == float("inf") else max_delta,
            "dollar_volume": round(dollar_vol) if dollar_vol else None,
            "short_float_pct": short, "chase_pct": round(chase, 1) if chase is not None else None,
        })

    # 5) Historial de compras de 4 años por ticker: cluster de 30 días y patrón del insider.
    for o in opportunities[:40]:
        hist = []
        if source == "openinsider":  # si OpenInsider está caído no se pierde tiempo reintentando
            try:
                hist = [r for r in parse_table(fetch(screener_url(end - timedelta(days=4 * 365), end, o["ticker"])))
                        if r.get("Trade Type", "").startswith("P")]
            except Exception:  # noqa: BLE001
                hist = []
        if hist:
            recent = [r for r in hist if r.get("Trade Date", "")[:10] >= (end - timedelta(days=30)).isoformat()]
            o["cluster_30d"] = len({r.get("Insider Name") for r in recent}) or o["n_insiders"]
        else:
            o["cluster_30d"] = o["n_insiders"]
        # 2.6 Rutinario (compra en el mismo mes los 3 años anteriores) u oportunista.
        patterns = []
        for i in o["insiders"]:
            i["pattern"] = insider_pattern(i, hist, start) if hist else "Sin historial"
            patterns.append(i["pattern"])
        if patterns and all(p == "Rutinario" for p in patterns):
            o["flags"].append("Insider rutinario: compra el mismo mes cada año (señal más débil)")
        elif "Primera compra en 3 años" in patterns:
            o["positives"].append("Primera compra en mercado abierto en 3 años (oportunista)")
        elif "Oportunista" in patterns:
            o["positives"].append("Compra oportunista, fuera de su patrón habitual")
        o["pattern"] = ("Rutinario" if patterns and all(p == "Rutinario" for p in patterns)
                        else next((p for p in ("Primera compra en 3 años", "Oportunista") if p in patterns),
                                  "Sin historial"))
        if source == "openinsider":
            time.sleep(1)

    opportunities.sort(key=lambda o: (o["tipo"] != "Persona", o.get("pattern") == "Rutinario",
                                      -o.get("cluster_30d", 1), not o["has_ceo_cfo"], -o["total_value"]))
    discarded.sort(key=lambda d: -d["value"])

    payload = {
        "mail_date": mail_date.isoformat(),
        "window": {"from": start.isoformat(), "to": end.isoformat(),
                   "note": "Fechas de publicación del Form 4 en hora de Nueva York, ambos días incluidos"},
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source, "holiday": holiday, "alerts": alerts, "notes": notes, "status": status,
        "opportunities": opportunities, "discarded": discarded,
    }
    raw = {"rows": rows, "fundamentals": fund, "form4": form4, "screener_url": url}

    md = [f"# Señales Stonks – mail del {mail_date:%d/%m/%Y}", "",
          f"Periodo: Form 4 publicados del {start:%d/%m} al {end:%d/%m} (hora de Nueva York).", ""]
    if alerts:
        md += ["## AVISOS", ""] + [f"- {a}" for a in alerts] + [""]
    if notes:
        md += ["## Notas", ""] + [f"- {n}" for n in notes] + [""]
    md += ["## Oportunidades", "", "| Ticker | Tipo | Insiders | Importe | Float | Precio | Cluster 30d | Patrón | A favor | Avisos |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for o in opportunities:
        md.append(f"| {o['ticker']} | {o['tipo']} | {', '.join(i['name'] + ' (' + i['title'] + ')' for i in o['insiders'])} "
                  f"| {o['total_value']:,} $ | {o['float_text']} | {o['price']} | {o.get('cluster_30d', '')} "
                  f"| {o.get('pattern', '')} | {'; '.join(o['positives'])} "
                  f"| {'; '.join(o['flags'])}{' · Ya salió el ' + o['ya_salio'] if o['ya_salio'] else ''} |")
    md += ["", "## Descartadas", "", "| Ticker | Importe | Float | Motivo |", "|---|---|---|---|"]
    md += [f"| {d['ticker']} | {d['value']:,} $ | {d['float'] or '-'} | {d['reason']} |" for d in discarded]

    if historical:
        out = DATA / "historico"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"signals-{mail_date}.json").write_text(json.dumps(payload | {"raw": raw}, ensure_ascii=False, indent=1))
        (out / f"signals-{mail_date}.md").write_text("\n".join(md))
    else:
        DATA.mkdir(exist_ok=True)
        (DATA / "signals.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        (DATA / "signals.md").write_text("\n".join(md))
        (DATA / "latest.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
        for o in opportunities:
            days = log.setdefault(o["ticker"], [])
            if mail_date.isoformat() not in days:
                days.append(mail_date.isoformat())
        log_path.write_text(json.dumps(log, indent=1, sort_keys=True))
    print(f"[{source}] {len(rows)} compras, {len(opportunities)} oportunidades, {len(discarded)} descartadas, "
          f"{len(alerts)} avisos")
    return 0


def main() -> int:
    hist = os.environ.get("HIST_DATE", "").strip()
    if hist:
        return build(date.fromisoformat(hist), historical=True)
    return build(datetime.now(MADRID).date(), historical=False)


if __name__ == "__main__":
    sys.exit(main())
