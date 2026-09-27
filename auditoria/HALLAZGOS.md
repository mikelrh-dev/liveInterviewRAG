# Los 118 hallazgos

Uno por línea, agrupados por severidad. `ID` = identificador propio de esta carpeta; entre paréntesis, el identificador del informe consolidado cuando existe. `Capa` indica de qué informe de `reports/audit/` sale.

| Severidad | Cantidad |
|---|---|
| CRÍTICO | 1 |
| ALTO | 16 |
| MEDIO | 68 |
| BAJO | 33 |
| **Total** | **118** |

## CRÍTICO (1)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| S1-01 (C-1) | No hay TLS en ninguna capa | `nginx/interview.conf:2` | Todo el tráfico, claves incluidas, va en claro por el puerto 80. |

## ALTO (16)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| S1-02 (S-1) | El rate limit es global, no por IP | `backend/main.py:311` | Un visitante agota las 10 req/min de todos los demás. |
| S1-03 (S-2) | El upload se bufferiza entero en RAM antes del corte de 5 MB | `backend/main.py:521,527` | Pocas peticiones concurrentes bastan para agotar la memoria. |
| S1-04 (S-3) | Whisper y el embedder corren dentro del event loop | `backend/main.py:539` | Un turno congela todas las conexiones, incluidos los streams SSE ajenos. |
| S1-05 (S-4) | Backend en `0.0.0.0:8000` sin autenticación ni firewall documentado | `deployment/interviewtts.service:10` | Si el 8000 está abierto, se salta el proxy y sus límites. |
| F2-01 (F-1) | El failover Google→OpenRouter duplica los tokens ya emitidos | `backend/services/llm.py:146-155` | El usuario oye la respuesta repetida y se persiste duplicada. |
| F2-02 (F-2) | El cliente reintenta un POST no idempotente | `frontend/app.js:888-902` | Un timeout de nginx relanza el pipeline entero y duplica el turno. |
| F2-03 (F-3) | Hambre del executor por defecto: LLM bloquea el offload de STT | `backend/main.py:878,710` | Medido: 1,80 s esperando un worker libre con el pool saturado. |
| F2-04 (F-4) | Carrera `UNIQUE(conversation_id, n)`: el turno se pierde en SQLite | `backend/services/persistence.py:37,232` | Tras reiniciar, el historial no recuerda turnos que el usuario oyó. |
| R3-01 (R-1) | Tres de los ocho filtros `doc_type` nunca matchean | `backend/services/rag.py:33-42,437-447` | 4 de 9 preguntas naturales responden sin grounding del perfil. |
| A4-01 (A-1) | `NameError` en cada shutdown: nunca se cierran los clientes HTTP | `backend/main.py:208` | El cierre falla siempre; traceback en cada ciclo de reinicio. |
| U5-01 (U-1) | `audio_url` se emite pero el frontend no lo maneja | `backend/main.py:773` / `frontend/app.js:968-1004` | Toda respuesta del caché FAQ/semántico sale en texto pero muda. |
| U5-02 (U-2) | La rama de despedida nunca sintetiza TTS | `backend/main.py:777-818` | Toda entrevista termina en silencio. |
| U5-03 (U-3) | El nº de turno se infiere del DOM y el transcript nunca se limpia | `frontend/app.js:987-992,1044-1051` | Desde la 2ª entrevista el contador miente y el panel da 404 mudo. |
| W7-01 (W-1) | El RAG sirve 11 chunks con `[TODO]` literales | `backend/services/rag.py:332-391` | El LLM puede leer el TODO o inventar la cifra que falta. |
| W7-02 (W-2) | `fraud-detector.md` se contradice sobre el deploy | `wiki/projects/fraud-detector.md:17,99` | El LLM recibe ambas versiones y la URL es la raíz del portfolio. |
| W7-03 (W-3) | Niveles de skill declarados sin confirmar, con el TODO abierto | `wiki/skills/devops.md:6,26`; `wiki/skills/frontend.md:6,24` | Afirmar competencia (React, Docker, CI/CD) que no se sustenta. |

## MEDIO (68)

Contexto, no lista de trabajo. Los de Capa 1 y 2 tienen más peso que el resto porque afectan a datos y a disponibilidad.

