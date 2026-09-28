# Backlog de trabajo — InterviewTTS

**Estado 2026-09-28:** **559 pytest + 2 xfailed, 89 node, 0 fallos.**

Regla del loop: **correcciones primero, mejoras después, visual al final.**
En cada ciclo: spec → build → test → juez.

---

## 1. Correcciones pendientes

✅ = cerrado y verificado.

| # | Corrección | Estado |
|---|---|---|
| C1 | `nginx` sin `client_max_body_size` | ✅ `3870547` — `5m` en `location /api/`, con test de consistencia config↔código |
| C2 | Clave de idempotencia en el turno | 🔶 trade-off aceptado: un fallo de red ambiguo cuesta un reintento manual. Es deliberado |
| C3 | Suites node fuera de `NODE_TESTS` | ✅ `68d0df1` — ahora se descubren por directorio, no lista. Las 4 restantes estaban verdes |
| C4 | Fuga de excepciones internas al cliente | ✅ `acaa7b7` — 13 rutas, payload genérico + log con `exc_info=True` |
| C5 | Patches de `periodic_cleanup` en el módulo equivocado | ✅ `49eb423` — repuntados a `backend.maintenance`, effectiveness probada por mutación |
| C6 | `store_is_configured` toca `_enabled` | ✅ `b606696` — accesor público `is_enabled()` con fallback honesto |
| C7 | Tests escriben en DB y `reports/` reales | 🔶 **peor de lo que decía: 1072 directorios en `reports/`** (no 378). Parcial: los 4 de `periodic_cleanup` aislados. Quedan los fixtures de streaming que tocan `AUDIO_DIR` real |
| C8 | El turno de despedida no se cuenta | ✅ `a26c900` — nuevo evento `turn_recorded` |
| C9 | `threshold=0.3` inerte | 🔶 sin hacer. El coseno top-1 más bajo observado es 0.414 |
| C10 | Sin CI | 🔶 sin hacer |
| C11 | Flake de mtime en NTFS | ✅ `83e3389` — **verificado: 0 fallos en 20 runs** (antes 2/20) |

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

---

## 5. Cambio de contrato — RESUELTO

`a26c900` había introducido `turn_recorded` para contar el turno de despedida, con el
argumento de que *"el goodbye no debe hacer cola detrás de un disco lento"*. **Ese
argumento nunca se midió, y es falso.**

| Operación | Mediana | p95 |
|---|---|---|
| `tts_service.synthesize()` del goodbye | **~1300 ms** | — |
| `persistence.record_turn()` vía `to_thread` | **~5.8 ms** | ~8.9 ms |

La despedida **ya esperaba 1,3 s al TTS** antes de sonar. La escritura es el **0,4%**
de un flujo que ya supera el segundo. El disco nunca fue el cuello de botella.

Y el evento posterior tenía un coste real: un cliente que cierra el stream al ver
`interview_end` —que es lo lógico, si lo lee como terminal— **pierde `turn_recorded`**,
y el contador vuelve a quedar corto. Es el mismo bug arreglado desde el otro lado.

**Resuelto en `1c99ec0`:** la escritura va **antes** de `interview_end`, el número
confirmado viaja en el propio `interview_end`, y `turn_recorded` desaparece.
*"El último evento es terminal"* vuelve a ser cierto sin excepciones, y los cinco
asserts que `a26c900` tuvo que debilitar están restaurados en su forma original.

**Coste real:** 5,8 ms. **Beneficio:** contrato intacto, un solo camino de código para
ambos eventos terminales, y desaparece un evento que el cliente podía no recibir.
