# stonks-data

Descarga automática de las compras de insiders publicadas en [OpenInsider](http://openinsider.com), de lunes a viernes por la tarde (hora de Madrid). Alimenta el mail diario de oportunidades de compra.

| Archivo | Contenido |
|---|---|
| `data/latest.md` | Tablas de la última descarga, legibles |
| `data/latest.json` | Las mismas filas en JSON |
| `data/status.json` | Qué fuentes funcionaron y cuántas filas trajeron |
| `data/archive/` | Copia de cada día |

Para lanzarlo a mano: pestaña **Actions** → **Descargar OpenInsider** → **Run workflow**.

Solo contiene datos públicos. No es asesoramiento financiero.

## Stonks Europa

`scripts/signals_eu.py` genera `data/eu/signals.json` cada mañana con las compras de directivos de cinco países:

| País | Fuente | Notas |
|---|---|---|
| Alemania | BaFin (CSV por persona) | Con historial del directivo (rutinario u oportunista) |
| Suiza | SIX (JSON) | Sin nombres ni fecha de aviso; los avisos ya vistos se guardan en `data/eu/seen_ch.json` |
| Francia | AMF (API + PDF) | Un PDF por notificación |
| España | CNMV (listado + PDF) | Formulario estándar; las compras de personas vinculadas se marcan |
| Reino Unido | RNS vía Investegate | Texto libre: se leen los anuncios con el formulario MAR estándar; los de formato raro salen en `notes` |

Float, capitalización y liquidez salen de TradingView por ISIN. Mínimos: 50.000 € por directivo (100.000 € en cluster), float 30%, capitalización 45 M€, liquidez 400.000 € al día. Plantilla del mail: `templates/mail_eu.html`. Workflow: `.github/workflows/fetch_eu.yml`.