### Capa 1 — Seguridad (7)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| S1-06 | Los temporales `.m4a` nunca se limpian | `backend/main.py:218` | Audio crudo del reclutador queda publicado para siempre tras una caída. |
| S1-07 | Temporales de entrada y salidas comparten el mismo directorio público | `backend/main.py:355,531` | Conocer el nombre del fichero basta para descargarlo. |
| S1-08 | El cuerpo de error del proveedor LLM se devuelve literal al cliente | `backend/services/llm.py:307,372` | Filtra cuota, modelo y rutas del servidor (la clave de API no se filtra). |
| S1-09 | `.rag_cache` queda fuera de `ReadWritePaths` | `deployment/interviewtts.service:23-25` | En producción la caché de embeddings nunca se escribe: se reindexa al arrancar. |
| S1-10 | `requirements.txt` va 1-3 versiones mayores por detrás, sin lockfile | `backend/requirements.txt:1-10` | Una máquina nueva instala lo último disponible, no lo que corre en producción. |
| S1-11 | `CORS_ORIGINS` se parte por comas sin `.strip()` | `backend/main.py:348` | Un espacio tras la coma deja la API inaccesible desde el navegador. |
| S1-12 | Ningún endpoint exige autenticación y el `cid` viaja en la URL | `backend/main.py:475,505,676,1016` | El id queda en logs y referer; `/docs` y `/openapi.json` quedan abiertos. |

### Capa 2 — Fiabilidad (9)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| F2-05 | RAG y caché semántica embeben en el loop; la query se embebe dos veces | `backend/main.py:840,843` | Dos inferencias en el event loop por turno, 32,7 ms medidos. |
| F2-06 | `periodic_cleanup` hace todo su I/O de forma síncrona en el loop | `backend/main.py:242,270,292` | Cada 30 min congela todos los streams SSE en curso. |
| F2-07 | Sin timeout en el bucle SSE ni en el offload de Whisper | `backend/main.py:893,710` | Si Whisper se cuelga, la espera no termina nunca. |
| F2-08 | Un stream truncado deja la interfaz atascada sin error visible | `frontend/app.js:949-951` | El micro no se reactiva solo: hay que recargar la página. |
| F2-09 | Un stream truncado deja tareas y tokens huérfanos | `backend/main.py:887,878,704` | La cola crece sin límite y se sigue pagando al proveedor. |
| F2-10 | `cleanup_stale_audio()` nunca borra los ficheros `.m4a` | `backend/main.py:218` | Fuga silenciosa de disco tras cada reinicio, OOM-kill o `docker restart`. |
| F2-11 | La persistencia falla en silencio | `backend/main.py:986-997` | El pipeline afirma haber guardado lo que no guardó; no hay métrica. |
| F2-12 | Una respuesta vacía del LLM se persiste como turno válido | `backend/services/llm.py:399-400` | El cliente ve `done` sin texto, sin error y contamina el contexto futuro. |
| F2-13 | Los dos endpoints divergen: la despedida solo existe en el streaming | `backend/main.py:778-818` | La misma entrada produce respuestas distintas según por dónde entre. |

### Capa 3 — Rendimiento y RAG (6)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| R3-02 | `SEMANTIC_CACHE_THRESHOLD = 0.93` es inalcanzable | `backend/config.py:89-91` | 0 de 24 paráfrasis acierta; se paga un embed por turno para fallar seguro. |
| R3-03 | Instrumentación por etapa incompleta | `backend/main.py:616-628,844` | El "8-12 s" del README no es verificable desde el código. |
| R3-04 | La ruta de streaming recupera dos veces para la misma query | `backend/main.py:840-843` | Se duplica el coste de RAG en el camino caliente de cada request. |
| R3-05 | `CHUNK_SIZE`/`CHUNK_OVERLAP` inertes; 36% de chunks degenerados | `backend/config.py:56-57` | Ajustarlos no cambia nada; 77 chunks de ruido compiten en cada búsqueda. |
| R3-06 | `detect_doc_type` poco fiable; el fallback a TF-IDF es invisible | `backend/services/rag.py:33-58` | `/api/health` no distingue RAG completo de RAG degradado. |
| R3-07 | La caché FAQ acierta el 48% y sobre-dispara por palabra clave | `backend/services/response_cache.py:19-305` | «¿Qué opinas de Python?» devuelve una respuesta prefabricada y se dice en voz alta. |

