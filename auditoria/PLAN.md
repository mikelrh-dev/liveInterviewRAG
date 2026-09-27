# Plan de corrección

Ordenado por severidad técnica, tal como quedó en `AUDITORIA-2026-09-27.md`. La suite está sana (258/258), así que **cada corrección se puede verificar**: ejecuta los tests antes y después.

Leyenda de esfuerzo: **S** = horas · **M** = un día · **L** = varios días.

> **Estado 2026-09-27:** Fases 1, 2 y 3 completadas y verificadas. **380 tests pytest + 38 node en verde** (partían de 258). `main.py` pasó de 1035 a 251 líneas. Pendiente de despliegue en el VPS: certificado TLS y los dos ficheros de configuración. **3.1 sigue siendo tarea humana**: resolver los 16 `[TODO]` y promover las 12 páginas `medium` de la wiki — eso exige datos personales que solo tienes tú.

> **Estado 2026-09-27:** Fases 1, 2 y 3 completadas y verificadas. **380 tests pytest + 38 node en verde** (partían de 258). `main.py` pasó de 1035 a 251 líneas. **Pendiente de despliegue en el VPS:** emitir el certificado TLS y subir `nginx/interview.conf` + `interviewtts.service`. El redirect 80→443 y el HSTS se activan **siguiendo el orden que documenta el propio fichero de nginx**.
>
> **3.1 sigue siendo tarea humana:** resolver los 16 `[TODO]` y promover las 12 páginas `medium` exige datos personales que solo tienes tú. El chunker ya no sirve los marcadores al LLM, así que el sistema no se auto-contamina mientras tanto, pero el hueco de contenido sigue ahí.

---

## Fase 1 — Seguridad y despliegue ← FASE ACTUAL

El único hallazgo que anula el resto del trabajo de seguridad. Es configuración pura: no hay que tocar lógica ni tests. **Antes de volver a desplegar.**

- [x] **1.1 — Habilitar TLS y redirigir 80→443** · `nginx/interview.conf` · depende: — · **M**
  Emitir certificado Let's Encrypt, añadir `listen 443 ssl` con `ssl_certificate`, `ssl_certificate_key` y `ssl_protocols TLSv1.2 TLSv1.3`, y `return 301 https://$host$request_uri;` en el 80. Añadir `Strict-Transport-Security` **solo** cuando el 443 funcione en todos los clientes.

- [x] **1.2 — Cerrar el backend a `127.0.0.1`** · `deployment/interviewtts.service` · depende: 1.1 · **S**
  Cambiar `--host 0.0.0.0` por `--host 127.0.0.1` y documentar el cierre del puerto 8000 en la security list de OCI como paso obligatorio del runbook. Añadir `--proxy-headers` **siempre junto a** `--forwarded-allow-ips=127.0.0.1`.

- [x] **1.3 — Corregir el rate limit para que sea por IP de verdad** · `backend/main.py:311` · depende: 1.2 · **M**
  Leer `X-Forwarded-For` (última IP de la lista) en vez de `request.client.host`. **Cuidado:** hoy el límite no es evadible precisamente porque la cabecera se ignora; añadir `--proxy-headers` sin `--forwarded-allow-ips=127.0.0.1` **introduce** la evasión. Verifica con dos clientes en IPs distintas que los contadores son independientes.

- [x] **1.4 — Imponer el corte de tamaño en el upload antes de materializarlo en RAM** · `backend/main.py:521,527` · depende: — · **M**
  Leer en bloques con `audio.read(65536)` acumulando y abortar en cuanto se supere el máximo, en vez de `read()` a secas. Subir el límite a `backend/config.py` y fijar `client_max_body_size 6m;` en el `location /api/` de nginx para que el contrato sea explícito.

- [x] **1.5 — Sacar `transcribe` del event loop en el endpoint de no-streaming** · `backend/main.py:539` · depende: — · **S**
  `await asyncio.to_thread(stt_service.transcribe, temp_audio)`, con paridad con la línea 710, que ya lo hace bien. Mismo tratamiento para `rag_pipeline.get_context_string` y `get_chunks_with_scores`.

**Cómo verificar la Fase 1**

```powershell
# 1. Config de nginx válida y TLS sirviendo
nginx -t
curl -I https://TU-DOMINIO/            # 200, Strict-Transport-Security presente
curl -I http://TU-DOMINIO/             # 301 a https
# 2. El backend ya no está en la red
Test-NetConnection -ComputerName TU-VPS-IP -Port 8000   # TcpTestSucceeded: False
# 3. El limitador ahora es por IP: dos clientes a la vez dan contadores independientes
# 4. Nada se rompió
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider   # 380 passed
```

---

## Fase 2 — Corrección de datos y flujo de voz

