# stonks-data

Descarga automática de las compras de insiders publicadas en [OpenInsider](http://openinsider.com), de lunes a viernes por la tarde (hora de Madrid). Alimenta el mail diario de oportunidades de compra.

**Respaldo:** si OpenInsider falla o no devuelve compras, `scripts/sec_edgar.py` lee directamente los Form 4 de la SEC (índice diario de EDGAR + "Latest filings") y genera las mismas filas. Si la SEC no publicó ningún Form 4 en el periodo (festivo en EE. UU.), `signals.json` lleva `"holiday": true` y el mail no se envía. Para probar el respaldo: lanzar el workflow con `force_sec = 1`.

| Archivo | Contenido |
|---|---|
| `data/latest.md` | Tablas de la última descarga, legibles |
| `data/latest.json` | Las mismas filas en JSON |
| `data/status.json` | Qué fuentes funcionaron y cuántas filas trajeron |
| `data/archive/` | Copia de cada día |

Para lanzarlo a mano: pestaña **Actions** → **Descargar OpenInsider** → **Run workflow**.

Solo contiene datos públicos. No es asesoramiento financiero.

## Stonks Europa

`scripts/signals_eu.py` genera `data/eu/signals.json` cada mañana con las compras de directivos de seis países:

| País | Fuente | Notas |
|---|---|---|
| Alemania | BaFin (CSV por persona) | Con historial del directivo (rutinario u oportunista) |
| Suiza | SIX (JSON) | Sin nombres ni fecha de aviso; los avisos ya vistos se guardan en `data/eu/seen_ch.json` |
| Francia | AMF (API + PDF) | Un PDF por notificación |
| España | CNMV (listado + PDF) | Formulario estándar; las compras de personas vinculadas se marcan |
| Italia | eMarket Storage (categoría Internal Dealing + PDF) | Modelo MAR bilingüe; las compras de sociedades o personas vinculadas se marcan |
| Reino Unido | RNS vía Investegate | Texto libre: se leen los anuncios con el formulario MAR estándar; los de formato raro salen en `notes` |

Float, capitalización y liquidez salen de TradingView por ISIN. Mínimos: 50.000 € por directivo (100.000 € en cluster), float 30%, capitalización 45 M€, liquidez 400.000 € al día. Plantilla del mail: `templates/mail_eu.html`. Workflow: `.github/workflows/fetch_eu.yml`.

## Flujo diario (optimizado)

1. GitHub Actions (tres intentos por mercado; si el primero deja las señales completas, los siguientes terminan en segundos):
   - EE. UU.: 03:23, 03:53 y 04:29 UTC → `scripts/signals.py` → `scripts/render_mail.py us`
   - Europa: 03:41, 04:13 y 04:47 UTC → `scripts/signals_eu.py` → `scripts/render_mail.py eu`
2. `render_mail.py` deja el mail ya hecho: `data/mail.html` + `data/mail_meta.json` (y `data/eu/...`). Estrellas por puntuación fija, formato español, descartadas agrupadas.
3. Las tareas programadas de Claude (08:17 y 08:47, Madrid) solo rellenan `{{resumen}}`, las noticias de las 3 primeras (`{{noticia_favor_X}}`, `{{noticia_riesgo_X}}`, `{{fuentes_noticias}}`) comprueban que el mail no pasa de 101 KB y envían.

### Ficha ampliada y tamaño del mail

- `scripts/enrich.py` añade a cada oportunidad (`o["extra"]`) datos de TradingView: sector e industria, PER, EV/EBITDA, P/VC, dividendo, margen neto, ROE, deuda/patrimonio, crecimiento de ventas y BPA, rendimiento (1S, 1M, 3M, YTD, 1A), distancia a máximos y mínimos de 52 semanas, RSI, beta, consenso de analistas y potencial hasta el precio objetivo. Si TradingView falla, el mail sale igual sin la ficha.
- Cada tarjeta muestra además los otros insiders de la compra (nombre, cargo, importe) y el enlace al Form 4.
- Gmail recorta los mails de más de ~102 KB de HTML. `render_mail.py` mete todas las tarjetas completas que caben en 92 KB (`BUDGET`) y deja el resto como filas en "Otras oportunidades"; si no caben todas, sube el mínimo de las descartadas (50.000 → 100.000 → 250.000) antes de quitar tarjetas. `mail_meta.json` indica `n_cards`, `discarded_floor`, `html_bytes` y `max_bytes`.

### Infografía "Radar de insiders" (EE. UU.)

- `scripts/render_infografia.py` rellena el bloque JSON (`<script id="report-data">`) de `templates/stonks_infografia.html` con las señales del día y deja `data/infografia.html` (en modo histórico, `data/historico/infografia-AAAA-MM-DD.html`). No toca CSS, JS ni estructura de la plantilla.
- Es una página para abrir en el navegador (no va en el cuerpo del mail). El script la deja pre-dibujada con un Chrome sin ventana (playwright), así que también se ve donde no se ejecuta JavaScript, como la vista previa de adjuntos del móvil; con JavaScript añade la interacción.
- Las oportunidades sin ficha ampliada van a "Otras oportunidades"; si no hay ninguna con ficha (o TradingView falla), ese día no se genera.
- Controles: `cards + others == opportunities`, `discarded + discarded_hidden == meta.discarded`, `three_star` y `highlights` coherentes (el script falla si no cuadran, y el mail sale igual sin infografía).
- El archivo sale en ASCII puro (entidades HTML y escapes \uXXXX): Gmail guarda los adjuntos .html en Latin-1 y convertía −, ★, ≥ o → en "?".
- Diseño actual: página con scroll vertical, tipografía Helvetica y paleta pastel monocromática en dos tonos opuestos (turquesa = señales buenas, coral = riesgo). La versión anterior, horizontal y oscura, se conserva en `templates/stonks_infografia_oscura.html` (no la usa el script).

## Informe web (GitHub Pages)

Gmail recorta los mails de más de ~102 KB y no previsualiza bien los adjuntos `.html`, así que el informe completo se publica como página web y el mail lleva un botón **Ver informe completo**.

- `scripts/publish_site.sh` (paso "Publicar el informe web" de los dos workflows) deja el informe del día en la rama `gh-pages`, que GitHub Pages sirve en `https://nilsode.github.io/stonks-data/<us|eu>/<AAAA-MM-DD>/`:
  - `index.html`: lo que abre el botón. En EE. UU. es la infografía; si ese día no se ha generado (y siempre en Europa), la versión web completa del mail.
  - `mail.html`: versión web completa del mail, con **todas** las tarjetas y **todas** las descartadas, sin el presupuesto de bytes. Cada tarjeta tiene el ancla `#c-TICKER`.
- El paso comprueba que la página ya responde con la versión recién publicada y solo entonces pasa `REPORT_URL` a `render_mail.py`. Si Pages falla o está desactivado, el mail sale como antes, sin botón: nunca se manda un enlace roto. `mail_meta.json` indica `report_url` y `web_url`.
- En el mail: botón bajo el resumen, cada fila de "Otras oportunidades" enlaza a su tarjeta completa, "Ver todas" en la nota de descartadas y "Versión web completa" en el pie. El cuerpo sigue con el mismo presupuesto (`BUDGET` 92 KB, tope 101 KB tras añadir resumen y noticias).
- Privacidad: todas las páginas llevan `noindex,nofollow,noarchive`, no hay índice de informes y los de más de 45 días se borran (`KEEP_DAYS` en `publish_site.py`). El repositorio es público, así que la dirección no es secreta: solo se evita que salga en buscadores.
- Las tareas programadas que envían el mail no cambian: el botón ya viene dentro de `data/mail.html`.