### Capa 4 — Arquitectura (14)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| A4-02 | `main.py` es un god module: 19 responsabilidades en 1035 líneas | `backend/main.py` | La orquestación no se puede probar sin pasar por HTTP. |
| A4-03 | Tabla de descomposición propuesta (recomendación, no implementada) | `backend/main.py:37-1035` | El refactor debe ir en un PR separado de cualquier cambio de funcionalidad. |
| A4-04 | El commit-turn está duplicado 4 veces con 4 variantes | `backend/main.py:630-658,744-770,784-806,973-1000` | Las copias ya divergieron: la de la línea 999 guarda en caché sin las guardas. |
| A4-05 | Validación de audio duplicada y `MAX_AUDIO_DURATION` muerto | `backend/main.py:516-532,688-701` | Un knob documentado no controla nada; el límite real es un literal repetido. |
| A4-06 | `periodic_cleanup()` mezcla 7 responsabilidades en un bucle | `backend/main.py:225-297` | No hay forma de testear una tarea individual sin arrancar el bucle entero. |
| A4-07 | El path streaming hace la retrieval RAG dos veces | `backend/main.py:840-843` | Falta un método que devuelva contexto y chunks del mismo retrieval. |
| A4-08 | La memoria de conversación (dominio) vive dentro del módulo HTTP | `backend/main.py:383-472` | Los tests de dominio arrastran singletons y mounts del sistema de ficheros. |
| A4-09 | `detect_farewell()` es regla de negocio en la capa HTTP | `backend/main.py:64-85` | El copy de despedida está hardcodeado dentro del handler. |
| A4-10 | El mount `/audio` depende del `mkdir` implícito de `TTSService` | `backend/services/tts.py:19` | Hacer los servicios perezosos rompe el arranque con `RuntimeError`. |
| A4-11 | Deriva de configuración: 3 valores de `LLM_MAX_TOKENS`, 2 knobs muertos | `backend/config.py`; `.env.example` | Cambiar `PORT` en `.env` no cambia nada y no avisa. |
| A4-12 | `app.js` mezcla ~20 responsabilidades y adivina el turno del DOM | `frontend/app.js:1044-1051` | No puede ser correcto por construcción: depende del render, no del servidor. |
| A4-13 | Fuga de `setInterval` y handlers "End Session" duplicados | `frontend/app.js:149-159,259-284` | Cada END añade un temporizador vivo; el sidebar no se limpia en la despedida. |
| A4-14 | `cleanup_stale_audio()` no cubre el `.m4a` que el sistema produce | `backend/main.py:218` frente a `:38-41` | El hueco se materializa en cada crash, y el directorio es público. |
| A4-15 | Se loguea la transcripción del usuario (dato personal) a journald | `backend/main.py:555,565,823,834` | El proyecto lo trata como privado en `reports/` pero lo escribe en el log del sistema. |

