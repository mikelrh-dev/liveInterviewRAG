# Mejoras: qué merece la pena hacer y qué no

**Fecha:** 2026-09-27 · **Alcance:** tres exploraciones (visual/interacción, calidad RAG,
capacidad funcional) consolidadas y verificadas contra el código · **Base:** `e8e1e6f`

## Veredicto

InterviewTTS ya no es un prototipo frágil: hay 391 tests, un pipeline STT → RAG → LLM → TTS con
streaming, persistencia con control de turnos y una suite de seguridad que resiste revisión. El
problema es que esa ingeniería está en un sitio y la credibilidad en otro. Hay una píldora
`LATENCY 12ms` escrita a mano en el HTML junto a nombres de modelo que sí se piden por red, un
reintento que convierte un 429 en cinco pipelines completos, y un fallo del LLM que cose media
respuesta con otra entera. Eso pesa más que cualquier decisión estética. **Lo que más valor da
ahora es cerrar la distancia entre lo que el sistema dice de sí mismo y lo que hace**, empezando
por borrar la telemetría inventada y arreglar el reintento: dos cambios de pocas líneas cada uno,
y los dos primeros que descubriría un reclutador técnico.

---

## 1. Las tres cosas que están realmente rotas

Las tres están verificadas leyendo el fichero exacto o ejecutando la comprobación. Son de la
categoría "el producto miente o se rompe", no "se podría embellecer".

### 1.1 Telemetría inventada en la interfaz

**`frontend/index.html:106`** (`LATENCY 12ms`), `:103` (`ONLINE`), `:138` (`All Systems Online`) ·
Corrección

La línea 105 lo admite en un comentario: `<!-- Static latency (per Q2=A decision) -->`. Es texto
fijo en el HTML: no hay petición de red detrás, ni temporizador, ni `/api/health` que lo alimente.
Lo de al lado sí es real — los nombres de modelo del panel `MODELOS` se piden al backend. Es
decir, la misma fila mezcla una medición auténtica y un número inventado, y el número inventado es
el que lleva la palabra "LATENCY".

En un portfolio leído por alguien técnico esto no es un detalle de diseño: es la diferencia entre
un sistema instrumentado y uno que presume. Abre las devtools y ese 12 ms no corresponde a nada.

**Arreglo (S).** Dos opciones honestas, y la segunda es la buena: medir `time.time()` en el
cliente entre el envío y el primer token, o sustituir la píldora por algo que no finja ser una
medición (`VOZ · IA · RAG`) y quitarla. "ONLINE" también sobra si nadie comprueba el estado. Si
decides instrumentarlo de verdad, es la tarea "Telemetría por etapa con time-to-first-token" que ya
figura en la Fase 4 del plan: hazla una vez y enciende la píldora con un número real.

### 1.2 El reintento amplifica el error que debería propagar

**`frontend/app.js:862-876`** (`fetchWithBackoff`) · Corrección

```js
const res = await fetch(url, options);
if (!res.ok) throw new Error(`HTTP ${res.status}`);   // <- cualquier 4xx/5xx
...
if (attempt === maxRetries) throw e;                  // maxRetries = 5
```

Reintenta cinco veces ante **cualquier** respuesta no exitosa, y cada reintento vuelve a enviar el
audio por el pipeline entero: transcripción, RAG, LLM y síntesis. No distingue:

- **429**: el servidor acaba de decir "para". Reintentarlo cinco veces con backoff exponencial
  agrava la saturación que lo causó. Es un amplificador.
- **413**: el audio no va a encoger. Y aquí es grave, porque nginx no tiene
  `client_max_body_size` (§2, fila 3), así que llega el 413 de su propio límite de 1 MB.
- **422**: la petición es inválida y lo seguirá siendo. Un 404 o un 401, igual.

Mientras tanto el estado dice `"Sin conexión - reintentando."`, que miente sobre la causa.

