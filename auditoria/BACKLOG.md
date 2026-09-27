# Backlog de trabajo — InterviewTTS

Estado al cierre de la sesión del 2026-09-27. **485 pytest + 2 xfailed, 89 node, 0 fallos.**

Regla del loop: **correcciones primero, mejoras después, visual al final.**
En cada ciclo: spec → build → test → juez.

---

## 1. Correcciones pendientes

Verificar que cada una sigue siendo real antes de actuar.

| # | Corrección | Dónde | Nota |
|---|---|---|---|
| C1 | `nginx` sin `client_max_body_size` | `nginx/interview.conf` | El default de 1MB anula el techo de 5MB de la app. La guarda de la Fase 1 está medio muerta, y el 413 resultante se reintentaba 5× (ya no, tras el fix de reintentos) |
| C2 | Sin clave de idempotencia en el turno | `backend/turns/`, `persistence.py` | Un fallo de red ambiguo cuesta un reintento manual. Es el trade-off explícito del fix de reintentos |
| C3 | 2 de 6 suites node en `NODE_TESTS` | `tests/test_sse_terminal_state.py` | 20 tests corren solo con el glob explícito |
| C4 | Test afirma una violación del spec | `tests/test_sse_terminal_state.py:236` | El spec dice que los errores NO deben incluir paths ni detalles internos; el test asegura que sí |
| C5 | 4 tests de `periodic_cleanup` parchean el módulo equivocado | `tests/test_conversation_memory.py` | Apuntan a `backend.main.*`; el código está en `backend/maintenance.py`. Pasan por identidad de módulo |
| C6 | `store_is_configured` toca un atributo privado | `backend/conversation.py:177` | `persistence._enabled`. Un rename silencioso cambia la semántica del fallback |
| C7 | Tests escriben en la DB y `reports/` reales | varios | 378 directorios hex acumulados en `reports/` |
| C8 | El turno de despedida no se cuenta | `backend/turns/streaming.py`, `frontend/app.js` | Se persiste pero no llega al contador |
| C9 | `threshold=0.3` inerte | `backend/services/rag.py` | El coseno top-1 más bajo observado es 0.414; nunca filtra |
| C10 | Sin CI | — | Nada demuestra que la suite está verde en HEAD |
| C11 | Flake de mtime en NTFS | `backend/services/report.py:64-66` | Mecanismo medido: 2,38e-07s. Opciones: epsilon en el `<=`, o `os.utime()` en el test |

---

## 2. Mejoras de producto

Solo cuando no queden correcciones.

| # | Mejora | Dónde | Estado |
|---|---|---|---|
| M1 | `"cuéntame más sobre InterviewTTS"` sigue cacheando y repite la intro | `response_cache.py`, keyword `interviewtts` | **Requiere decisión del dueño** — quitarla rompe una pregunta legítima |
| M2 | Retirar la caché semántica | `semantic_cache.py` | Medido: 0% de recall a 0.93; las distribuciones se solapan, ningún umbral sirve |
| M3 | Doble embedding por turno | `backend/turns/`, `rag.py` | 2× en streaming, 4× en el primer turno. Ganancia pura de eficiencia |
| M4 | Embedder multilingüe | `backend/services/rag.py` | La causa real de la dilución en FAQ: `all-MiniLM-L6-v2` es inglés puntuando español. No un cambio de config |
| M5 | Telemetría por etapa | `backend/turns/streaming.py` | `_t_stt`/`_t_rag`/`_t_llm` se registran pero no se exponen. El pill de TTFT ya hace el trabajo clave |

---

## 3. Mejoras visuales

Ordenadas por impacto sobre lo que el reclutador percibe.

### 3.1 El problema de la espera (el mayor)

- `setStatus` nunca se llama en los eventos `transcription` ni `token` → el reclutador ve **"ENVIANDO AUDIO…" los 8-12s completos**, una frase que es falsa casi todo ese tiempo
- El indicador de escritura aparece *antes* de la subida, cuando el servidor está transcribiendo
- `turn.settle("done")` escribe "Escuchando…" mientras aún quedan chunks de audio en cola
- `#orbital-ring` es estático: su clase `ring-circle` **no casa con ninguna regla CSS**

### 3.2 Layout y responsive

- Cabecera móvil: elementos que no envuelven, ~390px en viewport de 375/390px, **recorta END, la única forma de terminar una entrevista en móvil**
- La burbuja de mensaje (38.4px) **no cabe** en la ventana de scroll (~39px) a 1366×768
- El avatar se dimensiona por ancho (`100%`) pero su presupuesto se gasta en alto; `vh` no aparece en el CSS
- `#waveform` fijo en 200px se desborda por debajo de 872px de ancho
- El panel de contexto reserva 320px (23% de un viewport de 1366px) permanentemente

### 3.3 Accesibilidad

- Falta `aria-live` en `#conversation` y `#status`
- `aria-label` del botón de micro sigue diciendo "Iniciar entrevista" cuando ya es botón de parada
- `.chunk-pill` son `div` con `onclick` inline: sin teclado, sin `role`
- `text-overflow: ellipsis` sobre un elemento inline no hace nada
- Sin "saltar al último" en conversaciones largas

### 3.4 Sistema de diseño

- Burbujas de 0.85rem = 13.6px, por debajo de un suelo de lectura cómodo para el contenido principal
- Sin etiquetas de hablante: usuario vs candidato se distingue solo por lado y un glifo genérico
- Paleta azul/índigo genérica en el transcript — los dos colores más reconocibles de "IA generada"
- `--bg-void: #000000` es negro puro y rompe la paleta; la columna central queda más oscura que la página
- La rejilla "Mission Control" y la viñeta están **ocultas** bajo cuatro paneles opacos
- `--cyan-accent` declarado "solo zona avatar" pero aparece en `#status`, el cursor, el VU meter y el focus ring
- Valores de espaciado (12, 10, 8, 6, 4px) fuera de la escala

### 3.5 Confianza y honestidad

- El sistema **nunca instruye al modelo a seguinte que es una IA**, y sus primeras palabras habladas son "Soy Mikel, desarrollador junior DAM"
- El único señal honesto es un overlay dismissible
- `playbackRate` llega a 1.6× → se lee como glitch, no como boca
- El video de "hablando" se resetea a `currentTime = 0.3` en cada sentence → la cabeza da saltos visibles

### 3.6 Rendimiento de frontend

- El `requestAnimationFrame` de 60fps **nunca se cancela**, asigna un `Uint8Array` por frame y hace 64 `setAttribute` sobre 32 rects
- Falta `<link rel="preconnect">` para `fonts.gstatic.com` y `cdn.jsdelivr.net`
- Tailwind Play CDN cargado sin usar una sola utility class

---

## 4. Pendiente del dueño (no mío)

1. **Desplegar en el VPS**: emitir el certificado TLS y subir `nginx/interview.conf` + `interviewtts.service`. El redirect 80→443 va comentado a propósito.
2. **Las 6 afirmaciones sin respaldo** en `response_cache.py` (líneas 189, 269, 308, 230, 283, 202) — parked explícitamente.
3. **La decisión del keyword `interviewtts`** (M1).
4. **Si corrige o no "diferentes establecimientos"** en la respuesta de trabajo en equipo (línea 257).
