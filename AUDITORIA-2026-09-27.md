# Auditoría Integral de InterviewTTS — 2026-09-27

**Alcance:** auditoría técnica, funcional, visual y de contenido sobre el repositorio completo.
**Método:** 7 capas ordenadas por riesgo, cada una analizada con contexto fresco y verificada por el auditor sobre el código real antes de aceptarse.
**Alcance de la auditoría:** identificar y priorizar. **No se modificó código de producto.** El único artefacto creado es este informe.

> Documento de trabajo local. `.gitignore:80` ignora `AUDITORIA-*.md` por diseño (igual que la auditoría de agosto). Los informes detallados por capa están en `reports/audit/` (también ignorado).

---

## Resumen ejecutivo

- **1 hallazgo CRÍTICO**: no hay TLS en ninguna capa. Todo el tráfico, incluidas las credenciales de API que viajan al backend, va en claro por el puerto 80. En un producto cuyo activo a proteger es un perfil profesional, esto anula el resto del trabajo de seguridad.
- **16 hallazgos ALTO**, de los cuales 4 rompen directamente la experiencia principal del producto: la ruta cacheada nunca se habla, la despedida termina en silencio, laFiesta de turnos pierde datos bajo carrera, y el RAG entrega marcadores `[TODO]` al LLM como si fueran hechos.
- **La documentación pública miente en tres puntos concretos**: el README afirma "10 req/min por IP" (es un tope global), "155+ tests en verde" (250 pasan, 8 no se ejecutan) y "latencia 8-12s" (no existe telemetría para verificarlo). Un portfolio se evalúa por lo que documenta tanto como por lo que ejecuta.
- **La capa de servicios de `backend/` está bien diseñada** (cero código muerto en AST sobre todo el paquete). El problema estructural es cómo se cose: `main.py` (1035 líneas) y `app.js` (1218) son dos objetos dios, y el commit-turn está duplicado 4 veces — dos de las copias ya divergieron.
- **Acción recomendada antes de cualquier otra cosa:** arreglar el toolchain de tests. Sin tests ejecutables, cualquier corrección se aplica a ciegas.

| Severidad | Nº |
|---|---|
| CRÍTICO | 1 |
| ALTO | 16 |
| MEDIO | 68 |
| BAJO | 33 |
| **Total** | **118** |

---

## Baseline verificada (2026-09-27)

| Dato | Valor verificado |
|---|---|
| Tests | **250 pasan, 8 fallan** (no ejecutan) |
| Intérprete correcto | `venv\Scripts\python.exe` (el python global carece de `pydantic`) |
| Comando | `venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider` |
| CodeGraph | indexado: 40 archivos, 897 nodos, 2022 edges |
| `.env` | correctamente ignorado (`.gitignore:21`) |
| Wiki | 16 marcadores `[TODO]`, 12 páginas `confidence: medium`, 0 `low` |

---

## Capas e informes detallados

| # | Capa | Foco | CRÍT | ALTO | MED | BAJO | Informe |
|---|---|---|---|---|---|---|---|
| 1 | Seguridad e integridad | Ataque, secretos, SQL, TLS, rate limit | 1 | 4 | 7 | 5 | `reports/audit/capa-1-seguridad.md` |
| 2 | Fiabilidad y concurrencia | Event loop, SSE, carreras, degradación | 0 | 4 | 9 | 2 | `reports/audit/capa-2-fiabilidad.md` |
| 3 | Rendimiento y RAG | Latencia, retrieval, cachés, memoria | 0 | 1 | 6 | 2 | `reports/audit/capa-3-rendimiento.md` |
| 4 | Arquitectura y calidad | main.py/app.js, duplicación, config | 0 | 1 | 14 | 4 | `reports/audit/capa-4-arquitectura.md` |
| 5 | Funcional y UX | Recorrido, errores, a11y, overlay | 0 | 3 | 20 | 9 | `reports/audit/capa-5-funcional-ux.md` |
| 6 | Visual | Tokens, motion, avatar, overlay | 0 | 0 | 7 | 9 | `reports/audit/capa-6-visual.md` |
| 7 | Wiki (contenido) | TODO, confianza, TODO-filter, deriva | 0 | 3 | 5 | 2 | `reports/audit/capa-7-wiki.md` |

---

## Hallazgos CRÍTICO y ALTO (los 17 que mandan)

Cada uno verificado sobre el código por el auditor. `UB` = ubicación.

### CRÍTICO

