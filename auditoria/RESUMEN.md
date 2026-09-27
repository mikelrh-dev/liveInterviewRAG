# Resumen en una página

Auditoría del 2026-09-27 sobre InterviewTTS, 7 capas, 118 hallazgos. Si solo vas a leer un fichero de esta carpeta, es este.

## El veredicto

- **El riesgo que anula todo lo demás: no hay TLS.** Ninguna capa cifra el tráfico. Se arregla con configuración, no con código, y hasta que esté no tiene sentido invertir en el resto de la seguridad.
- Reparto: **1 CRÍTICO · 16 ALTO · 68 MEDIO · 33 BAJO.** Los 17 primeros son los que importan. Los 101 restantes son contexto, no una lista de trabajo.
- La infraestructura de seguridad del código (SQL, ficheros estáticos, XSS) está limpia. El problema es la **capa de despliegue**, no la lógica.
- La suite de tests pasa entera (258/258) y la capa de servicios de `backend/` está bien diseñada. El daño está en cómo se cose: `main.py` (1035 líneas) y `app.js` (1218) concentran demasiada responsabilidad, y el commit-turn está duplicado 4 veces con copias que **ya divergieron**.

## Los 5 bugs que rompen el producto

### 1. No hay TLS — CRÍTICO

`nginx/interview.conf:2` es `listen 80` sin `ssl`, y no existe ningún bloque `listen 443` en todo el fichero. La grabación de voz del reclutador, la transcripción y la respuesta de audio viajan en claro por el puerto 80. Sin HSTS tampoco se puede fijar HTTPS en el navegador después. Alguien en la misma red (WiFi de hotel, café, datos móviles) puede leer y alterar la entrevista entera.
**Cómo se arregla:** certificado Let's Encrypt, bloque `listen 443 ssl` con `ssl_protocols TLSv1.2 TLSv1.3`, redirección 301 del 80 y HSTS solo cuando el 443 ya funcione en todos los clientes.

### 2. La ruta cacheada nunca se habla

El backend emite un evento llamado `audio_url` (`main.py:773`), pero el dispatcher del frontend (`app.js:968-1004`) solo maneja `transcription`, `token`, `audio_chunk`, `done` y `interview_end`. No hay ninguna rama `audio_url` en el fichero entero. El usuario lee la respuesta en pantalla y no oye nada, sin ningún aviso. Y lo peor es *qué* se cachea: el FAQ cubre justamente las aperturas más probables ("cuéntame sobre ti", "preséntate", "quién eres"), así que el primer turno es el más probable que salga mudo.
**Cómo se arregla:** decidir un único evento de audio y cablear los dos lados. Lo mínimo: añadir la rama `audio_url` en `app.js` y encolarla como un chunk más.

### 3. La despedida nunca sintetiza TTS

La rama de farewell (`main.py:777-805`) emite tokens de texto y `interview_end` con `audio_url: ""` (línea 789), sin llamar nunca a TTS. El frontend, al recibir `interview_end`, apaga el avatar. **Toda entrevista termina en silencio**, en la modalidad que define el producto. Además, como no se emite `done`, el contador de la UI queda un turno por debajo de lo que se persistió.
**Cómo se arregla:** sintetizar la despedida antes de emitir `interview_end` y hacer que el frontend no detenga el avatar hasta que la cola de audio se vacíe.

### 4. Se pierden turnos bajo carrera

La tabla `turns` impone `UNIQUE(conversation_id, n)` (`persistence.py:37`), pero `record_turn` hace un `INSERT` plano (`persistence.py:232`) sin `ON CONFLICT`. Dos escrituras concurrentes del mismo número de turno hacen que una lance `IntegrityError`, que se traga como un simple `logger.warning`. **El turno se pierde en la base de datos mientras la memoria ya lo había añadido** (verificado: 1 turno en disco, 2 en memoria). El cliente ya recibió su audio y el `done`, así que para el usuario fue un éxito. Tras un reinicio, el historial ya no recuerda lo que el usuario oyó, y el contexto que se le pasa al modelo queda incompleto.
**Cómo se arregla:** `INSERT OR IGNORE` / `ON CONFLICT` con reintento sobre el siguiente `n`, o un `asyncio.Lock` por `conversation_id` alrededor de la sección crítica.

### 5. El RAG entrega marcadores `[TODO]` al LLM

El troceador (`rag.py:332-391`) solo hace `parse_frontmatter` + división por títulos + división por tokens. No filtra nada, y el campo `confidence` del frontmatter **no se lee en todo el pipeline**. Ejecutando el pipeline real: 38 documentos, 214 chunks, **11 con un `[TODO` literal**, en secciones tituladas "Outcomes" y "What I'd do differently" — exactamente las que pregunta un reclutador. El LLM puede leer el marcador en voz alta o, peor, inventar la cifra que falta ("X mil conversaciones, Y ms de latencia"), y no hay ninguna cifra real en el wiki que lo contradiga.
**Cómo se arregla:** dos cosas distintas. Filtrar `[TODO` en el chunker (evita que el marcador se lea) **y** responder los 16 TODOs abiertos (evita que el modelo improvise). El filtro solo no cierra el agujero.

## Lo que está bien

No toques esto buscando cosas que arreglar:

- **Cero inyección SQL.** Los 26 `execute()` usan parámetros `?` enlazados.
- **Cero path traversal.** `StaticFiles` no deja escapar de `AUDIO_DIR` (7/7 payloads → 404) y `_audio_extension` es lista blanca estricta.
- **La similitud coseno es correcta** (dot == coseno a 6 decimales) y la caché de embeddings se invalida bien.
- **El RAG no es el cuello de botella:** ~15 ms sobre un presupuesto de 8-12 s (0,13%).
- **Memoria:** 770 MB contra 24 GB (32× de holgura), sin crecimiento no acotado.
- **Los 11 pares texto/fondo pasan WCAG AA** sin excepción (peor caso 5,58:1).
- **Cero código muerto en `backend/`** (barrido AST). El código muerto está confinado a `initParticles()` en `app.js`, y su script ni siquiera se carga.
- **258 tests en verde.** Es la red de seguridad con la que hay que trabajar.

## Lo que el README dice mal

Solo dos puntos, de tres que se revisaron:

- **"10 req/min por IP"** es un **tope global**, no por IP. Detrás de Nginx, `request.client.host` es siempre `127.0.0.1`, así que todos los visitantes comparten el mismo presupuesto. Un solo cliente lo agota y el resto recibe `429`.
- **"Latencia 8-12s"** no tiene telemetría que lo respalde. `_t_rag` se calcula y nunca se registra, y no existe time-to-first-token en ninguna ruta. No es que la cifra sea falsa: es que nadie puede comprobarla desde el código.

La tercera afirmación del README —"155+ tests"— es correcta en lo esencial: la suite real tiene **258 tests**. El README se queda corto en la cuenta, no se equivoca.
