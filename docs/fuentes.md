# Fuentes de datos: estado medido

Medido desde GitHub Actions el 15/11/2026 con `scripts/check_sources.py` (workflow "Comprobar fuentes") y con
ejecuciones reales de fechas pasadas. Las latencias diarias quedan además en `status.http` de cada `signals.json`.

| Fuente | Uso | Estado | Latencia | Formato | Límites y campos que faltan |
|---|---|---|---|---|---|
| OpenInsider (screener) | Compras de EE. UU. del periodo | OK | 0,2 a 2,9 s | HTML, tabla `tinytable` | HTTP sin TLS. A veces devuelve una página sin tabla (12/11): ahora se reintenta. No marca declaraciones conjuntas ni Form 4 repetidos: se deduplican. |
| SEC EDGAR (índice diario y Form 4) | Respaldo de OpenInsider y revisión de cada Form 4 | OK | 0,1 a 0,8 s por documento | Texto y XML, sin codificación declarada | Límite de 10 peticiones/s (se usan 8). En modo respaldo son unos 1.700 documentos y 8 minutos; hubo 53 respuestas 429/503 que se reintentaron solas. Sin historial del insider (patrón "Sin historial"). |
| Finviz | Float, capitalización, short interest | OK | 0,1 a 0,3 s | HTML | Una petición por ticker con pausa de 1,5 s (anti-bots). Un 404 ya no se reintenta. |
| TradingView scanner | Segunda fuente de float y ficha ampliada; float europeo por ISIN | OK | 0,1 s | JSON | Campos nulos en valores pequeños (float o volumen). Sin clave ni contrato: puede cambiar sin aviso. |
| BaFin (Alemania) | Compras por persona, con historial | OK | 0,2 a 0,8 s | HTML y CSV, sin codificación declarada | Filtra por fecha de operación, no de publicación (se amplía 7 días). |
| SIX (Suiza) | Avisos de operaciones de directivos | OK | 0,8 s | JSON | No publica nombre ni fecha de aviso: los ya vistos se guardan en `seen_ch.json`. |
| AMF (Francia) | API y un PDF por notificación | OK | 0,7 a 1,4 s | JSON y PDF | Solo alcanza unas semanas hacia atrás (3.000 registros). |
| CNMV (España) | Listado y PDF | OK | 1,3 s | HTML y PDF | La más lenta e inestable (3 intentos). Las entregas de acciones a precio 0 no son compras y se descartan. |
| eMarket Storage (Italia) | Categoría Internal Dealing y PDF | OK | 1,3 a 1,5 s | HTML y PDF | Pocos avisos al día (0 a 5); muchas compras son de holdings vinculadas. |
| Investegate (Reino Unido) | Anuncios RNS | OK | 0,5 a 1,1 s | HTML, texto libre | Formularios no uniformes. Algunos fondos solo dan SEDOL: el ISIN se busca por el ticker de Londres. |
| Frankfurter (BCE) | Tipos de cambio | OK | 0,2 a 0,3 s | JSON | Si falla se usan tipos fijos y ahora sale un aviso. |
| GitHub Pages | Informe web | OK | 0,1 s | HTML | Tarda alrededor de 1 minuto en servir cada publicación. |