**C-1 · No hay TLS en ninguna capa.** `nginx/interview.conf:2` es `listen 80` sin `ssl`. Las claves de API viajan en cabeceras HTTP al proveedor desde el servidor, pero todo el tramo navegador→servidor también va en claro. Un `--host 0.0.0.0` (systemd) expone el backend. Impacto: interceptación de la sesión de entrevista y de cualquier secreto en tránsito. *Verificado por el auditor.*

### ALTO — Seguridad (Capa 1)

**S-1 · El rate limit no es por IP: es global.** `main.py:311` usa `request.client.host`; uvicorn arranca sin `--proxy-headers` (`deployment/interviewtts.service:10`), así que detrás de Nginx todos los visitantes comparten el cubo de 10 req/min. Un usuario agota el presupuesto de todos. *Cuidado al arreglar:* hoy no es evadible por `X-Forwarded-For` precisamente porque la cabecera se ignora; añadir `--proxy-headers` sin `--forwarded-allow-ips=127.0.0.1` **introduce** la evasión. *Verificado.*

**S-2 · El upload se bufferiza entero en RAM antes del corte de 5 MB.** `main.py:527` comprueba `MAX_AUDIO_SIZE` después de `read()`; Starlette no limita la parte de archivo. *Verificado.*

**S-3 · Whisper corre dentro del event loop en el endpoint de no-streaming.** `main.py:539` es síncrono; el mismo call en el stream (`main.py:710`) sí usa `to_thread`. Inconsistencia dentro del mismo fichero. *Verificado por el auditor.*

**S-4 · Backend en `0.0.0.0:8000` sin autenticación y sin firewall documentado.** La alcanzabilidad real depende de la security list de OCI (consola, no el repo) — marcado INFERIDO.

### ALTO — Fiabilidad (Capa 2)

**F-1 · Carrera `UNIQUE(conversation_id, n)`: el turno se pierde de SQLite pero queda en memoria.** `persistence.py:37` define la restricción; `persistence.py:232` hace un `INSERT` **plano** sin `ON CONFLICT`. Dos escrituras concurrentes del mismo `n` → una lanza `IntegrityError` mientras la memoria ya había añadido el turno. Pérdida de datos real. *Verificado por el auditor.*

**F-2 · El failover Google→OpenRouter duplica los tokens ya emitidos.** Se reintenta el prompt tras tokens parciales, concatenándolos dos veces.

**F-3 · Reintento de un POST no idempotente duplica turnos.** (relacionado con F-1)

**F-4 · Hambre del executor por defecto.** Los streams de LLM bloquean el offload de STT (`to_thread` compite por el executor).

### ALTO — RAG (Capa 3)

**R-1 · Tres de los ocho filtros `doc_type` no pueden matchear ningún chunk → RAG devuelve contexto vacío.** `QUERY_TYPE_KEYWORDS` usa claves **plural** (`stories`/`opinions`/`decisions`, rag.py:37-39) pero el frontmatter `type:` de la wiki usa **singular** (`type: decision`), y la normalización (rag.py:437-447) solo arregla `project`. Resultado: preguntas sobre opiniones/decisiones/historias responden **sin grounding** del perfil. Los autores lo detectaron para `project` (`tests/test_rag.py:432-437`) pero no lo generalizaron. **MEDIDO: 4 de 9 formulaciones naturales devuelven 0 chunks.** *Verificado por el auditor.*

### ALTO — Arquitectura (Capa 4)

**A-1 · El commit-turn está duplicado 4 veces** (`main.py` 630-658, 744-770, 784-806, 973-1000) y las copias **ya divergieron**: la de la línea 999 mete la respuesta en caché semántica sin las guardas `cached_response is None and semantic_hit is None` que sí tiene la de 597-601. Un refactor de "capa de orquestación" sin esta auditoría reintroduce el bug.

### ALTO — Funcional (Capa 5)

**U-1 · El backend emite `audio_url` y el frontend no lo maneja → toda respuesta del caché FAQ/semántico es muda.** `main.py:772` emite `audio_url`; el dispatcher de `app.js:968-1004` maneja `transcription`/`token`/`audio_chunk`/`done`/`interview_end` — **no** `audio_url`. El texto sale, la voz no. *Verificado por el auditor de punta a punta.*

**U-2 · La rama de despedida nunca sintetiza TTS → toda entrevista termina en silencio.** `main.py:777-805` emite `interview_end` con `audio_url: ""` (789) sin llamar a TTS. Es la modalidad que define el producto. *Verificado por el auditor.*

**U-3 · El nº de turno se infiere del DOM y el transcript nunca se limpia.** Desde la 2ª entrevista el contador miente y el panel de Contexto da 404 en silencio. `app.js:987-992`, `1044-1051`. Además: el backend emite `error` (main.py:741) que el frontend **no maneja** → fallo silencioso.