**Arreglo (S).** La opción más simple y más honesta es no reintentar en el cliente y dejar que el
backend sea el dueño de esa decisión, que es donde ya tiene la información correcta. Si lo
mantienes aquí: reintentar `429`, `502`, `503`, `504`, propagar el resto, y cortar el bucle en
cuanto `res.status === 413`.

### 1.3 El failover del LLM puede devolver la respuesta dos veces

**`backend/services/llm.py:146-155`** (`generate_stream`) · Corrección

```python
if self.google_api_key:
    try:
        yield from self._googleai_generate_stream(prompt, context, system_prompt)
        return
    except Exception as e:
        logger.warning("Google AI stream failed, falling back to OpenRouter: %s", e)
yield from self._openrouter_generate_stream(prompt, context, system_prompt)
```

El `try` envuelve el `yield from`, no solo la apertura de la conexión. Un generador es perezoso:
cada `yield` cede el control y lo devuelve al consumidor. Si Google se cae **después** de haber
emitido 40 tokens, esos 40 ya le llegaron al cliente. Salta la excepción, cae al `except`, y se
llama a OpenRouter, que emite la respuesta **entera** desde el principio.

El cliente recibe media respuesta de Google pegada a una completa de OpenRouter. Y como el
pipeline parte la respuesta en frases para el TTS, el usuario oye una respuesta que se contradice
a mitad. `generate()` (línea 137) no sufre esto: sin streaming, o falla antes de producir nada o ya
tiene la respuesta entera.

**Arreglo (M).** Mover el `try` para que solo cubra el primer token, como propone la Fase 2.6 del
plan. Un generador que ya ha emitido algo no puede reintentarse sin duplicar. Si se emitió texto,
propagar el error. **Y añadir el test de regresión que el plan ya menciona** (un fallo tras N
tokens no debe duplicar): ahora no existe, que es justo por lo que el bug lleva tanto tiempo
oculto — la suite está en verde porque nadie ejercita ese camino.

---

## 2. Mejoras de mayor valor

Ordenadas por relación valor/esfuerzo. **Evidencia**: `verificado` = comprobé el fichero o
ejecuté la comprobación; `lectura` = lectura estática sin ejecutar nada.

| # | Propuesta | Tipo | Evidencia | Esfuerzo | Coste continuo | Riesgo |
|---|---|---|---|---|---|---|
| 1 | Borrar `LATENCY 12ms` y `All Systems Online` (§1.1) | Corrección | verificado `index.html:106,138` | S | — | Ninguno |
| 2 | `fetchWithBackoff` solo reintenta 429/5xx (§1.2) | Corrección | verificado `app.js:862-876` | S | — | Bajo: pierde algún reintento útil |
| 3 | `client_max_body_size 6m;` en `nginx/interview.conf:56` | Corrección | verificado: ausente | S | — | Bajo. **Contradice el `[x]` de `PLAN.md` 1.4** |
| 4 | Fallback del LLM solo antes del primer token (§1.3) | Corrección | verificado `llm.py:146-155` | M | — | Bajo: obliga a gestionar el error en el front |
| 5 | Borrar las listas `keywords` de una palabra suelta | Corrección | verificado | S | — | Bajo: esa pregunta pasa al LLM |
| 6 | Rehacer o retirar las 4 respuestas cacheadas sin respaldo | Contenido | verificado | M (humano) | bajo demanda | **Alto si se hace mal** |
| 7 | Corregir el carácter CJK de `fraud-detector.md:97` | Contenido | verificado | S | — | Ninguno |
| 8 | Adjuntar el H1 a su sección en `rag.py:538` | Corrección | verificado | M | — | Bajo: cambia ids de chunk, hay que reindexar |
| 9 | Excluir `wiki/index.md` del corpus | Corrección | verificado: sin frontmatter | S | regenerar índice | Ninguno |
| 10 | `setStatus()` en los eventos `transcription` y `token` | Visual | verificado `app.js:1068,1070` | S | — | Ninguno |
| 11 | Estado real de las 8-12 s en el panel | Visual | lectura | S | — | Bajo |
| 12 | Quitar el auto-cierre a 5 s del panel de contexto | Visual | verificado `app.js:1208` | S | — | Ninguno |
| 13 | Subtítulos o texto del turno en el panel de contexto | Funcional | lectura | M | — | Bajo |
| 14 | Tests del proveedor Google y de la transición a OpenRouter | Corrección | verificado: 0 tests | M | mantener un test | Ninguno |
| 15 | Fijar el test inestable de `report.py` (§6) | Corrección | verificado por ejecución | S | — | Ninguno |
| 16 | Eliminar la fuga de errores del proveedor en las respuestas HTTP | Corrección | verificado `blocking.py:95,109` | M | — | Bajo. Contradice tu spec, línea 157 |
| 17 | Cap de concurrencia en `stt.transcribe` (`stt.py:42`) | Corrección | verificado: sin `Semaphore` | M | — | Bajo |
| 18 | Revisar `SEMANTIC_CACHE_TTL_DAYS` (14) contra `SESSION_TTL_HOURS` (2) | Corrección | verificado `config.py:87,107` | S | — | Bajo: es privacidad, no rendimiento |
| 19 | Embebir la consulta una vez por turno | Rendimiento | verificado `streaming.py:238,241` | S | — | Bajo |
| 20 | Harness de evaluación con 50 preguntas etiquetadas | Medición | no ejecutado | M | coste de API si lo lanzas | **Ninguno**: convierte el resto en datos |

