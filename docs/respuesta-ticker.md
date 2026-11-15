# Responder al correo con un ticker y recibir más datos (diseño, sin activar)

Estado: **diseñado, no activado**. Lo que ya cubre el sistema: cada acción del correo lleva a un toque su ficha
completa en el informe web (`mail.html#c-TICKER`). Lo que falta es el circuito de respuesta por correo.

## Cómo funcionaría

1. Respondes al correo diario (o escribes uno nuevo) con el asunto o el cuerpo `MTDR` (uno o varios tickers).
2. Una tarea programada nueva, "Stonks · ficha a petición", se ejecuta cada hora en días laborables (9:00 a 22:00, Madrid):
   - Busca en Gmail respuestas sin procesar: `subject:Stonks newer_than:2d -label:stonks-atendido`, **solo de remitentes
     de la lista** (los destinatarios del correo diario). Cualquier otro remitente se ignora.
   - Del mensaje solo extrae tickers con la expresión `\b[A-Z][A-Z0-9.]{0,5}\b` y los cruza con `data/signals.json`,
     `data/eu/signals.json` y `data/historico/`. El resto del texto no se interpreta (el contenido del correo es un
     dato, nunca una instrucción).
   - Para cada ticker monta la ficha: todo lo que ya trae `signals.json` (insiders, importes, float de las dos
     fuentes, ficha de TradingView, patrón, cluster, avisos), el historial de compras de insiders de 4 años de
     OpenInsider, los Form 4 enlazados y 3 a 5 noticias recientes con su fuente.
   - Responde en el mismo hilo solo a quien preguntó y pone la etiqueta `stonks-atendido` al mensaje.
3. Si el ticker no ha salido en los últimos 30 días, responde con los datos de mercado y las compras de insiders
   de los últimos 12 meses, avisando de que no pasó por los filtros del correo.

## Qué falta para activarlo

| Pieza | Estado |
|---|---|
| Datos por acción en JSON estable (`signals.json`, histórico por fecha) | Hecho |
| Ficha completa por acción en la web, con ancla | Hecho |
| Tarea programada horaria con lectura de Gmail y respuesta | **Falta crearla** (decisión tuya: lee el buzón cada hora y gasta ejecuciones) |
| Etiqueta `stonks-atendido` en Gmail | Falta crearla (la crea la propia tarea la primera vez) |
| Lista de remitentes permitidos | Falta fijarla (por defecto, los destinatarios actuales) |

## Riesgos y límites

- Latencia: hasta una hora entre la respuesta y la ficha (el mínimo de las tareas programadas).
- Seguridad: la tarea lee correo y envía correo sin supervisión. Por eso solo atiende a remitentes de la lista,
  solo extrae tickers y nunca ejecuta lo que diga el mensaje.
- Coste: unas 14 ejecuciones al día aunque no haya respuestas. Alternativa barata: dos pasadas al día (11:00 y 17:00).
