"""Genera el HTML de los mails a partir de las señales (sin intervención de Claude).

Uso:  python scripts/render_mail.py us|eu [ruta_signals.json]
Salida (junto al JSON de señales):
  mail.html       HTML ya minimizado, listo para enviar, con estos huecos para la tarea de Claude:
                    {{resumen}}                     párrafo de entrada (2-3 frases)
                    {{noticia_favor_TICKER}}        contexto de noticias a favor (o vacío)
                    {{noticia_riesgo_TICKER}}       contexto de noticias de riesgo (o vacío)
                    {{fuentes_noticias}}            enlaces de las noticias usadas (o vacío)
  mail_meta.json  asunto, si hay que enviar, tickers para buscar noticias, resumen por defecto y texto plano.
Las estrellas salen de una puntuación fija (la misma para todos los días), así el mail es reproducible.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
MAX_CARDS = 12
N_NEWS = 3

BADGE = ('<span style="{bg}padding:{pad};font-size:10px;letter-spacing:1px;text-transform:uppercase;font-weight:bold;'
         'white-space:nowrap;display:inline-block;line-height:14px;margin-bottom:4px;">{txt}</span>')
PENNY = BADGE.format(bg="background:#E4559A;color:#FFFFFF;", pad="4px 8px", txt="Penny stock")
BLUE = lambda txt: BADGE.format(bg="background:#1D3E8F;color:#FFFFFF;", pad="4px 8px", txt=txt)  # noqa: E731
REPEAT = lambda dm: BADGE.format(bg="border:1px dashed #8C877D;color:#8C877D;", pad="3px 8px",  # noqa: E731
                                 txt=f"Ya salió el {dm}")
CARD_NORMAL = dict(estilo_tarjeta="border:1px solid #111111;background:#FFFFFF;", color_numero="#D62718",
                   color_linea="#E6E1D5")
CARD_PENNY = dict(estilo_tarjeta="border:2px solid #E4559A;background:#FBE6EF;", color_numero="#E4559A",
                  color_linea="#F2B8D3")


# --------------------------------------------------------------------------- formato español

def es(n: float, dec: int = 2) -> str:
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(v: float, sym: str) -> str:
    v = v or 0
    return f"{es(v / 1e6, 2)} M{sym}" if v >= 1e6 else f"{es(v, 0)} {sym}"


def price_txt(p: float, sym: str) -> str:
    return f"{es(p, 2 if p >= 1 else 3)} {sym}"


def human(text) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*([KMBT]?)\s*$", str(text or ""))
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]


def millions(text) -> str:
    v = human(text) if not isinstance(text, (int, float)) else text
    if not v:
        return "—"
    return f"{es(v / 1e6, 2 if v < 1e9 else 0)} M" if v >= 1e6 else f"{es(v / 1e3, 0)} mil"


def pct_num(text) -> float | None:
    m = re.match(r"^\s*([+-]?[\d.]+)", str(text or ""))
    return float(m.group(1)) if m else None


def stars(n: int) -> str:
    return "★" * n + (f'<span style="color:#CFC9BB;">{"★" * (3 - n)}</span>' if n < 3 else "")


def dm(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%d/%m")
    except ValueError:
        return iso


def sentence(items: list[str]) -> str:
    items = [i.strip().rstrip(".") for i in items if i and i.strip()]
    return ". ".join(i[0].upper() + i[1:] for i in items) + "." if items else ""


def fill(s: str, m: dict) -> str:
    for k, v in m.items():
        s = s.replace("{{%s}}" % k, str(v))
    return s


def block(t: str, name: str) -> str:
    return re.search(rf"<!-- {name} \(repetir\) -->(.*?)<!-- /{name} -->", t, re.S).group(1)


# --------------------------------------------------------------------------- puntuación (estrellas)

FLAG_MINUS = re.compile(r"Ya sube|Cotiza un .* por encima|rutinario|simbólico|vinculada", re.I)


def score_us(o: dict) -> int:
    s = 2 if o.get("cluster_30d", 1) >= 3 else 1 if o.get("cluster_30d", 1) == 2 else 0
    s += 1 if o["has_ceo_cfo"] else 0
    s += 1 if o.get("pattern") in ("Oportunista", "Primera compra en 3 años") else 0
    md = o.get("max_delta_own")
    s += 1 if md == "New" or (isinstance(md, (int, float)) and md >= 50) else 0
    cap = human(o.get("market_cap"))
    s += 1 if cap and o["total_value"] / cap >= 0.0005 else 0
    pm = pct_num(o.get("perf_month"))
    s += 1 if pm is not None and pm <= -10 else 0
    s += 1 if (o.get("short_float_pct") or 0) >= 10 else 0
    s -= sum(1 for f in o["flags"] if FLAG_MINUS.search(f))
    s -= 1 if o.get("ya_salio") else 0
    return s


def score_eu(o: dict) -> int:
    s = 2 if o["cluster_30d"] >= 3 else 1 if o["cluster_30d"] == 2 else 0
    s += 1 if o["has_ceo_cfo"] else 0
    s += 1 if o["pattern"] in ("Oportunista", "Primera compra en 3 años") else 0
    s += 1 if o["market_cap"] and o["total_value"] / o["market_cap"] >= 0.0005 else 0
    pm = pct_num(o.get("perf_month"))
    s += 1 if pm is not None and pm <= -10 else 0
    s += 1 if o["total_value"] >= 250_000 else 0
    s -= sum(1 for f in o["flags"] if FLAG_MINUS.search(f))
    return s


def level(score: int, capped: bool) -> int:
    n = 3 if score >= 4 else 2 if score >= 2 else 1
    return min(n, 2) if capped else n


# --------------------------------------------------------------------------- nombres y cargos

US_ROLES = [(r"\bExec(?:utive)? COB\b|\bExecutive Chairman\b", "presidente ejecutivo"),
            (r"\bCOB\b|\bChairman\b", "presidente del consejo"), (r"\bDir\b", "consejero"),
            (r"\b10%", "accionista del 10%"), (r"\bPres\b|\bPresident\b", "presidente"),
            (r"\bVP\b", "VP"), (r"\bGC\b", "director jurídico"), (r"\bSecy\b", "secretario"),
            (r"\bTreas\b", "tesorero"), (r"\band\b", "y")]


def us_name(n: str, person: bool) -> str:
    if n.isupper():
        n = n.title()
    if not person:
        return n
    parts = n.split()
    suffix = [p for p in parts if p.rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv")]
    parts = [p for p in parts if p not in suffix]
    return " ".join(parts[1:] + parts[:1] + suffix) if len(parts) >= 2 else n


def us_role(t: str) -> str:
    out = []
    for part in [p.strip() for p in t.split(",") if p.strip()]:
        for pat, rep in US_ROLES:
            part = re.sub(pat, rep, part)
        out.append(part)
    out = list(dict.fromkeys(" y ".join(out).split(" y ")))
    if not out:
        return "insider"
    return out[0] if len(out) == 1 else ", ".join(out[:-1]) + " y " + out[-1]


def eu_name(n: str) -> str:
    if "," in n and not re.search(r"\b(SA|SE|AG|GmbH|SAS|SCI|SpA|SRL|Ltd|Limited|plc)\b", n, re.I):
        a, b = n.split(",", 1)
        n = f"{b.strip()} {a.strip()}"
    return n.title() if n.isupper() else n


def eu_role(t: str) -> str:
    t = {"Directivo (consejo ejecutivo)": "miembro del consejo ejecutivo (Vorstand)",
         "Consejero (consejo de supervisión)": "miembro del consejo de supervisión (Aufsichtsrat)"}.get(t, t)
    return t[0].lower() + t[1:] if t else "directivo"


SMALL_WORDS = {"OF", "THE", "AND", "DE", "LA", "DI", "DU", "ET", "DES", "DEL", "LE"}


def company(c: str) -> str:
    c = re.sub(r"/(New|DE|MD|NV)$", "", c.strip().replace(" Aktiengesellschaft", ""), flags=re.I)
    c = re.sub(r",?\s+(Inc\.?|Corp\.?|Corporation|Co\.|Ltd\.?|plc|SE|AG|SA|S\.A\.|S\.p\.A\.|N\.V\.|L\.P\.|LP)$", "", c,
               flags=re.I)
    if c.isupper() and " " in c:
        c = " ".join(w if len(w) <= 3 and w not in SMALL_WORDS and w.isalpha() else w.capitalize() for w in c.split())
    return c


def us_earnings(text: str, mail: date) -> str:
    m = re.match(r"([A-Z][a-z]{2}) (\d{1,2})", str(text or ""))
    if not m:
        return "Sin fecha"
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(m.group(1)) + 1
    d = date(mail.year, mon, int(m.group(2)))
    if (d - mail).days > 200:
        d = d.replace(year=mail.year - 1)
    elif (mail - d).days > 200:
        d = d.replace(year=mail.year + 1)
    return f"Publicados {d:%d/%m}" if d < mail else f"{d:%d/%m}"


# --------------------------------------------------------------------------- render

def render(kind: str, src: Path) -> dict:
    d = json.loads(src.read_text())
    mail = date.fromisoformat(d["mail_date"])
    eu = kind == "eu"
    t = (ROOT / "templates" / ("mail_eu.html" if eu else "mail.html")).read_text(encoding="utf-8")
    t = re.sub(r"<!--\s*PLANTILLA.*?-->", "", t, count=1, flags=re.S)
    card, otra, drow = block(t, "OPORTUNIDAD"), block(t, "FILA_OTRA"), block(t, "FILA_DESCARTADA")
    sym = "€" if eu else "$"
    who1, whoN = ("directivo", "directivos") if eu else ("insider", "insiders")

    # Estrellas y orden: por estrellas y, dentro de cada nivel, el orden del script.
    ops = []
    for idx, o in enumerate(d["opportunities"]):
        sc = score_eu(o) if eu else score_us(o)
        capped = o["tipo"] != "Persona"
        ops.append((level(sc, capped), idx, sc, o))
    ops.sort(key=lambda x: (-x[0], x[1]))

    cards, otras, plain, top = [], [], [], []
    for k, (n, _, sc, o) in enumerate(ops, 1):
        i = o["insiders"][0]
        person = i.get("tipo", "Persona") == "Persona"
        if eu and o["country"] == "CH":
            ins = "Directivo (nombre no publicado)"
        else:
            ins = eu_name(i["name"]) if eu else us_name(i["name"], person)
        extra = o["n_insiders"] - 1
        cargo = (eu_role(i["title"]) if eu else us_role(i["title"])) + (f", y {extra} más" if extra > 0 else "")
        fv = o.get("float_tradingview") if eu else (o.get("float_finviz") or o.get("float_tradingview"))
        float_pct = str(round(fv)) if fv is not None else "0"
        cl = o.get("cluster_30d") or o["n_insiders"]
        tipo_badge = ""
        if o["tipo"] != "Persona":
            tipo_badge = BLUE("Entidad vinculada" if eu else "Fondo (consejo)")
        risk = [f.replace(" (no se puede medir si es rutinario)", "") for f in o["flags"]]
        tk = re.sub(r"[^A-Za-z0-9]", "", o["ticker"])
        news = k <= N_NEWS
        m = dict(CARD_PENNY if o["penny"] else CARD_NORMAL)
        if eu:
            cur = {"EUR": "€", "GBP": "£"}.get(o["currency"], o["currency"])
            precio = price_txt(o["price"], cur)
            liq = o.get("dollar_volume")
            vol = "—" if not liq else (f"{es(liq / 1e6, 1)} M€ al día" if liq >= 1e6 else f"{liq / 1e3:.0f} mil € al día")
            res = dm(o["earnings"]) if o.get("earnings") else "Sin fecha"
        else:
            precio = price_txt(o["price"], "$")
            vol = millions(o.get("avg_volume"))
            res = us_earnings(o.get("earnings", ""), mail)
        m.update(posicion=f"{k:02d}", ticker=o["ticker"], compania=company(o["company"]), estrellas=stars(n),
                 insignia_tipo=tipo_badge, insignia_penny=PENNY if o["penny"] else "",
                 insignia_repetida=REPEAT(dm(o["ya_salio"])) if o.get("ya_salio") else "",
                 insider=ins, cargo=cargo, importe=money(o["total_value"], sym), precio=precio,
                 cluster=f"{cl} {who1 if cl == 1 else whoN}", fecha_operacion=dm(i["trade_date"]),
                 volumen_medio=vol, proximos_resultados=res, float_pct=float_pct,
                 acciones_float=millions(o.get("shs_float")),
                 por_que=(sentence(o["positives"]) or "Compra en mercado abierto.")
                 + (f"{{{{noticia_favor_{tk}}}}}" if news else ""),
                 riesgo=(sentence(risk) or "Sin avisos destacados.") + (f"{{{{noticia_riesgo_{tk}}}}}" if news else ""))
        if eu:
            m["pais"] = o["country_es"].upper()
        if news:
            top.append({"ticker": o["ticker"], "marker": tk, "company": o["company"],
                        "country": o.get("country_es", "EE. UU.")})
        line = f"{o['ticker']} {'★' * n} · {money(o['total_value'], sym)} · {ins} ({cargo})"
        plain.append(line)
        if k > MAX_CARDS:
            row = dict(o_ticker=o["ticker"], o_estrellas=stars(n), o_importe=m["importe"], o_float=float_pct + "%",
                       o_insider=f"{ins}, {cargo}")
            if eu:
                row["o_pais"] = m["pais"]
            otras.append(fill(otra, row))
        else:
            cards.append(fill(card, m))

    if cards:
        t = t.replace(card, "".join(cards))
        t = re.sub(r"<!-- AVISO_SIN_OPORTUNIDADES.*?-->", "", t, flags=re.S)
    else:
        aviso = re.search(r"<!-- AVISO_SIN_OPORTUNIDADES[^\n]*\n(.*?)\n\s*-->", t, re.S).group(1)
        t = t.replace(card, "")
        t = re.sub(r"<!-- AVISO_SIN_OPORTUNIDADES.*?-->", aviso, t, flags=re.S)
    if otras:
        t = t.replace(otra, "".join(otras))
    else:
        t = re.sub(r"<!-- OTRAS_OPORTUNIDADES -->.*?<!-- /OTRAS_OPORTUNIDADES -->", "", t, flags=re.S)

    # Descartadas: una fila por empresa desde el mínimo de cada mail.
    floor = 50_000 if eu else 100_000
    grouped: dict = {}
    for x in d["discarded"]:
        key = (x.get("country", ""), x["ticker"])
        g = grouped.setdefault(key, dict(x, value=0))
        g["value"] += x["value"]
    big = sorted((g for g in grouped.values() if g["value"] >= floor), key=lambda g: -g["value"])
    small = len(grouped) - len(big)
    rows = []
    for g in big:
        fl = g.get("float") or "—"
        fl = fl.replace(".", ",") if fl != "—" else fl
        r = dict(d_ticker=company(g["company"]) if eu else g["ticker"], d_importe=money(g["value"], sym),
                 d_float=fl, d_motivo=g["reason"])
        if eu:
            r["d_pais"] = g.get("country", "")
        rows.append(fill(drow, r))
    t = t.replace(drow, "".join(rows))

    # Avisos y fuentes.
    alerts = d.get("alerts") or []
    if alerts:
        t = t.replace("{{aviso_errores}}", " · ".join(alerts))
    else:
        t = re.sub(r'<p style="margin:0 0 14px;font-size:12px;font-weight:bold;color:#D62718[^>]*>\{\{aviso_errores\}\}</p>',
                   "", t)
    w = d["window"]
    period = (f"{dm(w['from'])}" if w["from"] == w["to"] else f"del {dm(w['from'])} al {dm(w['to'])}")
    if eu:
        fuentes = (f"Registros publicados {'el ' if w['from'] == w['to'] else ''}{period}. Float de TradingView "
                   "(mínimo 30%), importes por directivo desde 50.000 €.")
        if any(o["country"] == "CH" for o in d["opportunities"]):
            fuentes += " SIX (Suiza) no publica nombres ni fecha de aviso."
    else:
        fuentes = f"Form 4 publicados {'el ' if w['from'] == w['to'] else ''}{period}, hora de Nueva York."
    for note in d.get("notes") or []:
        note = re.sub(r"(https?://\S+)", r'<a href="\1" style="color:#8C877D;">anuncio</a>', note)
        fuentes += " " + note
    if eu:
        links = [("BaFin", "https://portal.mvp.bafin.de/database/DealingsInfo/"),
                 ("SIX", "https://www.ser-ag.com/en/topics/management-transactions.html"),
                 ("AMF", "https://bdif.amf-france.org/"),
                 ("CNMV", "https://internet.cnmv.es/Portal/Consultas/Directivos-Consulta?lang=es"),
                 ("eMarket Storage", "https://www.emarketstorage.it/it/comunicati-finanziari"),
                 ("RNS", "https://www.investegate.co.uk/category/directors-dealings"),
                 ("TradingView", "https://www.tradingview.com/")]
        fuentes_html = " · ".join(f'<a href="{u}" style="color:#F2EEE3;">{n}</a>' for n, u in links)
    else:
        fuentes_html = ('<a href="https://github.com/NilSoDe/stonks-data/blob/main/data/signals.md" '
                        'style="color:#F3B61F;text-decoration:none;">Señales del día</a>')
    total = len(cards) + len(otras)
    t = fill(t, dict(dia_semana=DIAS[mail.weekday()], fecha=f"{mail.day} de {MESES[mail.month - 1]}",
                     n_oportunidades=f"{total:02d}", n_descartadas=f"{len(grouped):02d}", aviso_fuentes=fuentes,
                     nota_descartadas=(f"Otras {small} compras de menos de {money(floor, sym)} no se muestran."
                                       if small else ""),
                     fuentes_enlaces=fuentes_html + "{{fuentes_noticias}}"))
    leftovers = set(re.findall(r"\{\{(\w+)\}\}", t)) - {"resumen", "fuentes_noticias"} - {
        f"noticia_favor_{x['marker']}" for x in top} - {f"noticia_riesgo_{x['marker']}" for x in top}
    assert not leftovers, f"Marcadores sin rellenar: {leftovers}"
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r">\s+<", "><", t)
    t = re.sub(r"\s{2,}", " ", t).strip()

    # Resumen por defecto (la tarea lo mejora con las noticias).
    if total:
        best = [f"{o['ticker']}" for n, _, _, o in ops[:3]]
        n3 = sum(1 for n, *_ in ops if n == 3)
        default = (f"{total} {'compra pasa' if total == 1 else 'compras pasan'} los filtros"
                   + (f", {n3} con tres estrellas" if n3 else "") + f". Destacan {', '.join(best)}.")
    else:
        default = "Hoy ninguna compra pasa los filtros."
    label = "Stonks Europa" if eu else "Stonks"
    meta = {
        "mail_date": d["mail_date"],
        "send": not d.get("holiday", False),
        "subject": f"{label} {mail:%d/%m} – {total} oportunidad{'es' if total != 1 else ''}",
        "n_opportunities": total,
        "news_for": top,
        "default_resumen": default,
        "alerts": alerts,
        "plain_text": f"{label} {mail:%d/%m}: {total} oportunidad{'es' if total != 1 else ''}.\n" + "\n".join(plain)
                      + ("\nAvisos: " + " · ".join(alerts) if alerts else ""),
        "html_bytes": len(t.encode()),
    }
    return {"html": t, "meta": meta}


def main() -> int:
    kind = sys.argv[1]
    src = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / ("eu" if kind == "eu" else "") / "signals.json"
    out = render(kind, src)
    stem = src.name.replace("signals", "mail").replace(".json", "")
    (src.parent / f"{stem}.html").write_text(out["html"], encoding="utf-8")
    (src.parent / f"{stem}_meta.json").write_text(json.dumps(out["meta"], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{src.parent / stem}.html: {out['meta']['html_bytes']} bytes, {out['meta']['subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
