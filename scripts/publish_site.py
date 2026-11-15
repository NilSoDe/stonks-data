"""Construye el informe web del día dentro de un checkout de la rama gh-pages (GitHub Pages).

Uso:  python scripts/publish_site.py us|eu DIR_SITIO [ruta/signals.json]

Deja en DIR_SITIO/<us|eu>/<AAAA-MM-DD>/:
  index.html  lo que abre el botón "Ver informe completo" del mail: la infografía (EE. UU., si se ha generado
              y es del mismo día) o, si no la hay, la versión web completa del mail.
  mail.html   versión web completa del mail: todas las tarjetas y todas las descartadas, sin el límite de Gmail.
Todas las páginas llevan <meta name="robots" content="noindex,nofollow,noarchive"> y los informes de más de
KEEP_DAYS días se borran. Imprime dos líneas: la ruta publicada (p. ej. "us/2026-11-16/") y la huella de
index.html, que publish_site.sh usa para comprobar que GitHub Pages ya sirve justo esta versión.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_mail import DIAS, MESES, ROOT, render  # noqa: E402

KEEP_DAYS = 45
NOINDEX = '<meta name="robots" content="noindex,nofollow,noarchive">'
HOME = ('<!doctype html><html lang="es"><head><meta charset="utf-8">' + NOINDEX + "<title>Stonks</title></head>"
        '<body style="font-family:Helvetica,Arial,sans-serif;padding:40px;color:#55524B;">'
        "Informes diarios de Stonks. Se abren desde el enlace de cada correo.</body></html>")


def infografia(src: Path, mail: date) -> str | None:
    """La infografía generada junto al signals.json, solo si es la de este mismo día."""
    f = src.parent / src.name.replace("signals", "infografia").replace(".json", ".html")
    if not f.exists():
        return None
    html = f.read_text(encoding="utf-8")
    label = f"{DIAS[mail.weekday()]} {mail.day} de {MESES[mail.month - 1]}"
    if not any(f'"date_label":{json.dumps(label, ensure_ascii=a)}' in html for a in (True, False)):
        return None  # es la de otro día (hoy no se ha generado)
    if "noindex" not in html:
        html, n = re.subn(r"(<meta charset=[^>]*>)", lambda m: m.group(1) + "\n" + NOINDEX, html, count=1)
        assert n == 1, "La infografía no tiene <meta charset>"
    return html


def prune(site: Path, today: date) -> None:
    for kind in ("us", "eu"):
        for d in (site / kind).iterdir() if (site / kind).is_dir() else ():
            try:
                old = (today - date.fromisoformat(d.name)).days > KEEP_DAYS
            except ValueError:
                continue
            if old:
                shutil.rmtree(d)


def main() -> int:
    kind, site = sys.argv[1], Path(sys.argv[2])
    src = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else ROOT / "data" / ("eu" if kind == "eu" else "") / "signals.json"
    mail = date.fromisoformat(json.loads(src.read_text())["mail_date"])
    web = render(kind, src, web=True)["html"]
    index = (infografia(src, mail) if kind == "us" else None) or web
    build = hashlib.sha256(index.encode()).hexdigest()[:16]
    index = index.replace(NOINDEX, NOINDEX + f'<meta name="stonks-build" content="{build}">', 1)

    out = site / kind / mail.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(index, encoding="utf-8")
    (out / "mail.html").write_text(web, encoding="utf-8")
    (site / ".nojekyll").write_text("")
    (site / "index.html").write_text(HOME, encoding="utf-8")
    prune(site, date.today())
    print(f"{kind}/{mail.isoformat()}/")
    print(build)
    return 0


if __name__ == "__main__":
    sys.exit(main())
