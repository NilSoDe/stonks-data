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

Informe web: si la variable REPORT_URL trae la dirección del informe del día en GitHub Pages (la pone el workflow
solo cuando la página ya responde), el mail lleva el botón "Ver informe completo" y enlaces a la versión web
completa (todas las tarjetas y todas las descartadas, sin el límite de tamaño de Gmail). Sin REPORT_URL el mail
sale igual que antes, sin enlaces rotos. render(..., web=True) genera esa versión web (la usa publish_site.py).
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
N_NEWS = 3

BADGE = ('<span style="{bg}padding:{pad};font-size:10px;letter-spacing:1px;font-weight:bold;'
         'white-space:nowrap;display:inline-block;line-height:14px;margin-bottom:4px;">{txt}</span>')
PENNY = BADGE.format(bg="background:#E4559A;color:#FFFFFF;", pad="4px 8px", txt="PENNY STOCK")
BLUE = lambda txt: BADGE.format(bg="background:#1D3E8F;color:#FFFFFF;", pad="4px 8px", txt=txt.upper())  # noqa: E731
REPEAT = lambda dm: BADGE.format(bg="border:1px dashed #8C877D;color:#8C877D;", pad="3px 8px",  # noqa: E731
                                 txt=f"YA SALIÓ EL {dm}")