### ALTO — Wiki (Capa 7)

**W-1 · El RAG sirve marcadores `[TODO]` literales al LLM.** `_chunk_document` (rag.py:332-391) solo hace `parse_frontmatter` + split; **`confidence` nunca se lee en todo el pipeline**. Prueba empírica con el `RAGPipeline` real: 38 docs → 214 chunks, **11 con `[TODO` literal**, en secciones "Outcomes" y "What I'd do differently". *Verificado por el auditor: 16 TODOs en wiki/, cero filtrado en el chunker.*

**W-2 · `fraud-detector.md:17` vs `:99` se contradice sobre el deploy**, y la URL es la raíz del portfolio. El LLM recibirá ambas versiones.

**W-3 · Niveles de skill sin confirmar con el TODO abierto en el mismo eje.** Riesgo de afirmación de competencia no sustentada.

---

## Lo que salió limpio (y merece confianza)

Para que el informe sea creíble, lo que **no** se encontró también cuenta:

- **Cero inyección SQL.** Los 26 `execute()` usan parámetros `?` enlazados.
- **`_audio_extension` no es vulnerable** a extensión arbitraria: es lista blanca estricta; 18 entradas hostiles producen solo `.m4a`/`.webm`.
- **`StaticFiles` no permite escapar de `AUDIO_DIR`**: 7/7 payloads de traversal → 404.
- **Ninguna vía de fuga de API key** al cliente.
- **Similitud coseno correcta** (dot == coseno a 6 decimales); caché de embeddings se invalida bien (ahorra ~3s medidos).
- **RAG no es el cuello de botella**: ~15ms sobre 8-12s (0,13%).
- **Memoria 770 MB contra 24 GB** (32× holgura), sin crecimiento no acotado.
- **Los 11 pares texto/fondo pasan WCAG AA** sin excepción (peor caso 5,58:1).
- **Cero código muerto** en todo `backend/` (barrido AST); el código muerto está confinado a `initParticles()` (~38 líneas) en `app.js`, cuyo script ni se carga.
- **Validador de wiki**: 0 errores, 65 warnings (todos `link asymmetry`), 37 archivos válidos.
- **`wikiDocs/` no es segunda fuente de verdad** — verificado, no hay doble fuente.

---

## Plan de acción priorizado

Ordenado por severidad técnica. La primera acción desbloquea la verificación de todas las demás.

### Fase 0 — Desbloquear la verificación (antes de tocar nada)

| # | Acción | Archivos | Depende | Esfuerzo |
|---|---|---|---|---|
| 0.1 | Fijar `pytest`/`pytest-asyncio` compatibles para que los 8 tests async se ejecuten | `pyproject.toml` (o lockfile) | — | S |

**Por qué primero:** sin esto, cualquier corrección posterior se aplica sin red de seguridad. El README afirma "155+ tests en verde"; la realidad es que TTS y memoria de conversación **no están verificadas** por CI hoy.

### Fase 1 — CRÍTICO + ALTO de seguridad y despliegue (antes de volver a desplegar)

| # | Acción | Archivos | Depende | Esfuerzo |
|---|---|---|---|---|
| 1.1 | Habilitar TLS (certbot + `listen 443 ssl`) y redirigir 80→443 | `nginx/interview.conf` | — | M |
| 1.2 | Cerrar el backend a `127.0.0.1` (o documentar firewall OCI); `--proxy-headers` **con** `--forwarded-allow-ips=127.0.0.1` | `deployment/interviewtts.service` | 1.1 | S |
| 1.3 | Corregir rate limit: `TrustedHostMiddleware`/`X-Forwarded-For` correcto, o IPO allowlist | `backend/main.py` | 1.2 | M |
| 1.4 | Imponer `Content-Length` / streaming con corte temprano en el upload | `backend/main.py` | — | M |
| 1.5 | Offload de `transcribe` en el endpoint de no-streaming (paridad con 710) | `backend/main.py:539` | 0.1 | S |

### Fase 2 — ALTO de corrección de datos y flujo de voz (producto roto)