### Capa 5 — Funcional y UX (20)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| U5-04 | Un 4xx determinista se reintenta 31 s y se reporta como problema de conexión | `frontend/app.js:888-902` | Un 422 o un 429 congelan la interfaz con un mensaje engañoso. |
| U5-05 | Un único turno de STT fallido destruye la entrevista completa | `backend/main.py:713-717` | Sin reintento ni "¿intentar de nuevo?": hay que empezar de cero. |
| U5-06 | Todos los estados de error se renderizan en el color normal | `frontend/app.js:616,691,720,1032` | Pasan `true` en vez de `"error"`: el fallo es indistinguible. |
| U5-07 | "END" no detiene el stream ni el audio en cola | `frontend/app.js:629-644` | El avatar sigue hablando y el transcript sigue creciendo. |
| U5-08 | El indicador de escritura no desaparece con respuesta vacía | `frontend/app.js:919,976,1030` | Tres puntos girando para siempre, sin texto ni salida. |
| U5-09 | Si falla el TTS de todos los chunks no hay aviso en la UI | `frontend/app.js:1005-1019` | Texto en pantalla y avatar en silencio: degradación muda de la modalidad. |
| U5-10 | No hay `aria-live` en el estado ni en la transcripción | `frontend/index.html:177,237` | Un lector de pantalla no recibe ningún aviso del flujo. |
| U5-11 | El `aria-label` del micro dice siempre "Iniciar entrevista" | `frontend/index.html:224` | El control principal miente sobre su función en cada turno. |
| U5-12 | Los chunks de RAG no son accesibles por teclado ni por lector | `frontend/app.js:1118-1138` | El propósito entero del panel de Contexto es inalcanzable sin ratón. |
| U5-13 | El toggle "Contexto" es un no-op en escritorio | `frontend/style.css:363,1070-1072` | El panel está siempre visible y el botón no cambia nada. |
| U5-14 | El panel de contexto se cierra solo a los 5 s con un timer sin control | `frontend/app.js:1100-1104` | Se cierra mientras el usuario lo está leyendo. |
| U5-15 | Por debajo de ~663 px de alto el transcript colapsa a 0 px | `frontend/style.css:47,478,502-511` | En 1280×720 no se ve ni el historial ni la línea de estado. |
| U5-16 | VAD: corte a 1,2 s, sin duración máxima, muerto sin `AudioContext` | `frontend/app.js:66-67,735-740` | Corta a mitad de frase; sin voz, la grabación no termina nunca. |
| U5-17 | El overlay de audio bloqueado es un modal solo-click | `frontend/index.html:56-60`; `frontend/app.js:336-362,603` | Se come el primer click y la entrevista arranca con el VAD muerto. |
| U5-18 | El disclaimer declara `aria-modal` sin trampa de foco | `frontend/index.html:66`; `frontend/app.js:211-238` | Promete fondo inerte y no lo es: hacen falta 3 `Tab` para llegar al micro. |
| U5-19 | Sin `localStorage` el micro queda bloqueado en cada visita | `frontend/app.js:204-238` | Recargar a mitad de entrevista pierde la sesión y hay que volver a aceptar. |
| U5-20 | `prefers-reduced-motion` no cubre el vídeo ni el bucle WebGL | `frontend/index.html:189-195`; `frontend/avatar.js:105-140` | El elemento más grande de la pantalla sigue moviéndose a 60 fps. |
| U5-21 | En móvil el panel de contexto cerrado sigue siendo enfocable | `frontend/style.css:1091-1099` | Se puede tabular hacia un `h2` y un botón invisibles. |
| U5-22 | El frontend no tiene ninguna infraestructura de pruebas | repo, sin `package.json` ni tests JS | 1218 líneas sin cobertura: por eso los 3 ALTO llegaron a `main`. |
| U5-23 | Huecos de cobertura en la lógica de conversación | `tests/test_report_service.py:201-241` | `detect_farewell` tiene un test positivo y ninguno negativo. |

### Capa 6 — Visual (7)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| V6-01 | La tarjeta del disclaimer no tiene fondo | `frontend/style.css:163,168,189,215,240` | `--surface-container` no existe: el `background` cae a transparente. |
| V6-02 | El anillo orbital está descentrado ~93 px | `frontend/style.css:570-578`; `frontend/index.html:202` | El elemento que comunica el estado se descentra de forma permanente. |
| V6-03 | El hero desborda por debajo de ~743 px de alto | `frontend/style.css:468-474,502-511` | En un portátil 1366×768 la transcripción se queda con cero contenido. |
| V6-04 | El avatar se recorta entre 769 y 1051 px de ancho | `frontend/style.css:363,515-522` | A 769 px de ancho solo se ve el 26% del círculo. |
| V6-05 | Los anillos de energía se renderizan de canto; el halo desborda sin recorte | `frontend/avatar.js:57,64,78`; `frontend/style.css:559-566` | Se leen como estelas horizontales parpadeantes, no como anillos. |
| V6-06 | `prefers-reduced-motion` no detiene el orbo WebGL | `frontend/avatar.js:105-140` | Un `requestAnimationFrame` permanente ignora la preferencia. |
| V6-07 | Los límites de los controles fallan WCAG 1.4.11 | `frontend/style.css:18` (`--outline-variant`) | 2,01:1 frente al 3:1 exigido en `#context-toggle` y `.chunk-pill`. |

