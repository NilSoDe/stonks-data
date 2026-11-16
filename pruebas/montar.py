import json, re, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import render_mail
T = [(1, "us", "data/historico/signals-2026-09-15.json", "[TEST 1/3 – 2026-09-15] Stonks EE. UU."),
     (2, "eu", "data/eu/historico/signals-2026-10-28.json", "[TEST 2/3 – 2026-10-28] Stonks Europa"),
     (3, "us", "data/historico/signals-2026-08-18.json", "[TEST 3/3 – 2026-08-18] Stonks EE. UU. (respaldo SEC)")]
for n, kind, src, subject in T:
    d = json.loads(Path(src).read_text())
    url = f"https://nilsode.github.io/stonks-data/{kind}/{d['mail_date']}/"
    out = render_mail.render(kind, Path(src), report_url=url)
    h, m = out["html"], out["meta"]
    y, mo, da = d["mail_date"].split("-")
    h = h.replace("{{resumen}}", f"{m['default_resumen']} Correo de PRUEBA {n}/3 con datos reales del {da}/{mo}/{y} (sin noticias)."
                  + (" Generado con OpenInsider apagado a prop&#243;sito: las compras salen directamente de la SEC (EDGAR)." if n == 3 else ""))
    h = re.sub(r"\{\{(noticia_\w+|fuentes_noticias)\}\}", "", h)
    assert "{{" not in h and all(ord(c) < 128 for c in h), n
    old = Path(f"pruebas/test{n}.html").read_text()
    Path(f"pruebas/test{n}.html").write_text(h)
    meta = {"subject": subject, "plain_text": subject + "\n" + m["plain_text"] + f"\nInforme completo: {url}", "bytes": len(h.encode()),
            "report_url": url, "n_opportunities": m["n_opportunities"], "n_cards": m["n_cards"]}
    Path(f"pruebas/test{n}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(n, kind, d["mail_date"], "bytes", len(h.encode()), "antes", len(old.encode()), "iguales" if old == h else "cambia",
          "| opp", m["n_opportunities"], "tarjetas", m["n_cards"], "| fuente", d.get("source"), "| avisos", len(d.get("alerts", [])))
