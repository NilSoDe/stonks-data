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

# Paleta pastel (la misma de la infografía): tinta verde oscuro, turquesa (bueno), coral (malo) y rosa (penny).
INK, INK2, INK3 = "#1F3436", "#4B6264", "#6F8284"
T1, T2, T3, T4, TI = "#D3EBE6", "#9AD6CC", "#59B8A6", "#2E8B7E", "#1D6A5F"
C1, C2, C3, CI = "#FBDDD4", "#F4AE9C", "#E5735E", "#A5412F"
PINK, PINK1, PINK2 = "#D96A9C", "#FBE3EE", "#F0B7D1"
LINE, OFF, BLUE_T = "#DCE8E5", "#CBD5D4", "#5B7FB8"

BADGE = ('<span style="background:{bg};color:#FFFFFF;font-size:9px;letter-spacing:1px;font-weight:bold;'
         'padding:3px 8px;border-radius:9px;white-space:nowrap;">{txt}</span>')
PENNY = BADGE.format(bg=PINK, txt="PENNY STOCK")
DESTACADA = BADGE.format(bg=T4, txt="DESTACADA")
BLUE = lambda txt: BADGE.format(bg=BLUE_T, txt=txt.upper())  # noqa: E731
REPEAT = lambda dm: (f'<span style="border:1px dashed {INK3};color:{INK3};font-size:9px;letter-spacing:1px;'  # noqa: E731
                     f'font-weight:bold;padding:2px 7px;border-radius:9px;white-space:nowrap;">YA SALIÓ EL {dm}</span>')
CARD_NORMAL = dict(estilo_tarjeta=f"background:#FFFFFF;border:1px solid {LINE};", color_numero=T4)
CARD_PENNY = dict(estilo_tarjeta=f"background:{PINK1};border:1px solid {PINK2};", color_numero=PINK)


# --------------------------------------------------------------------------- formato español

def es(n: float, dec: int = 2) -> str:
    return f"{n:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(v: float, sym: str) -> str:
    v = v or 0
    return f"{es(v / 1e6, 2)} M{sym}" if v >= 1e6 else f"{es(v, 0)} {sym}"


# Símbolo de cada divisa en el mail. El franco suizo va siempre como "CHF" (nunca "$" ni "Fr."), y una divisa sin
# símbolo conocido sale con su código ISO. Nunca se pone "$" ni "€" por defecto a algo que no lo es.
CUR_SYM = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF", "SEK": "SEK", "NOK": "NOK", "DKK": "DKK", "PLN": "PLN"}


def cur_sym(code: str | None) -> str:
    code = (code or "").upper()
    return CUR_SYM.get(code, code or "?")


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


def stars(n: int, col: str = T4) -> str:
    return f'<span style="color:{col};">{"★" * n}</span>' + (f'<span style="color:{OFF};">{"★" * (3 - n)}</span>' if n < 3 else "")