CARD_NORMAL = dict(estilo_tarjeta="border:1px solid #111;background:#fff;", color_numero="#D62718",
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
    items = [re.sub(r"(\d)\.(\d)", r"\1,\2", i.strip().rstrip(".")) for i in items if i and i.strip()]
    return ". ".join(i[0].upper() + i[1:] for i in items) + "." if items else ""


SHORT_HEX = {"#111111": "#111", "#FFFFFF": "#FFF", "#8C877D": "#887", "#55524B": "#554"}


def compact(h: str) -> str:
    """Mismos datos en menos bytes y en ASCII puro.

    - Colores en su forma corta y sin el ";" final de cada style: unos 3 KB menos por mail, que es lo que cuestan
      las entidades.
    - Todo carácter no ASCII (★, −, €, £, ·, acentos, ä, ß, espacios duros...) sale como entidad numérica
      (&#9733;). Así da igual con qué codificación se guarde, se adjunte o se copie el HTML: no puede aparecer
      ni "?" ni "\ufffd" ni "Ã©". Es el arreglo de raíz de los interrogantes (tests/test_texto.py lo vigila).
    """
    for a, b in SHORT_HEX.items():
        h = h.replace(a, b)
    h = re.sub(r'(style="[^"]*?);+"', r'\1"', h)
    return h.encode("ascii", "xmlcharrefreplace").decode("ascii")


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
    return f"Pub. {d:%d/%m}" if d < mail else f"{d:%d/%m}"


# --------------------------------------------------------------------------- ficha ampliada

BUDGET = 94_000  # bytes del HTML base; la tarea añade resumen y noticias (hasta ~3 KB), su tope es 101.000 y Gmail recorta a ~102 KB
LBL = '<td style="color:#8C877D;">{}</td><td><b>{}</b></td>'


def pct(v, dec: int = 1, sign: bool = True) -> str | None:
    if v is None:
        return None
    s = es(v, dec if abs(v) < 100 else 0)
    return (("+" if v > 0 and sign else "") + s + " %").replace("-", "−")


def num(v, dec: int = 1) -> str | None:
    return None if v is None else es(v, dec).replace("-", "−")


def rec_label(mark) -> str | None:
    if mark is None:
        return None
    for lim, txt in ((1.5, "Compra fuerte"), (2.5, "Compra"), (3.5, "Mantener"), (4.5, "Venta")):
        if mark <= lim:
            return txt
    return "Venta fuerte"


def ficha(pairs: list[tuple[str, str | None]], wide: list[tuple[str, str | None]]) -> str:
    nb = lambda v: str(v).replace(" ", "\u00a0")  # noqa: E731  (que no se partan "6.540 M $" ni "Vs. compra")
    # El valor "x % / y %" (insiders/instituciones) sí puede partirse: es el más ancho y desbordaba en móvil.
    pairs = [(nb(a), b if " / " in str(b) else nb(b)) for a, b in pairs if b not in (None, "", "—")]
    rows = []
    for i in range(0, len(pairs), 2):
        a, b = pairs[i], pairs[i + 1] if i + 1 < len(pairs) else None
        # Sin anchos en porcentaje: una columna al 22 % con "Deuda/patr." obligaba a 335 px de tabla y el mail
        # se salía de la pantalla en móviles de 360 px.
        cells = LBL.format(*a) + (LBL.format(*b) if b else "")
        rows.append(f"<tr>{cells}</tr>")
    for label, val in wide:
        if val:
            rows.append(f'<tr><td style="color:#8C877D;">{label}</td><td colspan="3"><b>{val}</b></td></tr>')
    return "".join(rows)


def ficha_rows(o: dict, eu: bool, res: str, i: dict) -> str:
    x = o.get("extra") or {}
    if eu:
        cur = {"EUR": "€", "GBP": "£"}.get(o.get("currency"), o.get("currency", ""))
        cap = f"{millions(o['market_cap'])} {cur}" if o.get("market_cap") else None
        liq = o.get("dollar_volume")
        liq = (f"{es(liq / 1e6, 1)} M€/día" if liq >= 1e6 else f"{liq / 1e3:.0f} mil €/día") if liq else None
        own = None
    else:
        cap = f"{millions(o.get('market_cap'))} $" if o.get("market_cap") else None
        dv = o.get("dollar_volume")
        liq = (f"{es(dv / 1e6, 1)} M$/día" if dv >= 1e6 else f"{dv / 1e3:.0f} mil $/día") if dv else None
        io_, inst = (str(o.get(k) or "").replace(".", ",").replace("%", " %") for k in ("insider_own", "inst_own"))
        own = " / ".join(v for v in (io_, inst) if v.strip()) or None
    ana = None
    if x.get("rec_mark") is not None:
        ana = rec_label(x["rec_mark"]) + (f" ({x['rec_total']:.0f})" if x.get("rec_total") else "")
        if x.get("target_upside") is not None:
            ana += f" · objetivo {pct(x['target_upside'], 0)}"
    pe = x.get("pe")
    pairs = [("Operación", dm(i["trade_date"])), ("Resultados", res),
             ("Cap.", cap), ("Liquidez", liq),
             ("Vs. compra", pct(o.get("chase_pct"))),
             ("Patrón", {"Primera compra en 3 años": "1ª en 3 años", "Sin historial": "Sin hist."}.get(o.get("pattern"), o.get("pattern"))),
             ("Short float", None if eu else pct(o.get("short_float_pct"), 1, False)),
             ("Insid./inst.", own),
             ("PER", num(pe) if pe is None or pe > 0 else "negativo"), ("EV/EBITDA", num(x.get("ev_ebitda"))),
             ("P/VC", num(x.get("pb"))), ("Dividendo", "No" if x.get("div_yield") == 0 else pct(x.get("div_yield"), 1, False)),
             ("Margen", pct(x.get("net_margin"))), ("ROE", pct(x.get("roe"))),
             ("Ventas a/a", pct(x.get("rev_growth"))), ("BPA a/a", pct(x.get("eps_growth"))),
             ("Deuda/patr.", num(x.get("debt_eq"), 2)), ("Beta", num(x.get("beta"), 2))]
    perf = [(k, x.get(f)) for k, f in (("1S", "perf_w"), ("1M", "perf_1m"), ("3M", "perf_3m"), ("YTD", "perf_ytd"),
                                         ("1A", "perf_y"))]
    if x.get("perf_1m") is None and o.get("perf_month"):
        perf[1] = ("1M", pct_num(o["perf_month"]))
    perf_txt = " · ".join(f"{k} {pct(v, 0 if abs(v) >= 10 else 1)}" for k, v in perf if v is not None)
    rango = " · ".join(t for t in (
        f"{pct(x['from_high'], 0 if abs(x['from_high']) >= 10 else 1)} desde máx." if x.get("from_high") is not None else "",
        f"{pct(x['from_low'], 0 if abs(x['from_low']) >= 10 else 1)} desde mín." if x.get("from_low") is not None else "",
        f"RSI {x['rsi']:.0f}" if x.get("rsi") is not None else "") if t)
    return ficha(pairs, [("Rendimiento", perf_txt), ("52 semanas", rango), ("Analistas", ana)])


def otros(o: dict, eu: bool, sym: str) -> str:
    if eu and o.get("country") == "CH":
        return ""
    seen: dict[str, list] = {}
    for i in o["insiders"]:
        name = eu_name(i["name"]) if eu else us_name(i["name"], i.get("tipo", "Persona") == "Persona")
        role = eu_role(i["title"]) if eu else us_role(i["title"])
        s = seen.setdefault(name, [role, 0])
        s[1] += i.get("value") or 0
    names = list(seen.items())[1:]
    if not names:
        return ""
    txt = "; ".join(f"{n} ({r}, {money(v, sym)})" for n, (r, v) in names[:5])
    if len(names) > 5:
        txt += f"; y {len(names) - 5} más"
    return f'<div style="font-size:11px;line-height:16px;color:#55524B;margin-top:2px;">También: {txt}</div>'


# --------------------------------------------------------------------------- render

BOTON = ('<table cellpadding="0" cellspacing="0" style="margin:16px 0 0;"><tr><td style="background:#111111;">'
         '<a href="{url}" style="display:inline-block;padding:12px 18px;font-size:13px;font-weight:bold;letter-spacing:1px;'
         'color:#F3B61F;text-decoration:none;">VER INFORME COMPLETO &rarr;</a></td></tr></table>'
         '<div style="margin-top:6px;font-size:11px;color:#8C877D;">{sub}</div>')
WEB_HEAD = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=480">'  # el móvil encoge la página para que quepa, como Gmail
            '<meta name="robots" content="noindex,nofollow,noarchive"><title>{title}</title></head>'
            '<body style="margin:0;background:#F2EEE3;">')


