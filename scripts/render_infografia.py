"""Infografía horizontal "Stonks · Radar de insiders" (EE. UU.).

Rellena el bloque JSON de templates/stonks_infografia.html con las señales del día. No toca CSS, JS ni estructura:
la plantilla calcula sola luces, mapa, gauges y agrupaciones. Es una página para abrir en el navegador (no un mail).

Uso:  python scripts/render_infografia.py [ruta/signals.json]   ->  infografia.html junto al signals.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_mail import (DIAS, MESES, ROOT, company, dm, es, level, millions, money, pct_num, price_txt,  # noqa: E402
                         rec_label, score_us, us_earnings, us_name, us_role)

FLOOR = 100_000  # la plantilla rotula "compras de menos de 100.000 $ que no se muestran"
PATTERN = {"Primera compra en 3 años": "1ª en 3 años", "Sin historial": "Sin historial"}


def r1(v):
    return None if v is None else round(float(v), 1)


def phrases(items: list[str]) -> list[str]:
    out = []
    for i in items:
        i = re.sub(r"(\d)\.(\d)", r"\1,\2", str(i).strip().rstrip("."))
        if i:
            out.append(i[0].upper() + i[1:] + ".")
    return out


def card(rank: int, n: int, o: dict, mail: date) -> dict:
    x = o["extra"]
    i = o["insiders"][0]
    seen: dict[str, list] = {}
    for p in o["insiders"]:
        s = seen.setdefault(us_name(p["name"], p.get("tipo", "Persona") == "Persona"), [us_role(p["title"]), 0])
        s[1] += p.get("value") or 0
    people = list(seen.items())
    tags = []
    if o.get("penny"):
        tags.append({"label": "PENNY STOCK", "kind": "penny"})
    if o.get("ya_salio"):
        tags.append({"label": f"YA SALIÓ EL {dm(o['ya_salio'])}", "kind": "exited"})
    if o.get("tipo") != "Persona":
        tags.append({"label": "FONDO (CONSEJO)", "kind": "fund"})
    dv = o.get("dollar_volume")
    earn = us_earnings(o.get("earnings", ""), mail).replace("Pub. ", "")
    fv = o.get("float_finviz") or o.get("float_tradingview")
    return {
        "rank": rank, "ticker": o["ticker"], "name": company(o["company"]),
        "sector": x.get("sector") or "", "industry": x.get("industry") or "",
        "stars": n, "tags": tags,
        "insider": {"name": people[0][0], "role": people[0][1][0], "form4": i.get("sec_link") or ""},
        "also": [{"name": nm, "role": role, "amount": money(v, "$")} for nm, (role, v) in people[1:6]],
        "amount_label": money(o["total_value"], "$"), "amount_usd": o["total_value"],
        "price_label": price_txt(o["price"], "$"), "cluster": int(o.get("cluster_30d") or o["n_insiders"]),
        "trade_date": dm(i["trade_date"]), "earnings": None if earn == "Sin fecha" else earn,
        "mcap_label": f"{millions(o['market_cap'])} $" if o.get("market_cap") else None,
        "liquidity_label": (f"{es(dv / 1e6, 1)} M$/día" if dv >= 1e6 else f"{dv / 1e3:.0f} mil $/día") if dv else None,
        "vs_buy": r1(o.get("chase_pct")), "pattern": PATTERN.get(o.get("pattern"), o.get("pattern")),
        "short_float": r1(o.get("short_float_pct")),
        "insider_pct": pct_num(o.get("insider_own")), "inst_pct": pct_num(o.get("inst_own")),
        "per": r1(x.get("pe")), "ev_ebitda": r1(x.get("ev_ebitda")), "pbv": r1(x.get("pb")),
        "div": r1(x.get("div_yield")) or 0,
        "margin": r1(x.get("net_margin")), "roe": r1(x.get("roe")), "sales_yoy": r1(x.get("rev_growth")),
        "eps_yoy": r1(x.get("eps_growth")), "de": x.get("debt_eq"), "beta": x.get("beta"),
        "perf": {"1S": r1(x.get("perf_w")), "1M": r1(x.get("perf_1m")), "3M": r1(x.get("perf_3m")),
                 "YTD": r1(x.get("perf_ytd")), "1A": r1(x.get("perf_y"))},
        "from_high": r1(x.get("from_high")), "from_low": r1(x.get("from_low")),
        "rsi": None if x.get("rsi") is None else round(x["rsi"]),
        "analysts": {"rating": rec_label(x.get("rec_mark")),
                     "count": None if x.get("rec_total") is None else int(x["rec_total"]),
                     "upside": None if x.get("target_upside") is None else round(x["target_upside"])},
        "float_pct": round(fv) if fv is not None else None,
        "shares_label": millions(o.get("shs_float")) if o.get("shs_float") else None,
        "pro": phrases(o.get("positives") or []) or ["Compra en mercado abierto."],
        "risk": phrases(f.replace(" (no se puede medir si es rutinario)", "") for f in o.get("flags") or []),
    }


def build(d: dict) -> dict | None:
    mail = date.fromisoformat(d["mail_date"])
    ops = []
    for idx, o in enumerate(d["opportunities"]):
        ops.append((level(score_us(o), o["tipo"] != "Persona"), idx, o))
    ops.sort(key=lambda t: (-t[0], t[1]))
    # La plantilla necesita la ficha completa para dibujar la tarjeta y el mapa; sin ella, a "otras".
    full = lambda o: bool(o.get("extra")) and o["extra"].get("from_high") is not None  # noqa: E731
    cards, others = [], []
    for n, _, o in ops:
        if full(o):
            cards.append(card(len(cards) + 1, n, o, mail))
        else:
            i = o["insiders"][0]
            fv = o.get("float_finviz") or o.get("float_tradingview")
            others.append({"ticker": o["ticker"], "stars": n, "amount_label": money(o["total_value"], "$"),
                           "float_pct": round(fv) if fv is not None else None,
                           "insider": us_name(i["name"], i.get("tipo", "Persona") == "Persona"),
                           "role": us_role(i["title"])})
    if not cards:
        return None

    grouped: dict = {}
    for x in d["discarded"]:
        g = grouped.setdefault(x["ticker"], dict(x, value=0))
        g["value"] += x["value"]
    big = sorted((g for g in grouped.values() if g["value"] >= FLOOR), key=lambda g: -g["value"])
    discarded = [{"ticker": g["ticker"], "amount_label": money(g["value"], "$"),
                  "float_label": (g.get("float") or "—").replace(".", ","), "reason": g["reason"]} for g in big]

    total = len(cards) + len(others)
    n3 = sum(1 for n, *_ in ops if n == 3)
    hl = [c["ticker"] for c in cards[:3]]
    w = d["window"]
    filed = dm(w["to"]) if w["from"] == w["to"] else f"{dm(w['from'])}–{dm(w['to'])}"
    note = (f"Form 4 publicados {'el ' + filed if w['from'] == w['to'] else 'del ' + dm(w['from']) + ' al ' + dm(w['to'])}, "
            "hora de Nueva York. Ficha ampliada (valoración, márgenes, rendimiento y analistas) de TradingView.")
    for extra in (d.get("alerts") or []) + (d.get("notes") or []):
        note += " " + extra
    meta = {
        "kicker": "Stonks · compras de insiders",
        "date_label": f"{DIAS[mail.weekday()]} {mail.day} de {MESES[mail.month - 1]}",
        "filed": filed, "opportunities": total, "three_star": n3,
        "discarded": len(grouped), "discarded_hidden": len(grouped) - len(big), "highlights": hl,
        "summary": (f"{total} {'compra pasa' if total == 1 else 'compras pasan'} los filtros"
                    + (f", {n3} con tres estrellas" if n3 else "") + f". Destacan {', '.join(hl)}."),
        "source_note": note,
        "disclaimer": "Análisis automatizado, no es asesoramiento financiero. Verifica antes de operar.",
    }
    data = {"meta": meta, "cards": cards, "others": others, "discarded": discarded}
    # Control de calidad del prompt del generador.
    assert len(cards) + len(others) == meta["opportunities"]
    assert len(discarded) + meta["discarded_hidden"] == meta["discarded"]
    assert meta["three_star"] == sum(1 for c in cards + others if c["stars"] == 3)
    assert all(any(c["ticker"] == t for c in cards) for t in hl)
    return data


def render(src: Path) -> str | None:
    data = build(json.loads(src.read_text()))
    if data is None:
        return None
    t = (ROOT / "templates" / "stonks_infografia.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out, n = re.subn(r'(<script id="report-data" type="application/json">).*?(</script>)',
                     lambda m: m.group(1) + "\n" + payload + "\n" + m.group(2), t, count=1, flags=re.S)
    assert n == 1, "La plantilla no tiene el bloque report-data"
    return out


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "signals.json"
    dst = src.parent / src.name.replace("signals", "infografia").replace(".json", ".html")
    html = render(src)
    if html is None:
        dst.unlink(missing_ok=True)
        print("Sin oportunidades con ficha: no se genera la infografía")
        return 0
    dst.write_text(html, encoding="utf-8")
    print(f"{dst}: {len(html.encode())} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