Cuatro filas merecen explicación, porque el resto se lee solo:

- **Fila 3 es un descuido, no un hallazgo.** `PLAN.md` marca la Fase 1.4 como completada y su
  descripción incluye "fijar `client_max_body_size 6m;` en la `location /api/` de nginx". No está
  ahí, ni en `interview.conf` ni en `deployment/interviewtts.service`. La app sí tiene su límite
  (`MAX_AUDIO_SIZE = 5 MB`, `backend/uploads.py:20`, aplicado en `backend/main.py:254`), pero
  nginx corta antes con 1 MB, así que para peticiones grandes el límite de la app es código
  muerto. Y ese 413 ahora se reintenta cinco veces (§1.2). Tres líneas de configuración.
- **Fila 5 es la mejor relación valor/esfuerzo del documento.** En
  `backend/services/response_cache.py`, las listas `keywords` contienen palabras sueltas (`python`,
  `api`, `rest`, `tests`, `sql`, `rag`, `dam`, `docker`, `interviewtts`, `fortalezas`,
  `debilidades`, `presenta`, `aprendes`, `aprendiste`, `la ia`) y se buscan con
  `re.search(rf"\b{keyword}\b", pregunta)` sobre la pregunta entera. Las entradas se evalúan en
  orden y gana la primera. Resultado: "¿cuántos tests tiene el proyecto de fraude?" devuelve la
  respuesta de hábitos ("hago tests con pytest") y "¿qué API usasteis para el bot?" devuelve una
  respuesta sobre InterviewTTS. Son preguntas sobre tus proyectos reales que reciben la respuesta
  del proyecto equivocado. Las listas `phrases` no tienen ese problema: son subcadenas específicas.
  Borrar `keywords` es una línea por entrada y elimina la clase entera de fallo.
- **Fila 8, el mecanismo.** `rag.py:538` parte con `re.split(r'\n(?=#{1,3}\s)', content)`. El
  primer elemento es el H1 solo, porque el corte ocurre en el salto que precede a la siguiente
  cabecera. Como `len(section.split())` no llega a `chunk_size`, la línea 551 lo emite tal cual: un
  chunk de dos palabras que es solo el título del documento, y la línea 538 no lo reengancha a la
  sección que sigue. No he medido cuántas veces eso altera el ranking y no voy a inventar un
  `recall@k`. Lo que sí se puede afirmar es que el corpus contiene un fragmento por documento que
  no dice nada y contiene el título, y los títulos de tus proyectos son exactamente las palabras
  que usa un reclutador al preguntar.