def dm(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%d/%m")
    except ValueError:
        return iso


def sentence(items: list[str]) -> str:
    # Coma decimal ("13.4%" -> "13,4%") sin tocar los miles ("792.150 €" salía como "792,150 €").
    items = [re.sub(r"(\d)\.(\d{1,2})(?!\d)", r"\1,\2", i.strip().rstrip(".")) for i in items if i and i.strip()]
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

# El orden importa: "Non-Executive Chairman" antes que "Executive Chairman" (salía "Non-presidente ejecutivo").
US_ROLES = [(r"\bNon-?\s?Exec(?:utive)? (?:COB|Chair(?:man)?)\b", "presidente no ejecutivo"),
            (r"\bExec(?:utive)? (?:COB|Chair(?:man)?)\b", "presidente ejecutivo"),
            (r"\bVice ?Chair(?:man)?\b", "vicepresidente del consejo"),
            (r"\bCOB\b|\bChair(?:man)?\b", "presidente del consejo"),
            (r"\bManaging Director\b", "director general"), (r"\bDirector-by-Deputization\b", "consejero por delegación"),
            (r"\bDirector Emeritus\b", "consejero emérito"), (r"\bDir(?:ector)?\b", "consejero"),
            (r"\b10%", "accionista del 10%"), (r"\bVice ?Pres(?:ident)?\b", "vicepresidente"),
            (r"\bPres\b|\bPresident\b", "presidente"),
            (r"\bVP\b", "VP"), (r"\bGC\b", "director jurídico"), (r"\b(?:Corp )?Secy?\b|\b(?:Corp )?Secretary\b", "secretario"),
            (r"\bTreas(?:urer)?\b", "tesorero"), (r"\band\b|\s&\s", " y ")]


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
        out.append(re.sub(r"\s+", " ", part).strip())
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
N_FULL = 3  # en el correo, las 3 primeras tarjetas llevan todos los ratios en casillas; el resto, en una línea


def corta(txt: str, urls: bool = False) -> str:
    """Recorta las tiras sin espacios de más de 40 caracteres (errores con URLs, rutas...) para que no ensanchen
    el mail ni el informe web en el móvil. Con urls=True respeta las que empiezan por http (luego se enlazan)."""
    pat = r"(?<!\S)(?!https?://)\S{41,}" if urls else r"\S{41,}"
    return re.sub(pat, lambda m: m.group(0)[:38] + "…", str(txt or ""))


def pct(v, dec: int = 1, sign: bool = True) -> str | None:
    if v is None:
        return None
    d = dec if abs(v) < 100 else 0
    if round(v, d) == 0:
        v = 0  # sin "+0 %" ni "−0 %"
    s = es(v, d)
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


# --------------------------------------------------------------------------- piezas de la tarjeta (estilo pastel)

def bar(w: float, color: str) -> str:
    w = max(3, min(100, w))
    return (f'<div style="background:{LINE};border-radius:4px;height:7px;font-size:0;line-height:0;">'
            f'<div style="background:{color};width:{w:.0f}%;height:7px;border-radius:4px;">&nbsp;</div></div>')


def brow(label: str, w: float, color: str, val: str) -> str:
    return (f'<tr><td width="86" style="font-size:11px;color:{INK3};padding:3px 0;">{label}</td>'
            f'<td style="padding:3px 8px;">{bar(w, color)}</td>'
            f'<td width="62" align="right" style="font-size:12px;font-weight:bold;padding:3px 0;white-space:nowrap;">{val}</td></tr>')


def pill(label: str, val: str, good: bool | None) -> str:
    bg, bd, col = (T1, T2, TI) if good else (C1, C2, CI) if good is False else ("#FFFFFF", LINE, INK)
    return (f'<td width="33%" style="background:{bg};border:1px solid {bd};border-radius:12px;padding:6px 7px;">'
            f'<div style="font-size:8px;letter-spacing:1px;color:{INK3};font-weight:bold;">{label}</div>'
            f'<div style="font-size:15px;font-weight:bold;color:{col};white-space:nowrap;">{val}</div></td>')


def tile(label: str, val: str) -> str:
    return (f'<td width="25%" style="background:#FFFFFF;border:1px solid {LINE};border-radius:9px;padding:4px 7px;">'
            f'<div style="font-size:8px;letter-spacing:1px;color:{INK3};">{label}</div>'
            f'<div style="font-size:13px;font-weight:bold;white-space:nowrap;">{val}</div></td>')


def lines(items: list[str], dot: str, tail: str = "") -> str:
    """Una línea por motivo con un punto de color; tail (el hueco de la noticia) va pegado a la última."""
    out = []
    for k, t in enumerate(items):
        out.append(f'<div style="font-size:12px;line-height:18px;color:{INK2};"><span style="color:{dot};">&#9679;</span>'
                   f'&nbsp; {t}{tail if k == len(items) - 1 else ""}</div>')
    return "".join(out)


def phrases(items: list[str]) -> list[str]:
    out = []
    for i in items:
        i = re.sub(r"(\d)\.(\d{1,2})(?!\d)", r"\1,\2", str(i or "").strip().rstrip("."))
        if i:
            out.append(i[0].upper() + i[1:] + ".")
    return out


def ficha_rows(o: dict, eu: bool, res: str, i: dict, full: bool, cluster: str) -> dict:
    """Devuelve las piezas de la tarjeta: tercera cifra, ficha (barras, rendimiento, ratios y float) y pie."""
    x = o.get("extra") or {}
    if eu:
        # Capitalización en la divisa de cotización (la del precio). "market_cap" va en EUR (para los filtros): si
        # falta la local, se enseña esa, rotulada en euros. Antes salía la cifra en euros con el símbolo local.
        cur = cur_sym(o.get("currency"))
        if o.get("market_cap_local"):
            cap = f"{millions(o['market_cap_local'])} {cur}"
        else:
            cap = f"{millions(o['market_cap'])} €" if o.get("market_cap") else None
        tcur = cur_sym(x.get("tv_currency") or o.get("currency"))
        liq = o.get("dollar_volume")
        liq = (f"{es(liq / 1e6, 1)} M€/día" if liq >= 1e6 else f"{liq / 1e3:.0f} mil €/día") if liq else None
        own = None
    else:
        cap = f"{millions(o.get('market_cap'))} $" if o.get("market_cap") else None
        tcur = "$"
        dv = o.get("dollar_volume")
        liq = (f"{es(dv / 1e6, 1)} M$/día" if dv >= 1e6 else f"{dv / 1e3:.0f} mil $/día") if dv else None
        io_, inst = (pct(pct_num(o.get(k)), 1 if k == "insider_own" else 0, False) for k in ("insider_own", "inst_own"))
        own = (f"INSID. {io_}" if io_ else "") + (" / " if io_ and inst else "") + (f"INST. {inst}" if inst else "")
    ch = o.get("chase_pct")
    tercera = (pill("VS. COMPRA", pct(ch), ch <= 0) if ch is not None
               else pill("CLUSTER 30D", cluster, None))

    rows = []
    up = x.get("target_upside")
    if up is not None:
        rows.append(brow("Objetivo analist.", up / 2, T4 if up >= 0 else C3, pct(up, 0)))
    sf = None if eu else o.get("short_float_pct")
    if sf is not None:
        rows.append(brow("Short float", sf * 2.5, "#B8C4C3", pct(sf, 1, False)))
    rsi = x.get("rsi")
    if rsi is not None:
        rows.append(brow("RSI", rsi, C3 if rsi >= 70 else T3 if rsi <= 30 else "#9FB3B1", f"{rsi:.0f}"))
    fh = x.get("from_high")
    if fh is not None:
        rows.append(brow("Máx. 52 sem.", 100 + fh, T3, pct(fh, 0 if abs(fh) >= 10 else 1)))
    h = ('<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">' + "".join(rows) + "</table>"
         if rows else "")

    perf = [(k, x.get(f)) for k, f in (("1S", "perf_w"), ("1M", "perf_1m"), ("3M", "perf_3m"), ("YTD", "perf_ytd"),
                                         ("1A", "perf_y"))]
    if x.get("perf_1m") is None and o.get("perf_month"):
        perf[1] = ("1M", pct_num(o["perf_month"]))
    if any(v is not None for _, v in perf):
        cells = ""
        for k, v in perf:
            good = v is None or round(v) >= 0
            cells += (f'<td width="20%" align="center" style="background:{T1 if good else C1};border-radius:8px;padding:4px 2px;">'
                      f'<div style="font-size:9px;color:{INK3};">{k}</div><div style="font-size:12px;font-weight:bold;'
                      f'color:{TI if good else CI};white-space:nowrap;">{pct(v, 0) if v is not None else "—"}</div></td>')
        h += ('<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;border-collapse:separate;'
              f'border-spacing:3px 0;"><tr>{cells}</tr></table>')

    pe = x.get("pe")
    ratios = [("PER", num(pe) if pe is None or pe > 0 else "negativo"), ("EV/EBITDA", num(x.get("ev_ebitda"))),
              ("P/VC", num(x.get("pb"))), ("Dividendo", "No" if x.get("div_yield") == 0 else pct(x.get("div_yield"), 1, False)),
              ("Margen", pct(x.get("net_margin"))), ("ROE", pct(x.get("roe"))),
              ("Ventas a/a", pct(x.get("rev_growth"))), ("BPA a/a", pct(x.get("eps_growth"))),
              ("Deuda/patr.", num(x.get("debt_eq"), 2)), ("Beta", num(x.get("beta"), 2)),
              ("Cap.", cap), ("Liquidez", liq)]
    ratios = [(k, v) for k, v in ratios if v not in (None, "", "—")]
    if full and ratios:
        grid = ""
        for k in range(0, len(ratios), 4):
            part = ratios[k:k + 4]
            grid += "<tr>" + "".join(tile(a.upper(), b) for a, b in part) + '<td width="25%"></td>' * (4 - len(part)) + "</tr>"
        h += ('<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;border-collapse:separate;'
              f'border-spacing:3px;">{grid}</table>')
    elif ratios:
        short = {"PER", "EV/EBITDA", "P/VC", "Dividendo", "Margen", "ROE", "Cap.", "Liquidez"}
        txt = " · ".join(f"{a} <b>{b.replace(' ', '&nbsp;')}</b>" for a, b in ratios if a in short)
        h += f'<div style="font-size:11px;line-height:17px;color:{INK2};margin-top:8px;">{txt}</div>'

    fv = o.get("float_tradingview") if eu else (o.get("float_finviz") or o.get("float_tradingview"))
    if fv is not None:
        acc = millions(o.get("shs_float")).upper()
        info = " · ".join(t for t in (f"FLOAT {round(fv)}%", f"{acc} ACC." if acc != "—" else "", own) if t)
        h += (f'<div style="font-size:9px;letter-spacing:1px;color:{INK3};margin-top:8px;font-weight:bold;">{info}</div>'
              f'<div style="margin-top:4px;">{bar(fv, "#8FA3A1")}</div>')

    ana = None
    if x.get("rec_mark") is not None:
        ana = rec_label(x["rec_mark"]) + (f" ({x['rec_total']:.0f})" if x.get("rec_total") else "")
        if x.get("target_price") and up is not None:  # objetivo con precio y divisa (misma que la cotización)
            ana += f" · objetivo {price_txt(x['target_price'], tcur).replace(' ', '&nbsp;')}"
    patron = {"Primera compra en 3 años": "1ª en 3 años", "Sin historial": "Sin hist."}.get(o.get("pattern"), o.get("pattern"))
    pie = " · ".join(f"{a} <b>{b}</b>" for a, b in (("Operación", dm(i["trade_date"])), ("Resultados", res),
                                                    ("Patrón", patron), ("Cluster", cluster), ("Analistas", ana)) if b)
    pie = (f'<div style="font-size:11px;line-height:17px;color:{INK3};margin-top:8px;border-top:1px solid {LINE};'
           f'padding-top:6px;">{pie}</div>')
    return {"tercera": tercera, "ficha": h, "pie": pie}


def mapa(ops: list, sym: str, img: str, eu: bool) -> str:
    """Bloque "Top importe" (las 5 mayores compras) y, si hay infografía, la imagen de su mapa."""
    top = sorted((o for *_, o in ops), key=lambda o: -o["total_value"])[:5]
    if not top:
        return ""
    mx = top[0]["total_value"] or 1
    rows = "".join(
        f'<tr><td width="58" style="font-size:12px;font-weight:bold;padding:3px 0;white-space:nowrap;">{o["ticker"]}</td>'
        f'<td style="padding:3px 8px;">{bar(o["total_value"] / mx * 100, T3)}</td>'
        f'<td width="74" align="right" style="font-size:12px;padding:3px 0;white-space:nowrap;">{money(o["total_value"], sym)}</td></tr>'
        for o in top)
    pic = ""
    if img:
        pic = (f'<div style="font-size:11px;line-height:16px;color:{INK3};margin-bottom:8px;">Cada burbuja es una ficha. '
               "Tamaño = importe; color = luz de la ficha (turquesa ★★★, azul-turquesa ★★, rosa penny, gris ya salió). "
               "Arriba a la izquierda, lo más castigado con más recorrido según analistas.</div>"
               f'<img src="{img}" alt="Mapa de oportunidades" width="100%" style="display:block;width:100%;height:auto;'
               f'border:0;border-radius:12px;margin-bottom:12px;">')
    title = "Mapa de oportunidades" if img else "Top importe"
    return (f'<div style="font-size:11px;font-weight:bold;letter-spacing:3px;text-transform:uppercase;color:{INK2};'
            f'margin:22px 0 10px;"><span style="display:inline-block;width:18px;height:3px;background:{C3};'
            f'vertical-align:middle;margin-right:8px;"></span>{title}</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid {LINE};'
            f'border-radius:22px;"><tr><td style="padding:14px;">{pic}'
            + (f'<div style="font-size:10px;letter-spacing:2px;color:{INK3};font-weight:bold;margin-bottom:4px;">TOP IMPORTE</div>'
               if img else "")
            + f'<table width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr></table>')


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
    return f'<div style="font-size:11px;line-height:16px;color:{INK2};margin-top:3px;">También: {txt}</div>'


# --------------------------------------------------------------------------- render

BOTON = (f'<table cellpadding="0" cellspacing="0" style="margin-top:16px;"><tr><td style="background:{T4};border-radius:12px;">'
         '<a href="{url}" style="display:inline-block;padding:13px 20px;font-size:13px;font-weight:bold;letter-spacing:1px;'
         'color:#FFFFFF;text-decoration:none;">VER INFORME COMPLETO &rarr;</a></td></tr></table>'
         f'<div style="margin-top:6px;font-size:11px;color:{INK3};">{{sub}}</div>')
WEB_HEAD = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow,noarchive"><title>{title}</title></head>'
            '<body style="margin:0;background:#EEF4F2;">')


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
    # Con &nbsp;: al compactar, un espacio normal entre </b> y <span> se perdía y salía "VINCIFR".
    tag = lambda txt: f'&nbsp;<span style="font-weight:normal;color:{BLUE_T};font-size:9px;letter-spacing:1px;">{txt}</span>'  # noqa: E731
    info = infografia_del_dia(src, mail) if not eu else None
    jpg = info.with_suffix(".jpg") if info else None
    img = ("mapa.jpg" if web else f"{url}mapa.jpg" if url else "") if jpg and jpg.exists() else ""

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
        float_pct = str(round(fv)) if fv is not None else "—"
        cl = o.get("cluster_30d") or o["n_insiders"]
        tipo_badge = BLUE("Entidad vinculada" if eu else "Fondo (consejo)") if o["tipo"] != "Persona" else ""
        risk = [f.replace(" (no se puede medir si es rutinario)", "") for f in o["flags"]]
        tk = re.sub(r"[^A-Za-z0-9]", "", o["ticker"])
        news = k <= N_NEWS and not web
        m = dict(CARD_PENNY if o["penny"] else CARD_NORMAL)
        if o.get("ya_salio") and not o["penny"]:
            m["color_numero"] = INK3
        acc = m["color_numero"]
        badges = [DESTACADA if k <= 3 and not o.get("ya_salio") else "", tipo_badge, PENNY if o["penny"] else "",
                  REPEAT(dm(o["ya_salio"])) if o.get("ya_salio") else ""]
        cluster = f"{cl} {who1 if cl == 1 else whoN}"
        if eu:
            precio = price_txt(o["price"], cur_sym(o["currency"]))
            res = dm(o["earnings"]) if o.get("earnings") else "Sin fecha"
        else:
            precio = price_txt(o["price"], "$")
            res = us_earnings(o.get("earnings", ""), mail)
        link = (i.get("sec_link") or i.get("url") or "").replace("http://www.sec.gov", "https://www.sec.gov")
        if not web and "sec.gov" in link:
            # En el CORREO no puede ir ningún enlace a sec.gov: el entorno desde el que la tarea envía con Gmail rechaza
            # la llamada (filtro de privacidad: dirección de correo junto a una URL de la SEC) y el mail no sale.
            # El enlace lleva a la tarjeta de la versión web, que sí trae el Form 4 original.
            link = f"{full_url}#c-{tk}" if full_url else ""
        x = o.get("extra") or {}
        sector = " · ".join(v for v in (x.get("sector"), x.get("industry")) if v)
        m.update(ancla=f'<a id="c-{tk}"></a>' if web else "", posicion=f"{k:02d}", ticker=o["ticker"], compania=company(o["company"]),
                 sector=f" · {sector}" if sector else "", pais_tag=tag(o["country_es"].upper()) if eu else "",
                 estrellas=stars(n, acc), insignias="&nbsp; ".join(b for b in badges if b),
                 insider=f"<b>{ins}</b>" + (f'&nbsp; <a href="{link}" style="color:{INK3};font-size:11px;">Form 4</a>' if link else ""),
                 cargo=role if others else cargo, otros_insiders=others,
                 importe=money(o["total_value"], sym), precio=precio,
                 **ficha_rows(o, eu, res, i, web or k <= N_FULL, cluster),
                 por_que=lines(phrases(o["positives"]) or ["Compra en mercado abierto."], T3,
                               f"{{{{noticia_favor_{tk}}}}}" if news else ""),
                 riesgo=lines(phrases(risk), C3, f"{{{{noticia_riesgo_{tk}}}}}" if news else "") if risk
                 else lines(["Sin avisos destacados."], OFF, f"{{{{noticia_riesgo_{tk}}}}}" if news else ""))
        if news:
            top.append({"ticker": o["ticker"], "marker": tk, "company": o["company"],
                        "country": o.get("country_es", "EE. UU."),
                        # Para la tarea: divisa de cotización (precio, capitalización, objetivo) y de los importes.
                        "currency": o.get("currency") or "USD", "currency_symbol": cur_sym(o.get("currency") or "USD"),
                        "amounts_currency": "EUR" if eu else "USD"})
        plain.append(f"{o['ticker']} {'★' * n} · {money(o['total_value'], sym)} · {ins} ({cargo})")
        cards.append(fill(card, m))
        o_tk = f'<a href="{full_url}#c-{tk}" style="color:{INK};">{o["ticker"]}</a>' if full_url else o["ticker"]
        rows_otra.append(fill(otra, dict(o_ticker=o_tk, o_estrellas=stars(n, acc), o_importe=m["importe"].replace(" ", "&nbsp;"),
                                         o_float=float_pct + "%", o_insider=f"{ins} ({cargo})",
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
            fl = (g.get("float") or "—").replace(" ", "")
            out.append(fill(drow, dict(d_ticker=company(g["company"]) if eu else g["ticker"],
                                       d_importe=money(g["value"], sym).replace(" ", "&nbsp;"), d_float=fl.replace(".", ","),
                                       d_motivo=g["reason"], d_pais=tag(g.get("country", "")) if eu else "")))
        return out, len(grouped) - len(big)

    # Avisos y fuentes.
    alerts = [corta(a) for a in d.get("alerts") or []]
    if alerts:
        t = t.replace("{{aviso_errores}}", " · ".join(alerts))
    else:
        t = re.sub(r'<p style="[^"]*">\{\{aviso_errores\}\}</p>', "", t)
    w = d["window"]
    period = (f"{dm(w['from'])}" if w["from"] == w["to"] else f"del {dm(w['from'])} al {dm(w['to'])}")
    if eu:
        fuentes = (f"Registros publicados {'el ' if w['from'] == w['to'] else ''}{period}. Float de TradingView "
                   "(mínimo 30%), importes por directivo desde 50.000 €. Importes, liquidez y descartadas en euros "
                   "(tipo del BCE del día); precio, capitalización y objetivo en la divisa de cotización.")
        if any(o["country"] == "CH" for o in d["opportunities"]):
            fuentes += " SIX (Suiza) no publica nombres ni fecha de aviso."
    else:
        fuentes = f"Form 4 publicados {'el ' if w['from'] == w['to'] else ''}{period}, hora de Nueva York."
    if any(o.get("extra") for o in d["opportunities"]):
        fuentes += " Ficha ampliada (valoración, márgenes, rendimiento y analistas) de TradingView."
    for note in d.get("notes") or []:
        note = corta(note, urls=True)
        note = re.sub(r"(https?://[^\s,]+)", rf'<a href="\1" style="color:{INK3};">anuncio</a>', note)
        fuentes += " " + note
    if eu:
        links = [("BaFin", "https://portal.mvp.bafin.de/database/DealingsInfo/"),
                 ("SIX", "https://www.ser-ag.com/en/topics/management-transactions.html"),
                 ("AMF", "https://bdif.amf-france.org/"),
                 ("CNMV", "https://internet.cnmv.es/Portal/Consultas/Directivos-Consulta?lang=es"),
                 ("eMarket Storage", "https://www.emarketstorage.it/it/comunicati-finanziari"),
                 ("RNS", "https://www.investegate.co.uk/category/directors-dealings"),
                 ("TradingView", "https://www.tradingview.com/")]
        fuentes_html = " · ".join(f'<a href="{u}" style="color:{INK3};">{n}</a>' for n, u in links)
    else:
        fuentes_html = ('<a href="https://github.com/NilSoDe/stonks-data/blob/main/data/signals.md" '
                        f'style="color:{TI};text-decoration:none;">Señales del día</a> · '
                        f'<a href="https://www.tradingview.com/" style="color:{INK3};">TradingView</a>')
    total = len(cards)
    if url:
        fuentes_html = (f'<a href="{full_url}" style="color:{TI};font-weight:bold;text-decoration:none;">Versi&oacute;n web completa</a> · '
                        + fuentes_html)
        sub = ("La infografía con todas las fichas y el mapa interactivo. Se abre en el navegador." if info
               else "Todas las fichas y todas las descartadas, sin recortes. Se abre en el navegador.")
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
                         n_oportunidades=str(total), n_tres=str(sum(1 for n, *_ in ops if n == 3)),
                         n_descartadas=str(len(grouped)), aviso_fuentes=fuentes, mapa=mapa(ops, sym, img, eu),
                         nota_descartadas=(f"Otras {small} compras de menos de {money(floor, sym)} no se muestran."
                                           + (f' <a href="{full_url}#descartadas" style="color:{INK3};">Ver todas</a>'
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
    # Red de seguridad: si algún texto trajera un enlace a sec.gov, se queda el texto sin enlace (ver arriba).
    t = re.sub(r'<a href="https?://[^"]*sec\.gov[^"]*"[^>]*>(.*?)</a>', r"\1", t)

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
        # Divisas: los importes del mail van en "amounts_currency"; precio, capitalización y objetivo en la divisa de
        # cada acción (news_for[].currency). fx_eur: unidades de cada divisa por 1 EUR (BCE), para convertir con "≈".
        "amounts_currency": "EUR" if eu else "USD",
        "fx_eur": d.get("fx_eur") or {},
        "plain_text": f"{label} {mail:%d/%m}: {total} oportunidad{'es' if total != 1 else ''}.\n" + "\n".join(plain)
                      + ("\nAvisos: " + " · ".join(alerts) if alerts else ""),
        "html_bytes": len(t.encode()),
        "max_bytes": 101_000,
        "report_url": url,
        "web_url": full_url,
    }
    # Infografía para adjuntar (solo EE. UU.): la del mismo día, junto al signals.json. La tarea programada la adjunta.
    if info:
        ih = info.read_text(encoding="utf-8")
        if "sec.gov" not in ih:
            meta.update(infografia=str(info.relative_to(ROOT)) if info.is_relative_to(ROOT) else str(info),
                        infografia_bytes=len(ih.encode()), infografia_nombre=f"stonks_infografia_{mail:%d-%m}.html")
    return {"html": t, "meta": meta}


def infografia_del_dia(src: Path, mail: date) -> Path | None:
    """La infografía generada junto al signals.json, solo si es la de ese mismo día."""
    f = src.parent / src.name.replace("signals", "infografia").replace(".json", ".html")
    if not f.exists():
        return None
    lab = f"{DIAS[mail.weekday()]} {mail.day} de {MESES[mail.month - 1]}"
    h = f.read_text(encoding="utf-8")
    return f if any(f'"date_label":{json.dumps(lab, ensure_ascii=a)}' in h for a in (True, False)) else None


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