**El producto roto.** Cuatro de los cinco bugs de `RESUMEN.md` están aquí. La suite sigue verde, así que se puede ir de uno en uno.

- [x] **2.1 — `INSERT OR IGNORE` / `ON CONFLICT` en `record_turn`** · `backend/services/persistence.py:232` · depende: — · **S**
  Reintentar sobre el siguiente `n` en vez de tragar el `IntegrityError`, o proteger `main.py:974-997` con un `asyncio.Lock` por `conversation_id`. Decide también la semántica de duplicado.
  *Test de regresión: el probe de la Capa 2, traducido a pytest.*

- [x] **2.2 — Unificar el evento de audio entre backend y frontend** · `backend/main.py:773`; `frontend/app.js:968-1004` · depende: — · **M**
  El backend emite `audio_url` y el front escucha `audio_chunk`. Decidir **uno** y cablear los dos lados. Lo más limpio: que `emit_cached_answer` emita `audio_chunk` con `id: 0` y eliminar `audio_url`.
  *Añade el test de contrato de eventos que habría atrapado esto (ver 3.4).*

- [x] **2.3 — Sintetizar TTS en la rama de despedida** · `backend/main.py:777-818` · depende: 2.2 · **S**
  Sintetizar antes de emitir `interview_end` y hacer que el frontend no llame a `stopInterview()` hasta que la cola de audio se drene.

- [x] **2.4 — Manejar el evento `error` y cerrar limpio un stream truncado** · `frontend/app.js` · depende: 2.2 · **S**
  Añadir la rama `error` al dispatcher (hoy el backend la emite en `main.py:741` y el front la ignora) y un flag `sawTerminal` para detectar un stream que termina sin `done`.

- [x] **2.5 — Estado de turno desde el servidor, y limpiar el transcript por entrevista** · `frontend/app.js:987-992,1044-1051` · depende: — · **M**
  Incluir `n` en el payload SSE `done` (el backend ya lo tiene en `main.py:974`) y borrar `getCurrentTurnNumber()`. Limpiar `#conversation` en `startInterview`.

- [x] **2.6 — Que el failover del LLM no reemita tokens** · `backend/services/llm.py:146-155` · depende: — · **M**
  El fallback a OpenRouter solo es seguro si el proveedor primario falla **antes del primer token**. Si ya se emitió texto, propagar el error en vez de reiniciar la respuesta.
  *Test de regresión: un fallo tras N tokens no debe duplicar.*

- [x] **2.7 — Alinear singular/plural en el filtro `doc_type`** · `backend/services/rag.py:33-45,437-447` · depende: — · **S**
  Tres de los ocho filtros no pueden matchear nada y devuelven `[]` sin warning. Derivar el filtro de los tipos realmente presentes en el corpus y **nunca truncar a vacío** por un filtro que no aplica.
  *Test parametrizado: todos los pares de `QUERY_TYPE_KEYWORDS` × tipos del corpus.*

- [x] **2.8 — Filtrar `[TODO` y marcadores de confianza en el chunker** · `backend/services/rag.py:332` · depende: — · **M**
  Filtrar en `_chunk_document` antes de trocear. **Ojo:** esto evita que el marcador se lea en voz alta, pero no evita que el modelo invente la cifra que falta. Cerrar los 16 TODOs del wiki es la otra mitad (Fase 3).

**Cómo verificar la Fase 2**

```powershell
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider   # 380 passed, más los nuevos
venv\Scripts\python.exe scripts/wiki/validate.py --wiki wiki/     # 0 errores, 65 warnings
# A mano, en el navegador: pregunta algo del FAQ ("preséntate") y DEBES oír la respuesta
# A mano: termina con "gracias, eso es todo" y DEBES oír la despedida
# A mano: segunda entrevista seguida -> el contador de turnos debe volver a 1 y abrir el panel de Contexto
# A mano: con dos pestañas en la misma conversación, al reiniciar el backend el historial
#        debe conservar los mismos turnos que se oyeron
```

---

## Fase 3 — Arquitectura y wiki

Mantenimiento y contenido. Nada aquí rompe el producto hoy, pero 2.6 y 2.7 rely del andamiaje de 3.2 y 3.4.

- [ ] **3.1 — Resolver los 16 `[TODO]` y promover las 12 páginas `medium`** · `wiki/**` · depende: — · **M (humano)**
  Son decisiones de contenido, no de código. Prioridad: `projects/interview-tts.md:46` (métricas), `fraud-detector.md:17` vs `:99` (decidir si está desplegado y dejar **una sola** fuente), y los niveles de `skills/devops.md:26` y `skills/frontend.md:24`.

