# stonks-data

Descarga automática de las compras de insiders publicadas en [OpenInsider](http://openinsider.com), de lunes a viernes de madrugada (hora de Madrid). Alimenta el mail diario de oportunidades de compra.

**Respaldo:** si OpenInsider falla o no devuelve compras, `scripts/sec_edgar.py` lee directamente los Form 4 de la SEC (índice diario de EDGAR + "Latest filings") y genera las mismas filas. Si la SEC no publicó ningún Form 4 en el periodo (festivo en EE. UU.), `signals.json` lleva `"holiday": true` y el mail no se envía. Para probar el respaldo: lanzar el workflow con `force_sec = 1`.

| Archivo | Contenido |
|---|---|
| `data/signals.json` / `data/signals.md` | Oportunidades, descartadas, avisos y estado de las fuentes |
| `data/latest.json` | Datos en bruto del día (compras, Finviz/TradingView, Form 4) |
| `data/signals_log.json` | Historial de oportunidades (para marcar las que ya salieron) |
| `data/mail.html` / `data/mail_meta.json` | Mail ya montado y sus datos (asunto, envío, noticias) |
| `data/historico/` | Señales y mails de fechas concretas (modo histórico) |

`scripts/fetch_openinsider.py` ya no se ejecuta solo: son las funciones de descarga que usan `signals.py` y `sec_edgar.py`.

Para lanzarlo a mano: pestaña **Actions** → **Señales Stonks** → **Run workflow** (con `fecha` para un día concreto).

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
- Gmail recorta los mails de más de ~102 KB de HTML. `render_mail.py` mete todas las tarjetas completas que caben en 94 KB (`BUDGET`) y deja el resto como filas en "Otras oportunidades"; si no caben todas, sube el mínimo de las descartadas (50.000 → 100.000 → 250.000) antes de quitar tarjetas. `mail_meta.json` indica `n_cards`, `discarded_floor`, `html_bytes` y `max_bytes`.

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
- En el mail: botón bajo el resumen, cada fila de "Otras oportunidades" enlaza a su tarjeta completa, "Ver todas" en la nota de descartadas y "Versión web completa" en el pie. Presupuesto del cuerpo: `BUDGET` 94 KB, tope 101 KB tras añadir resumen y noticias.
- Privacidad: todas las páginas llevan `noindex,nofollow,noarchive`, no hay índice de informes y cada uno se borra a los 45 días de publicarse (`KEEP_DAYS` en `publish_site.py`). El repositorio es público, así que la dirección no es secreta: solo se evita que salga en buscadores.
- Las tareas programadas que envían el mail no cambian: el botón ya viene dentro de `data/mail.html`.

## Fiabilidad, pruebas y fuentes

- `scripts/net.py` es la capa de red común: reintentos con espera creciente (red, 429 y 5xx; un 4xx no se reintenta), respuestas 200 sin el contenido esperado tratadas como fallo (`expect`), caché en disco de los documentos que no cambian (Form 4, PDF de los reguladores, anuncios RNS; carpeta `STONKS_CACHE`, compartida entre los tres intentos de la mañana con `actions/cache`), decodificación sin inventar caracteres y latencias por dominio en `status.http`.
- Los mails y la versión web salen en ASCII puro (entidades numéricas), con colores cortos y sin `;` final: ningún paso intermedio puede convertir `€`, `★`, `−` o un acento en `?`. Los nombres de las fuentes se decodifican como UTF-8 y, si no lo son, como Windows-1252.
- Deduplicación: en EE. UU., la misma compra en varias filas (Form 4 repetido o declaración conjunta) cuenta una vez; en Europa, la misma operación notificada dos veces o por dos fuentes (CNMV y RNS).
- Tests sin red: `pip install -r requirements-dev.txt && python -m pytest -q` (workflow "Tests" en cada PR). Fallan si reaparece un `?`, un `\ufffd`, un `Ã©` o un `None` en el texto visible, si el cuerpo pasa del presupuesto o si se rompen los lectores.
- `python scripts/check_sources.py` (workflow "Comprobar fuentes") prueba cada fuente real y saca la tabla de `docs/fuentes.md`.
- Para probar el respaldo: workflow "Señales Stonks" con `force_sec = 1`. Para ver qué notificaciones europeas no se han podido leer: "Señales Stonks Europa" con `debug = 1`.
- Diseño de "responder con un ticker": `docs/respuesta-ticker.md` (sin activar).

## Por qué el correo no lleva enlaces a la SEC

Del 14 al 16/11/2026 el correo de EE. UU. no llegó a enviarse: la tarjeta enlazaba cada "Form 4" a `sec.gov` y el entorno desde el que la tarea programada llama a Gmail rechaza cualquier envío que junte una dirección de correo con una URL de la SEC (filtro de privacidad). La tarea terminaba "bien", pero sin mandar nada. Desde entonces:

- En el correo, "Form 4" enlaza a la tarjeta de la versión web (`mail.html#c-TICKER`), que sí lleva el documento original de la SEC. Sin informe web, sale sin enlace.
- `tests/test_texto.py::test_correo_sin_enlaces_a_la_sec` falla si vuelve a colarse un `sec.gov` en el HTML o en el texto plano del correo.