def render(kind: str, src: Path, web: bool = False, report_url: str | None = None) -> dict:
    """web=False: mail (con presupuesto de bytes). web=True: página completa para el informe web, sin recortes."""
    d = json.loads(src.read_text())
    url = "" if web else (os.environ.get("REPORT_URL", "") if report_url is None else report_url).strip()
    url = url.rstrip("/") + "/" if url else ""
    full_url = url + "mail.html" if url else ""  # versión web completa del mail (todas las tarjetas y descartadas)
    mail = date.fromisoformat(d["mail_date"])
    eu = kind == "eu"
    t = (ROOT / "templates" / ("mail_eu.html" if eu else "mail.html")).read_text(encoding="utf-8")
    t = re.sub(r"<!--\s*PLANTILLA.*?-->", "", t, count=1, flags=re.S)
    card, otra, drow = block(t, "OPORTUNIDAD"), block(t, "FILA_OTRA"), block(t, "FILA_DESCARTADA")
    sym = "€" if eu else "$"
    who1, whoN = ("directivo", "directivos") if eu else ("insider", "insiders")
    tag = lambda txt: f' <span style="font-weight:normal;color:#1D3E8F;font-size:9px;letter-spacing:1px;">{txt}</span>'  # noqa: E731

    # Estrellas y orden: por estrellas y, dentro de cada nivel, el orden del script.
    ops = []
    for idx, o in enumerate(d["opportunities"]):
        sc = score_eu(o) if eu else score_us(o)
        ops.append((level(sc, o["tipo"] != "Persona"), idx, sc, o))
    ops.sort(key=lambda x: (-x[0], x[1]))

    cards, rows_otra, plain, top = [], [], [], []
    for k, (n, _, sc, o) in enumerate(ops, 1):
        i = o["insiders"][0]
        person = i.get("tipo", "Persona") == "Persona"
        if eu and o["country"] == "CH":
            ins = "Directivo (nombre no publicado)"
        else:
            ins = eu_name(i["name"]) if eu else us_name(i["name"], person)
        extra_n = o["n_insiders"] - 1
        role = eu_role(i["title"]) if eu else us_role(i["title"])
        cargo = role + (f", y {extra_n} más" if extra_n > 0 else "")
        if i.get("joint"):  # la misma compra declarada también por su fondo o por otra persona vinculada
            role += " (declarada también por " + ", ".join(us_name(n, True) for n in i["joint"][:2]) + ")"
            cargo = role + (f", y {extra_n} más" if extra_n > 0 else "")
        others = otros(o, eu, sym)
        fv = o.get("float_tradingview") if eu else (o.get("float_finviz") or o.get("float_tradingview"))
        float_pct = str(round(fv)) if fv is not None else "0"
        cl = o.get("cluster_30d") or o["n_insiders"]
        tipo_badge = BLUE("Entidad vinculada" if eu else "Fondo (consejo)") if o["tipo"] != "Persona" else ""
        risk = [f.replace(" (no se puede medir si es rutinario)", "") for f in o["flags"]]
        tk = re.sub(r"[^A-Za-z0-9]", "", o["ticker"])
        news = k <= N_NEWS and not web
        m = dict(CARD_PENNY if o["penny"] else CARD_NORMAL)
        if eu:
            cur = {"EUR": "€", "GBP": "£"}.get(o["currency"], o["currency"])
            precio = price_txt(o["price"], cur)
            res = dm(o["earnings"]) if o.get("earnings") else "Sin fecha"
        else:
            precio = price_txt(o["price"], "$")
            res = us_earnings(o.get("earnings", ""), mail)
        link = i.get("sec_link") or i.get("url") or ""
        x = o.get("extra") or {}
        sector = " · ".join(v for v in (x.get("sector"), x.get("industry")) if v)
        m.update(ancla=f'<a id="c-{tk}"></a>' if web else "", posicion=f"{k:02d}", ticker=o["ticker"], compania=company(o["company"]),
                 sector=f" · {sector}" if sector else "", pais_tag=tag(o["country_es"].upper()) if eu else "",
                 estrellas=stars(n), insignia_tipo=tipo_badge, insignia_penny=PENNY if o["penny"] else "",
                 insignia_repetida=REPEAT(dm(o["ya_salio"])) if o.get("ya_salio") else "",
                 insider=ins + (f' <a href="{link}" style="color:#8C877D;font-size:11px;">Form 4</a>' if link else ""),
                 cargo=role if others else cargo, otros_insiders=others,
                 importe=money(o["total_value"], sym), precio=precio, cluster=f"{cl} {who1 if cl == 1 else whoN}",
                 float_pct=float_pct, acciones_float=millions(o.get("shs_float")).upper(),
                 ficha=ficha_rows(o, eu, res, i),
                 por_que=(sentence(o["positives"]) or "Compra en mercado abierto.")
                 + (f"{{{{noticia_favor_{tk}}}}}" if news else ""),
                 riesgo=(sentence(risk) or "Sin avisos destacados.") + (f"{{{{noticia_riesgo_{tk}}}}}" if news else ""))
        if news:
            top.append({"ticker": o["ticker"], "marker": tk, "company": o["company"],
                        "country": o.get("country_es", "EE. UU.")})
        plain.append(f"{o['ticker']} {'★' * n} · {money(o['total_value'], sym)} · {ins} ({cargo})")
        cards.append(fill(card, m))
        o_tk = f'<a href="{full_url}#c-{tk}" style="color:#111111;">{o["ticker"]}</a>' if full_url else o["ticker"]
        rows_otra.append(fill(otra, dict(o_ticker=o_tk, o_estrellas=stars(n), o_importe=m["importe"],
                                         o_float=float_pct + "%", o_insider=f"{ins}, {cargo}",
                                         o_pais=tag(o["country"]) if eu else "")))

    # Descartadas: una fila por empresa desde un mínimo (que sube si hace falta sitio para tarjetas).
    grouped: dict = {}
    for x in d["discarded"]:
        g = grouped.setdefault((x.get("country", ""), x["ticker"]), dict(x, value=0))
        g["value"] += x["value"]

    def desc_rows(floor: int) -> tuple[list[str], int]:
        big = sorted((g for g in grouped.values() if g["value"] >= floor), key=lambda g: -g["value"])
        out = []
        for g in big:
            fl = g.get("float") or "—"
            out.append(fill(drow, dict(d_ticker=company(g["company"]) if eu else g["ticker"],
                                       d_importe=money(g["value"], sym), d_float=fl.replace(".", ","),
                                       d_motivo=g["reason"], d_pais=tag(g.get("country", "")) if eu else "")))
        return out, len(grouped) - len(big)

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
    if any(o.get("extra") for o in d["opportunities"]):
        fuentes += " Ficha ampliada (valoración, márgenes, rendimiento y analistas) de TradingView."
    for note in d.get("notes") or []:
        note = re.sub(r"(https?://[^\s,]+)", r'<a href="\1" style="color:#8C877D;">anuncio</a>', note)
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
                        'style="color:#F3B61F;text-decoration:none;">Señales del día</a> · '
                        '<a href="https://www.tradingview.com/" style="color:#F2EEE3;">TradingView</a>')
    total = len(cards)
    if url:
        fuentes_html = (f'<a href="{full_url}" style="color:#F3B61F;text-decoration:none;">Versi&oacute;n web completa</a> · '
                        + fuentes_html)
        sub = "Todas las fichas y todas las descartadas, sin recortes. Se abre en el navegador."
        t = t.replace("{{boton_informe}}", BOTON.format(url=url, sub=sub))
    else:
        t = t.replace("{{boton_informe}}", "")
    allowed = {"resumen", "fuentes_noticias"} | {f"noticia_{s}_{x['marker']}" for x in top for s in ("favor", "riesgo")}

    def assemble(n_cards: int, floor: int) -> str:
        h = t.replace(card, "".join(cards[:n_cards]))
        if cards:
            h = re.sub(r"<!-- AVISO_SIN_OPORTUNIDADES.*?-->", "", h, flags=re.S)
        else:
            aviso = re.search(r"<!-- AVISO_SIN_OPORTUNIDADES[^\n]*\n(.*?)\n\s*-->", h, re.S).group(1)
            h = re.sub(r"<!-- AVISO_SIN_OPORTUNIDADES.*?-->", aviso, h, flags=re.S)
        if n_cards < total:
            h = h.replace(otra, "".join(rows_otra[n_cards:]))
        else:
            h = re.sub(r"<!-- OTRAS_OPORTUNIDADES -->.*?<!-- /OTRAS_OPORTUNIDADES -->", "", h, flags=re.S)
        drows, small = desc_rows(floor)
        h = h.replace(drow, "".join(drows))
        h = fill(h, dict(dia_semana=DIAS[mail.weekday()], fecha=f"{mail.day} de {MESES[mail.month - 1]}",
                         n_oportunidades=f"{total:02d}", n_descartadas=f"{len(grouped):02d}", aviso_fuentes=fuentes,
                         nota_descartadas=(f"Otras {small} compras de menos de {money(floor, sym)} no se muestran."
                                           + (f' <a href="{full_url}#descartadas" style="color:#8C877D;">Ver todas</a>'
                                              if full_url else "") if small else ""),
                         fuentes_enlaces=fuentes_html + "{{fuentes_noticias}}"))
        leftovers = set(re.findall(r"\{\{(\w+)\}\}", h)) - allowed
        assert not leftovers, f"Marcadores sin rellenar: {leftovers}"
        h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
        h = re.sub(r">\s+<", "><", h)
        return compact(re.sub(r"\s{2,}", " ", h).strip())

    if web:  # versión web: todo, sin presupuesto de bytes
        return {"html": web_page(assemble(total, 0), d, eu, total), "meta": {"mail_date": d["mail_date"]}}

    # Máximo de tarjetas completas que caben; si no caben todas, primero se recortan descartadas pequeñas.
    best = None
    for floor in (50_000, 100_000, 250_000):
        n_cards = total
        h = assemble(n_cards, floor)
        while len(h.encode()) > BUDGET and n_cards > 0:
            n_cards -= 1
            h = assemble(n_cards, floor)
        if best is None or n_cards > best[0]:
            best = (n_cards, floor, h)
        if n_cards == total:
            break
    n_cards, floor, t = best

    # Resumen por defecto (la tarea lo mejora con las noticias).
    if total:
        lead = [o["ticker"] for _, _, _, o in ops[:3]]
        n3 = sum(1 for n, *_ in ops if n == 3)
        default = (f"{total} {'compra pasa' if total == 1 else 'compras pasan'} los filtros"
                   + (f", {n3} con tres estrellas" if n3 else "") + f". Destacan {', '.join(lead)}.")
    else:
        default = "Hoy ninguna compra pasa los filtros."
    label = "Stonks Europa" if eu else "Stonks"
    meta = {
        "mail_date": d["mail_date"],
        "send": not d.get("holiday", False),
        "subject": f"{label} {mail:%d/%m} – {total} oportunidad{'es' if total != 1 else ''}",
        "n_opportunities": total,
        "n_cards": n_cards,
        "discarded_floor": floor,
        "news_for": top,
        "default_resumen": default,
        "alerts": alerts,
        "plain_text": f"{label} {mail:%d/%m}: {total} oportunidad{'es' if total != 1 else ''}.\n" + "\n".join(plain)
                      + ("\nAvisos: " + " · ".join(alerts) if alerts else ""),
        "html_bytes": len(t.encode()),
        "max_bytes": 101_000,
        "report_url": url,
        "web_url": full_url,
    }
    return {"html": t, "meta": meta}


def web_page(h: str, d: dict, eu: bool, total: int) -> str:
    """Envuelve el HTML del mail en una página completa: resumen por defecto, sin huecos, con noindex y anclas."""
    mail = date.fromisoformat(d["mail_date"])
    default = (f"{total} {'compra pasa' if total == 1 else 'compras pasan'} los filtros." if total
               else "Hoy ninguna compra pasa los filtros.")
    h = h.replace("{{resumen}}", default).replace("{{fuentes_noticias}}", "")
    a, b = h.rsplit(">Descartadas</div>", 1)  # el último es el título de la sección (el primero, el contador)
    h = a + '><a id="descartadas"></a>Descartadas</div>' + b
    assert "{{" not in h, "Marcadores sin rellenar en la versión web"
    title = f"{'Stonks Europa' if eu else 'Stonks'} {mail:%d/%m/%Y} – informe completo"
    return compact(WEB_HEAD.format(title=title) + h + "</body></html>")


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
