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
3. Las tareas programadas de Claude (08:17 y 08:47, Madrid) solo rellenan `{{resumen}}`, las noticias de las 3 primeras (`{{noticia_favor_X}}`, `{{noticia_riesgo_X}}`, `{{fuentes_noticias}}`) y envían.