- **Fila 20 es la de más valor y menos glamour.** `recall@k`, `MRR`, latencias por etapa y coste
  por entrevista no están medidos en ninguna parte. El informe de RAG produjo 50 preguntas
  etiquetadas y un arnés ejecutable, y nunca lo ejecutó. Sin eso, las filas 5, 8, 9 y 19 son
  cambios razonados, no mejoras demostradas.

---

## 3. Mejoras visuales que sí valen

La exploración visual encontró cosas reales y cosas que no se sostienen. Separo las dos.

### Reales

1. **El estado se congela durante toda la espera.** `app.js:992` pone `"Enviando audio…"` y el
   siguiente `setStatus` no llega hasta la línea 1024 (`"Escuchando…"`), que dispara `done`. Ni
   `transcription` (1068) ni `token` (1070) llaman a `setStatus`. El reclutador ve "Enviando
   audio…" durante los 8-12 s aunque la transcripción haya llegado y la respuesta ya se esté
   escribiendo palabra a palabra en pantalla. Tres llamadas lo arreglan y de paso cambian la
   percepción de la espera. Es la mejora visual de mayor impacto.
2. **El panel de contexto se cierra solo a los 5 s** (`app.js:1208-1211`), justo cuando el
   reclutador está mirando qué evidencia recovered el sistema. Y en escritorio el
   `#context-toggle` (304) **no hace nada**: `#context-panel.open` solo está definido dentro del
   `@media (max-width: 768px)` (`style.css:1122`), así que en escritorio la clase se añade y se
   quita sin efecto. El panel ya es columna visible del grid (`style.css:373`). Tres arreglos
   baratos: no auto-cerrar, y que el toggle no prometa lo que no hace.
3. **`.chunk-preview` no se recorta nunca.** Es un `<span>` (`app.js:1230`) dentro de un
   `.chunk-pill` que es bloque normal (`style.css:993`, sin `display: flex`). Sobre una caja en
   línea, `overflow: hidden` y `text-overflow: ellipsis` (`style.css:1030-1031`) no se aplican. El
   texto no lleva los puntos suspensivos que el CSS promete. Una línea: `display: block`.
4. **Los fragmentos de contexto no son accesibles con el teclado.** Son `<div>` con
   `onclick="toggleChunk(this)"` (`app.js:1228`), sin `tabindex` ni `role="button"`. Convertirlos
   en `<button>` resuelve teclado, foco y semántica de una vez.
5. **El transcript no dice quién habla.** `addMessage` crea avatar y color por lado, pero ningún
   texto nombra a la persona. Para un producto que es una conversación de dos, la etiqueta
   explícita es lo más barato que hay.
6. **No hay `aria-live` en ninguna parte.** No lo encontré ni en `index.html` ni en `app.js`. Una
   transcripción que se actualiza sola es el caso canónico de `aria-live`.

### Cosméticas

Hazlas si te apetece, no si buscas resultado. Burbujas a 0,85 rem = 13,6 px (`style.css:811`),
legibles: es estética, no corrección. `--bg-void: #000000` (`style.css:11`) rompe la paleta si el
resto es azul noche: una línea. Los colores del transcript son genéricos: personalizarlos es
cosmético puro. `playbackRate = 0.7 + ttsVolume * 0.9` (`app.js:488`) barre hasta 1,6×, que suena
artificial. El panel reserva 320 px (`--context-w`, `style.css:60`), un 23,4 % de 1366 px, para
tres fragmentos cortos: defendible en monitor, cuestionable en portátil.

### Tres afirmaciones de ese informe que **no** se sostienen