- [x] **3.2 — Capa de orquestación y unificar el commit-turn** · `backend/main.py:630-658,744-770,784-806,973-1000` · depende: 2.1 · **L**
  Extraer `conversation_store.append_turn(...)` y colapsar las 4 copias a una línea cada una. **Cuidado:** la copia de la línea 999 ya divergió (guarda en caché semántica sin las guardas de la 597-601); un refactor sin la auditoría previa reintroduce el bug.

- [x] **3.3 — Dividir `app.js` (1218) y `main.py` (1035)** · varios · depende: 3.2 · **L**
  **Ojo con la trampa:** el mount `/audio` depende del `mkdir` implícito de `TTSService.__init__` (`tts.py:19`). Hacer los servicios perezosos rompe el arranque con `RuntimeError`. Haz el `mkdir` explícito antes de cualquier `mount` y quita el del constructor.

- [x] **3.4 — Tests de contrato de eventos (SSE) y de `getCurrentTurnNumber`** · `tests/` · depende: — · **M**
  La red que habría atrapado U5-01, U5-02 y U5-03 en el primer commit. Un test que compare el conjunto de eventos que `main.py` emite con el que el dispatcher sabe manejar.

**Cómo verificar la Fase 3**

```powershell
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider   # 380 passed + los nuevos de contrato
venv\Scripts\python.exe scripts/wiki/validate.py --wiki wiki/     # 0 errores
venv\Scripts\python.exe -c "import backend.main"                 # arranca sin RuntimeError
Select-String -Path "wiki\**\*.md" -Pattern "\[TODO"              # 0 coincidencias
Select-String -Path "wiki\**\*.md" -Pattern "confidence: medium"  # 0 coincidencias
```

---

## Fase 4 — MEDIO (selección)

El resto de los MEDIO y todos los BAJO están en `HALLAZGOS.md` y en los informes de capa. Si solo hay tiempo para un handful, estos son los de mejor relación esfuerzo/impacto:

- [ ] **Tokens inexistentes** en la tarjeta del disclaimer (`--surface-container`, `--font-sora`, `--font-inter`): el `background` cae a `transparent` y la puerta de entrada no tiene fondo. **Dos cambios de un carácter.** · `frontend/style.css:163-240` · **S** · V6-01, U5-25
- [ ] **Cuatro `setStatus(..., true)` en vez de `"error"`**: los cuatro fallos graves se pintan en el color normal. **Cuatro cambios de un carácter.** · `frontend/app.js:616,690,720,1032` · **S** · U5-06, V6-12
- [ ] **Mover `#orbital-ring` bajo `#avatar-wrapper`**: hoy resuelve su centro contra `#hero-center` y queda ~93 px descentrado. · `frontend/index.html:202` · **S** · V6-02
- [ ] **Escalar el hero con la altura disponible** (`min(380px, 40dvh)` + `min-height` en `#conversation`): hoy el transcript colapsa a 0 px en cualquier portátil de menos de ~743 px de alto. · `frontend/style.css:502-511` · **S** · V6-03, U5-15
- [ ] **`--outline-variant` a 3:1** (WCAG 1.4.11): hoy da 2,01:1 en `#context-toggle`, `.chunk-pill` y `.disclaimer-card`. · `frontend/style.css:18` · **S** · V6-07
- [ ] **`.m4a` en el filtro de limpieza + test de regresión**: los temporales del navegador en iPhone no se borran nunca tras un reinicio. · `backend/main.py:218` · **S** · S1-06, F2-10, A4-14
- [ ] **`.rag_cache` en `ReadWritePaths`**: una línea en el unit file; hoy la caché de embeddings nunca se escribe en producción. · `deployment/interviewtts.service:23-25` · **S** · S1-09
- [ ] **Recalibrar o retirar la caché semántica**: el umbral 0,93 nunca acierta y no existe punto de operación útil con este modelo. · `backend/config.py:89-91` · **M** · R3-02
- [ ] **Telemetría por etapa con time-to-first-token**: sin esto el "8-12 s" del README seguirá sin poder comprobarse. · `backend/main.py:616-628,844` · **M** · R3-03
- [ ] **Limpiar `initParticles()`** (muerta, y `tsparticles` ni se carga) y `audioBlocked` (se escribe dos veces, no se lee nunca). · `frontend/app.js:379-416,30` · **S** · A4-16

**Cómo verificar la Fase 4**

```powershell
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider   # 380 passed
# Tokens CSS: ningún var(--x) referenciado sin definir en :root
$root = Select-String -Path "frontend\style.css" -Pattern "^\s*--([a-z-]+):" -AllMatches
# Contraste: --outline-variant debe dar >= 3:1 sobre #111111
```

Y una pasada de navegador, que es lo único que falta: abrir la página a 1366×768, 900×600 y 1024×768, en claro y oscuro, y comprobar que el transcript no colapsa, que el anillo está centrado y que el CTA del disclaimer tiene fondo. Ninguna prueba automática cubre el render.