### Capa 7 — Wiki (5)

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| W7-04 | 10 páginas `medium` nunca revisadas | `wiki/decisions/`, `wiki/faq/`, `wiki/opinions/`, `wiki/stories/` | Respuestas correctas pero genéricas, sin ningún ancla memorable. |
| W7-05 | El RAG ingiere `index.md`, que además está desfasado | `wiki/index.md`; `backend/services/candidate.py:54` | El modelo ve metadatos de confianza y cree que 7 páginas no existen. |
| W7-06 | Texto corrupto con caracteres CJK inyectados | `wiki/projects/fraud-detector.md:97` | Ruido de alto grado para el embedding; se propaga a `profile.json`. |
| W7-07 | Cuatro wikilinks rotos con placeholder de plantilla | `wiki/faq/por-que-dejar-supermercados.md:24` (+3) | El LLM puede reproducir la sintaxis `[[faq/...]]` en voz alta. |
| W7-08 | No hay gate entre "contenido sin confirmar" y "contenido servido" | `scripts/wiki/validate.py` vs `backend/services/rag.py:332-391` | Causa raíz compartida de W7-01, W7-05, W7-06 y W7-07. |

**Total MEDIO: 68** (7 + 9 + 6 + 14 + 20 + 7 + 5)

## BAJO (33)

Contexto. Ninguno rompe nada por sí solo; se dejan aquí para que la lista sea completa y no haya que releer los informes.