- **"`#orbital-ring` es estático porque su `class="ring-circle"` no casa con ninguna regla."** A
  medias. `#orbital-ring` está en `index.html:208` con `class="orbital-ring"` y **sí** cambia con
  el estado: `style.css:601-611` le pone opacidad 0,3 en reposo y 0,6 / 0,8 / 1,0 en processing /
  speaking / listening. Lo muerto es la clase `ring-circle` del `<circle>` interior (215), que no
  casa con nada: limpiar una clase sobrante, no un componente roto. Relacionado: `PLAN.md` sigue
  listando en su Fase 4 "mover `#orbital-ring` bajo `#avatar-wrapper`" como pendiente, pero ya
  está hecho y documentado en `index.html:200-207` y `style.css:585-588`. Esa entrada está
  obsoleta.
- **"`#waveform` fijo en 200 px desborda por debajo de 872 px."** No hay ningún breakpoint de
  872 px en `style.css`: los únicos `@media` son dos de 768 px y uno de `prefers-reduced-motion`.
  Y `#waveform` tiene `display: none` por debajo de 768 px (`style.css:1149`), precisamente para
  liberar espacio. No hay nada que arreglar.
- **"La cabecera móvil tiene cinco elementos (~390 px) y recortan END."** En móvil hay **tres**:
  `.latency` está en `display: none` (`style.css:1109`). Que END se recorte o no es una
  comprobación de navegador que nadie ha hecho, porque ninguna exploración pudo ejecutarse. Si te
  preocupa, míralo antes de tocar nada.

---

## 4. Contenido y veracidad de la wiki

Ninguna se puede automatizar: requieren que decidas qué es cierto. Es también donde más daño puede
hacer un automatizado mal hecho, porque una respuesta incorrecta sobre tu propia historia
profesional es la peor clase de bug posible en un portfolio.

### Las cuatro respuestas cacheadas que la wiki no respalda

Es el hallazgo con más consecuencias de los tres informes. En `backend/services/response_cache.py`
hay respuestas que se dicen al reclutador **literalmente, sin pasar por el LLM**, y por tanto sin
ninguna comprobación de coherencia con tu perfil. Cuatro afirman cosas que `grep` no encuentra en
ninguno de los 51 ficheros de `wiki/`:

| Respuesta cacheada | Qué afirma | Estado en `wiki/` |
|---|---|---|
| `¿qué sabes de bases de datos?` | "Trabajo con MySQL, PostgreSQL y **SQLite**" | **Cero apariciones de "SQLite"**. `skills/data.md:16-17` solo lista MySQL, PostgreSQL y MongoDB |
| `¿qué experiencia tienes con Python?` | "lo usé en **proyectos del DAM para bases de datos**" | La wiki te sitúa en Java/Hibernate/SQL-PL-SQL de DAM (`profile/mikel.md:35`, `skills/backend.md:18,20`) y Python en InterviewTTS: atribución intercambiada |
| `¿por qué elegiste DAM?` | "al descubrir la programación, **mi cabeza hizo click**" | Historia de origen inventada; no aparece en la wiki |
| `¿cuál es tu mayor logro?` | "Mi mayor logro… es ser autodidacta y resolutivo" | La wiki nunca formula un "mayor logro": es una evasiva disfrazada de respuesta |