| # | Acción | Archivos | Depende | Esfuerzo |
|---|---|---|---|---|
| 2.1 | `INSERT OR IGNORE` / `ON CONFLICT` en `record_turn`, y decidir semántica de duplicado | `backend/services/persistence.py:232` | 0.1 | S |
| 2.2 | Unificar el evento de audio: el backend emite `audio_url`, el front maneja `audio_chunk` — decidir uno y cablear ambos lados | `backend/main.py`, `frontend/app.js` | 0.1 | M |
| 2.3 | Sintetizar TTS en la rama de despedida | `backend/main.py:777-805` | 2.2 | S |
| 2.4 | Manejar `error` en el dispatcher; salida segura del stream truncado | `frontend/app.js` | 2.2 | S |
| 2.5 | Estado de turno desde el servidor, no desde el DOM; limpiar transcript por entrevista | `frontend/app.js` | — | M |
| 2.6 | No reemitir tokens en failover; hacer el reintento idempotente | `backend/services/llm.py` | 0.1 | M |
| 2.7 | `return []` con warning cuando el filtro `doc_type` no matchea; alinear singular/plural | `backend/services/rag.py:33-45,437-447` | 0.1 | S |
| 2.8 | Filtrar `[TODO` y marcadores de confianza en el chunker; o curar la wiki antes de compilar | `backend/services/rag.py:332` | — | M |

### Fase 3 — ALTO/alto de arquitectura y wiki (mantenimiento y contenido)

| # | Acción | Archivos | Depende | Esfuerzo |
|---|---|---|---|---|
| 3.1 | Resolver los 16 `[TODO]` y promover las 12 páginas `medium` | `wiki/**` | — | M (humano) |
| 3.2 | Introducir capa de orquestación; unificar el commit-turn (4 copias, 2 divergentes) | `backend/main.py` | 2.1 | L |
| 3.3 | Dividir `app.js` (1218) y `main.py` (1035); **ojo**: el mount `/audio` depende del `mkdir` implícito de `TTSService.__init__` (`tts.py:19`) — hacer perezosos los servicios rompe el arranque | varios | 3.2 | L |
| 3.4 | Tests de contrato de eventos (SSE) y de `getCurrentTurnNumber` — la red que habría atrapado U-1/U-2/U-3 | `tests/` | 0.1 | M |

### Fase 4 — MEDIO (selección; el resto en los informes de capa)

- **Tokens inexistentes** en la tarjeta del disclaimer (`--surface-container`, `--font-sora`, `--font-inter`): el `background` cae a `transparent` — la puerta de entrada no tiene fondo (C6-M1).
- **Mover `#orbital-ring` bajo `#avatar-wrapper`**: hoy resuelve su centro contra `#hero-center` y queda ~93px descentrado (C6-M2).
- **Avatar de 380px contra columna central de 129px**: en 769-1051px de ancho se ve el 25% del círculo (C6-M4).
- **`--outline-variant` falla WCAG 1.4.11** (2,01:1 vs 3:1 exigido) en `#context-toggle`, `.chunk-pill`, `.disclaimer-card` (C6-M7).
- **Caché semántica calibrada contra ruido**: `FakeEmbedder` (tests/test_semantic_cache.py:27-44) usa vectores MD5 sembrados al azar; el umbral nunca acierta con el embedder real (banda [0.80,1.0] vacía). Recalibrar o retirar.
- **`CHUNK_SIZE`/`CHUNK_OVERLAP` muertos**: 0 de 214 chunks llegan a 400 palabras (máx 210); el 36% tiene <15 palabras. Recalibrar.
- **Sin telemetría de latencia**: `_t_rag` (main.py:844) se calcula y nunca se registra; sin time-to-first-token no se puede verificar el "8-12s" del README.
- **65 warnings de `link asymmetry`** en la wiki.
- **4 wikilinks rotos** `[[faq/...]]` y `index.md` desfasado e ingerido.
- **Limpiar `initParticles()`** (muerto, y `tsparticles` ni se carga).

---

## Notas de método y límites

- Las 7 capas se analizaron con contexto fresco. Ningún hallazgo de una capa sesgó otra. Los hallazgos CRÍTICO/ALTO fueron **verificados por el auditor** sobre el código real; el resto citan `file:line` verificable.
- **Lo que no se pudo verificar** (marcado INFERIDO en los informes): alcanzabilidad real del puerto 8000 (security list de OCI vive en consola); CVE concretos de dependencias (sin red); comportamiento de render (no hay navegador ni servidor) — los hallazgos de layout de Capa 6 se apoyan en aritmética cerrada del CSS, no en render; mediciones de latencia STT/LLM/TTS (sin audio real ni claves) — marcadas ESTIMADO.
- **Todas las mediciones empíricas** vêm de un x86 de escritorio, no del VPS ARM64. Para lo CPU-bound (`torch`, `onnxruntime`) el factor real será 2-4×.
- Las subagentes raramente fallaron al devolver su resumen, pero el archivo se escribió siempre; los conteos se extrajeron directamente de los informes.
- `reports/` y `AUDITORIA-*.md` están gitignorados. Para versionar este informe: `git add -f AUDITORIA-2026-09-27.md`.