| ID | Hallazgo | Ubicación | Impacto en una frase |
|---|---|---|---|
| S1-13 | `add_header` en `/audio/` descarta las cabeceras heredadas | `nginx/interview.conf:29` | Las respuestas de audio pierden `X-Frame-Options` y `nosniff`. |
| S1-14 | Faltan CSP, `Referrer-Policy` y `Permissions-Policy` | `nginx/interview.conf:33-35` | Sin CSP, una inyección futura no tiene freno. |
| S1-15 | `cleanup_expired` deja los directorios vacíos por conversación | `backend/services/report.py:65-69` | Los inodos crecen de forma lineal e indefinida. |
| S1-16 | El 429 no lleva `Retry-After`; `/audio/*` no pasa por el limitador | `backend/main.py:310,320-325` | El cliente reintenta a ciegas justo cuando el límite se satura. |
| S1-17 | `.env.example` fija `DB_PATH` como ruta relativa | `.env.example:44`; `backend/config.py:81-83` | Correcto por casualidad: depende del `WorkingDirectory` de systemd. |
| F2-14 | `except HTTPException: raise` es código muerto en el generador SSE | `backend/main.py:1004-1005` | Inalcanzable hoy; si lo fuera, rompería el stream a mitad. |
| F2-15 | Los directorios por conversación crecen de forma indefinida | `backend/services/tts.py:42`; `backend/services/report.py:42` | Ningún camino del código elimina un directorio de sesión. |
| R3-08 | `RAGPipeline(cache_dir=str)` lanza `TypeError` | `backend/services/rag.py:136-139` | La anotación `Optional[Path]` no se hace cumplir. |
| R3-09 | 331 MB de RSS para `import torch` sirven un modelo de 23 MB | `backend/services/rag.py:146-148` | 32× de holgura contra 24 GB: no es un problema de memoria. |
| A4-16 | Código muerto: `initParticles()`, `audioBlocked`, rama inalcanzable, imports | `frontend/app.js:379-416,30`; `backend/main.py:1004` | 38 líneas de partículas que nunca corrieron; `tsParticles` ni se carga. |
| A4-17 | Timing del pipeline por índice posicional en una lista | `backend/main.py:534,557-558,616-620` | Las etiquetas dependen de la aritmética de índices, no de nombres. |
| A4-18 | Montajes de estáticos desalineados con la configuración de nginx | `backend/main.py:355,1032-1035` | En producción FastAPI solo recibe `/api/*`, pero no está documentado. |
| A4-19 | Defaults de modelo duplicados entre `config.py` y las firmas | `backend/config.py:38,41,49`; `backend/services/llm.py:99-101` | Cambiar el default de la firma no tiene ningún efecto. |
| U5-24 | "ONLINE", "LATENCY 12ms" y "All Systems Online" son estáticos | `frontend/index.html:101-106,136-139` | Siguen diciendo "online" mientras el estado dice lo contrario. |
| U5-25 | Cinco custom properties del disclaimer no existen | `frontend/style.css:163,168,189,215,240` | La tarjeta queda transparente y sin la tipografía Sora. |
| U5-26 | Etiquetas de sidebar a 9,6 px | `frontend/style.css:388-394` | Ilegibles sin zoom. |
| U5-27 | La jerarquía de encabezados salta de h1 a h3; no hay skip link | `frontend/index.html:97,127,135,144,153` | Navegar por encabezados pierde un nivel entero. |
| U5-28 | Voseo en la despedida mientras la interfaz usa tuteo | `backend/main.py:779` | La frase más memorable es la única que cambia de registro. |
| U5-29 | `startSessionTimer` filtra intervals; el END por micro no reinicia el cronómetro | `frontend/app.js:149-159,629-644` | Tras N sesiones hay N temporizadores escribiendo el mismo elemento. |
| U5-30 | El cursor de escritura queda parpadeando en el último mensaje | `frontend/app.js:1066-1075` | Sugiere que el avatar sigue escribiendo cuando ya terminó. |
| U5-31 | `escapeHtml` no escapa comillas y hay código muerto en el pipeline | `frontend/app.js:1210-1214,928-943` | Sin XSS hoy, pero a una línea de refactor de serlo. |
| U5-32 | El mensaje de bienvenida se muestra duplicado al cargar | `frontend/index.html:178-180`; `frontend/app.js:329` | Dos textos casi idénticos en la primera pantalla. |
| V6-08 | La capa de tokens de motion solo se aplica a `transition` | `frontend/style.css:63-68,141,640` | 0 de 8 `@keyframes` usan un token de duración. |
| V6-09 | Sin escala tipográfica ni tokens de radio | `frontend/style.css:390,212` | 10 medidas sueltas y 7 radios literales. |
| V6-10 | Deriva de espaciado y de z-index | `frontend/style.css:485,154,252,542,564` | `z-index: 1100` queda fuera del mapa de 9 tokens. |
| V6-11 | El sistema de color de estado se contradice y se sale de la paleta | `frontend/avatar.js:174-179`; `frontend/style.css:683,705-715` | Avatar violeta, micro verde y status neutro para el mismo estado. |
| V6-12 | Cuatro estados de error graves nunca reciben el estilo de error | `frontend/app.js:616,690,720,1032`; `frontend/style.css:713-715` | Se renderizan en el mismo gris que "Escuchando…". |
| V6-13 | `.typing-indicator` puede quedar animándose entre entrevistas | `frontend/app.js:919,976,996-1004` | Un nodo animado sobrevive al fin de sesión y bloquea el siguiente. |
| V6-14 | Micro-motion defectuoso: cursor que nunca parpadea, transición muerta en el VU | `frontend/app.js:1066-1075`; `frontend/style.css:456,867` | Recrear el nodo en cada token reinicia la animación. |
| V6-15 | El disclaimer no tiene coreografía de entrada, focus trap ni autofocus | `frontend/style.css:1036-1038`; `frontend/index.html:66` | El overlay aparece de golpe sobre la propia animación de página. |
| V6-16 | Instrumentación muerta o engañosa, presentada como dato en vivo | `frontend/index.html:101-106`; `frontend/app.js:458-478` | El waveform muestra el micrófono mientras habla el gemelo. |
| W7-09 | Siete archivos editados sin subir `updated` | `wiki/experience/*`, `wiki/projects/*`, `wiki/faq/presentacion-30-segundos.md` | La única señal de "revisado por una persona" miente. |
| W7-10 | `candidate/` en disco está obsoleto respecto al pipeline actual | `candidate/` vs `scripts/wiki/compile.py:198-214` | Quien mire `candidate/` concluirá que no hay ningún TODO. |

**Total BAJO: 33** (5 + 2 + 2 + 4 + 9 + 9 + 2)