Por qué importa más de lo que parece: la entrada de bases de datos **mezcla** algo que sí
respalda la wiki (`skills/backend.md:33` menciona "complex queries, triggers, stored
procedures") con algo que no (SQLite), y lo presenta todo como experiencia laboral. Y la de DAM
contiene una anécdota personal inventada que no puedes corregir porque no existe en ningún sitio.

**Qué hacer (M, es tarea tuya).** No lo arregles con un editor de textos: decide una por una si
la afirmación es cierta. Si es cierta y la wiki no lo dice, la respuesta correcta es **añadirla a
la wiki** (para que el RAG también la sepa), no borrarla. Si no es cierta, bórrala y deja que el
LLM responda desde la wiki. La alternativa —arreglar solo los textos y dejar `keywords`— es
peor, porque el fallo de enrutado seguiría mandando esas preguntas a la respuesta fija.

### El resto

- **`wiki/projects/fraud-detector.md:97`, corrupción de codificación.** Contiene `o??arias`, que
  son dos caracteres CJK: U+8003 y U+8651 (考虑), restos de una doble codificación de
  "considerarías". Sobrevive al filtro porque `strip_placeholders` solo busca
  `\[\s*TODO\b[^\]]*\]` (`rag.py:107`) y esto no es un marcador. Va al LLM. Y la línea es un
  `[TODO: ask Mikel]`: tras filtrar el marcador **queda la pregunta sin responder** servida como
  si fuera contenido, bajo un encabezado titulado "What I'd do differently". El filtro quita la
  señal de que falta información y deja el agujero más difícil de detectar. Es el argumento más
  fuerte para responder los `[TODO]` pendientes (Fase 3.1, sigue abierta).
- **`confidence: low` nunca se dispara.** Hay 31 páginas `high`, 14 `medium` y **ninguna** `low`.
  La rama de `rag.py:514-524` es una guarda que hoy no hace nada: no la llames código muerto, es
  una protección correcta. Pero conviene saber que las 14 páginas `medium` se sirven **sin
  filtrar** y que nadie ha medido si son fiables. Es la tarea humana de la Fase 3.1.
- **Ninguna de las tres exploraciones midió calidad de recuperación.** Todo lo anterior sobre
  chunking y enrutado es razonamiento sobre la estructura del código, no medición.

---

## 5. Lo que no hay que construir

Esta sección vale tanto como las propuestas. El producto funciona y las tres exploraciones
coinciden en que el problema no es la falta de piezas.

| Propuesta | Por qué no |
|---|---|
| **Endpoint HTTP para los informes** | Es lo más grande de las tres exploraciones y aun así no lo recomiendo **ahora**. `report.py:4` dice "no HTTP surface" y es cierto: las seis rutas del proyecto no exponen informes. Es funcionalidad, no bug, y una ruta de descarga es un producto nuevo con su propio modelo de amenazas. Está bien que no exista; si algún día lo quieres, hazlo con la deliberación que exige exponer datos personales |
| **Base de datos vectorial o reranker** | El corpus son 37 documentos reales. Tu `RESUMEN.md` mide el RAG en torno a 15 ms sobre 8-12 s: un 0,13 %. No hay escala que una base vectorial resuelva |
| **Embedder multilingüe para la caché semántica** | Resuelve un problema que ya decidiste no tener. El comentario de `main.py:127-146` dice que con umbral 0,93 no puede acertar nunca y propone apagarla. Apágala: es más honesto que buscarle un modelo mejor |
| **Detección automática de idioma en el STT** | Producto en español, candidato español, reclutador español. No hay problema que esto resuelva |
| **WebSockets en lugar de SSE** | SSE funciona, es más fácil de depurar y ya está cableado. Cambiarlo es reescribir el streaming para no ganar nada medible |
| **Autenticación** | Es un portfolio de un candidato en un VPS. Añadir auth es superficie de ataque, no reducción |
| **Mostrar el coste por turno al reclutador** | Interesante para ti, insultante para quien entrevista. Y no tienes números medidos que mostrar |
| **Más animación, cabeza 3D, coach mark de primer arranque** | Ruido. La animación existente cumple su función |
| **Colapsar el raíl, o volcar los fragmentos del RAG en el transcript** | Problemas ya resueltos. El raíl es tu panel de evidencia, que es lo que hace creíble al sistema; y volcar los fragmentos en el transcript se revirtió porque el TTS leía `"[Source: ...]"` en voz alta |
| **Biblioteca de animación en JS** | El problema de percepción de la espera son tres llamadas a `setStatus`, no 40 KB de dependencias |
| **Sesenta ideas más** | Este es el punto. Tienes 391 tests, un pipeline completo y seguridad limpia. El déficit no es de alcance, es de honestidad: dos números inventados, un reintento que amplifica errores y un failover que cose respuestas |

---

## 6. Estado de verificación

Existe porque las tres exploraciones **no pudieron ejecutarse**: A y C no tenían shell, y B no midió
nada. Todo lo de aquí lo comprobé yo contra los ficheros.

### Verificado por ejecución

- **La suite no está en verde de forma fiable.** `PLAN.md` y `auditoria/README.md:10` dicen
  "380 / 390 tests en verde". Mido **1 fallo, 390 pasan** (391 recogidos). El fallo es
  `tests/test_report_service.py::TestReportServiceCleanup::test_cleanup_custom_days_override`, y es
  **inestable**: lo vi fallar en la ejecución completa de los 25 ficheros, en 3 de 12 ejecuciones del
  fichero solo, y en 1 de 8 en otro par. Al reejecutarlo pasa.
  Causa raíz **medida**, no supuesta: `ReportService.cleanup_expired` compara
  `f.stat().st_mtime <= cutoff` con `cutoff = time.time() - days*86400`, y el test siembra el
  fichero con `write_text` (mtime natural, sin `os.utime`) esperando que `days=0` lo borre. En
  Windows el mtime del sistema de ficheros puede quedar **por delante** de un `time.time()`
  posterior: en 5000 escrituras, **613 (12 %) tienen `st_mtime > cutoff`**, con un desvío máximo de
  **2,38e-07 s**. Es una carrera de 238 nanosegundos. **Producción no está afectada** (con 30 días
  de retención ningún informe real está a 238 ns de expirar), pero tu red de seguridad tiene un test
  que falla solo, y eso erosiona la confianza en la red que te está protegiendo mientras cambias
  código.
- **El venv existe** (`venv/Scripts/python.exe`). Una de las tres exploraciones afirmó que no
  había entorno virtual, y de ahí sazonó su lectura. Es falso. Sus afirmaciones sobre el número de
  tests **no las he dado por buenas**: el único recuento en el que baso algo es el mío.
- La raíz de la corrupción CJK: bytes `E6 80 8B E6 99 91` = U+8003 + U+8651 en `fraud-detector.md:97`.

### Verificado leyendo el fichero exacto

Sin ambigüedad posible, aunque sin ejecutar el comportamiento: los tres de §1 con su `file:line` ·
`client_max_body_size` ausente de `nginx/interview.conf` y `deployment/interviewtts.service` ·
`MAX_AUDIO_SIZE = 5 MB` en `uploads.py:20` aplicado en `main.py:254` · `tts.py:8` importa solo
`edge_tts`, mientras `README.md:44,135` presentan Piper como primario: **el README miente aquí** ·
`blocking.py:95,109` interpolan la excepción cruda en el `detail` HTTP y `llm.py:213,246` la
construyen con el `error_body` del proveedor, violando
`openspec/specs/conversation-engine/spec.md:157` · las seis rutas del proyecto, ninguna expone
informes · `stt.py` sin `Semaphore` ni `Lock` · ausencia de `.github/` · los conteos de la wiki
(31 `high`, 14 `medium`, 0 `low`; 51 ficheros; 0 apariciones de "SQLite") · 0 tests que ejerciten
`_googleai_generate`.

### Sin medir, y no hay que inventarlo

`recall@k`, `MRR`, precisión del chunking: ningún número. Latencia por etapa y si los "8-12 s" del
README son ciertos: ningún número. Coste por entrevista, en euros o en tokens: ningún número.
Cuánto cuesta en milisegundos el doble embebido: describí el trabajo duplicado, no su coste. **Todo
lo que un navegador muestra**: ninguna de las tres exploraciones abrió el navegador, así que
cualquier afirmación sobre lo que se ve en pantalla —incluido si END se recorta en móvil— es una
predicción, no un hecho.

### Dos correcciones que le debo a los informes

- **La afirmación sobre Whisper es incorrecta.** El informe de capacidad funcional dice que "el
  README afirma Whisper medium + float16 mientras la config dice small/int8". No es así:
  `README.md:41` dice correctamente "int8 quantization, default `small`", y `:150` dice
  explícitamente "The config default is now `small`, and the test verifies it". Lo de `:170` es una
  **fila de una tabla de opciones consideradas**, no una afirmación sobre la configuración actual.
  El README es correcto aquí; el informe leyó mal la tabla.
- **No he podido confirmar el "test que fija la violación".** El informe dice que un test asegura la
  fuga de errores del proveedor. Busqué en `tests/` y no hay ninguno que compruebe el `detail` de
  una respuesta HTTP por ese camino: `test_llm.py:89` mira la excepción del servicio y
  `test_max_body.py:227` es del límite de tamaño. Puede existir con otra forma; no lo encontré, así
  que no lo afirmo en ninguna dirección.

---

## 7. Si solo haces tres cosas

En este orden, y el orden es la parte importante.

**1. Arregla `fetchWithBackoff` (`app.js:862-876`).** Primero, y no por ser el más grave, sino
porque es el que cambia el carácter del sistema. Un límite de tasa que se reintenta cinco veces no
es un bug: es un producto que no sabe decir "para". Cuando alguien agota el presupuesto de
peticiones y ve cinco intentos con "Sin conexión - reintentando.", lo que aprende de tu ingeniería
no es lo que quieres. Además es el que multiplica el 413 de nginx (fila 3), así que arreglarlo
desactiva media amplificación. Suele ser un día, y el test de regresión es fácil: simular un 429 y
contar las peticiones.

**2. Quita la telemetría inventada (`index.html:106`).** Segundo, y por una razón que no es de
código: es la acción con mejor relación entre esfuerzo y reputación del documento. Son **dos líneas
que se borran**. La alternativa es instrumentar de verdad el time-to-first-token y encender la
píldora con un número real, pero si eliges entre las dos cosas, borra. Va después del punto 1 por
dependencia: cuando `fetchWithBackoff` deje de reintentar, el único número que verás en pantalla
durante una entrevista real será el que decidas poner ahí.

**3. Cierra el ciclo del chunking del RAG.** Tercero, porque es el mayor riesgo de **daño real de
contenido** y no debe ir en un commit apresurado. Cuatro cambios, todos en el mismo eje:
`response_cache.py` (borra las listas `keywords`) · las cuatro respuestas sin respaldo, una a una,
decidiendo tú la verdad · el CJK de `fraud-detector.md:97` · `rag.py:538` (H1) y excluir
`index.md`. El orden interno importa: **borra `keywords` primero, en un commit aparte**. Es el
cambio que más superficie arregla por línea escrita y el más rápido de revertir si algo va mal. Las
cuatro respuestas y el H1 tocan lo que el LLM cree que es verdad sobre tu vida profesional, y eso
merece un commit propio y revisado.

**Por qué no empieza por el LLM duplicado (§1.3).** Es un bug real y de los más duplicados del
proyecto, pero requiere que Google caiga **a mitad de stream**, que es raro. El reintento ocurre
cada vez que alguien llega a su límite. El duplicado es el más interesante; no es el más frecuente,
y esa distinción decide el orden.

---

## Anexo: estado del repositorio

`git status` muestra 12 ficheros de `wiki/` modificados y dos directorios sin seguimiento
(`.codegraph/`, `wikiDocs/`) **preexistentes a esta auditoría**; no los he tocado. Este documento,
`auditoria/MEJORAS.md`, es el único fichero que he creado. No he modificado código, tests ni `wiki/`.
