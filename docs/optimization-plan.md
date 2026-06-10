# Plan de Optimización: Conversación Fluida

## Objetivo
Reducir la latencia de ~10-13s a ~3-5s manteniendo la calidad de respuestas.

## Análisis Actual

```
Pipeline secuencial:
Audio → STT (1-2s) → RAG (<0.1s) → LLM (4-8s) → TTS (2-3s) → Response
```

**Cuellos de botella:**
- LLM OpenRouter: 4-8s (70% del tiempo total)
- TTS Edge-TTS: 2-3s (20% del tiempo total)
- STT Whisper: 1-2s (10% del tiempo total)

**Principio:** No podemos cambiar el modelo LLM (calidad), pero sí cómo lo consumimos.

---

## Sprint 1: Medición y Quick Wins (30 min)

### Objetivo
Tener datos concretos y aplicar optimizaciones inmediatas sin cambios arquitectónicos.

### Tareas

#### 1.1 Timing Logs en Pipeline
**Archivo:** `backend/main.py`

Agregar timestamps en cada paso del endpoint `send_message`:
```python
t0 = time.time()
# STT
t1 = time.time()
# RAG
t2 = time.time()
# LLM
t3 = time.time()
# TTS
t4 = time.time()
logger.info(f"Pipeline timing: STT={t1-t0:.2f}s, RAG={t2-t1:.2f}s, LLM={t3-t2:.2f}s, TTS={t4-t3:.2f}s, Total={t4-t0:.2f}s")
```

**Entregable:** Logs muestran breakdown exacto por turno.

#### 1.2 Reducir LLM_MAX_TOKENS
**Archivo:** `backend/config.py` y `.env`

Cambiar default de 500 a 300 tokens. Para entrevistas, respuestas concisas son mejores.

```python
self.LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "300"))
```

**Impacto:** Reduce ~20-30% del tiempo de LLM.

#### 1.3 Pre-warm LLM Connection
**Archivo:** `backend/main.py` (lifespan)

Al arrancar, hacer una llamada dummy al LLM para "calentar" la conexión:
```python
# Pre-warm LLM connection
try:
    llm_service.generate(prompt="ping", context="", system_prompt="")
    logger.info("LLM connection pre-warmed")
except Exception as e:
    logger.warning(f"LLM pre-warm failed: {e}")
```

**Impacto:** Primera llamada de entrevista es ~1-2s más rápida.

#### 1.4 Optimizar Whisper Parameters
**Archivo:** `backend/services/stt.py`

Reducir `beam_size` de 5 a 3 (margen de precisión vs velocidad):
```python
segments, info = self._model.transcribe(
    str(audio_path),
    beam_size=3,  # was 5
    language=None,
    vad_filter=True,
)
```

**Impacto:** STT ~20-30% más rápido con pérdida mínima de precisión.

#### 1.5 Considerar Whisper tiny
**Archivo:** `.env`

Opción: cambiar `WHISPER_MODEL=base` a `WHISPER_MODEL=tiny`.

**Tradeoff:** 
- tiny: ~0.5s vs base: ~1.5s
- tiny es menos preciso en acentos/ruido
- **Recomendación:** Mantener `base` si hay ruido ambiental, `tiny` si ambiente controlado.

### Criterios de éxito Sprint 1
- Logs muestran timing breakdown
- Latencia total reducida ~15-25%
- No hay regresión en calidad de transcripción

---

## Sprint 2: LLM Streaming (1-2h)

### Objetivo
El usuario ve el texto de la respuesta aparecer letra por letra mientras el LLM genera. Reduce latencia percibida de ~10s a ~2-3s.

### Arquitectura

```
Frontend ←SSE← Backend ←streaming← OpenRouter LLM
   ↓
   Muestra texto en vivo
   (audio espera al final)
```

### Tareas

#### 2.1 Backend: Streaming LLM Service
**Archivo:** `backend/services/llm.py`

Agregar método `generate_stream()` que use `httpx` streaming:
```python
def generate_stream(self, prompt, context, system_prompt):
    """Generator that yields text chunks as they arrive."""
    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST",
            f"{OPENROUTER_BASE}/chat/completions",
            headers={...},
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,  # Enable streaming
                ...
            },
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
```

**Entregable:** Método que genera tokens uno por uno.

#### 2.2 Backend: SSE Endpoint
**Archivo:** `backend/main.py`

Nuevo endpoint que stremea la respuesta:
```python
@app.post("/api/conversation/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, audio: UploadFile = File(...)):
    # ... validación igual que antes ...
    
    async def event_generator():
        # STT (no streaming, espera resultado)
        user_text = await asyncio.to_thread(stt_service.transcribe, temp_audio)
        yield sse_event("transcription", {"text": user_text})
        
        # RAG
        context = rag_pipeline.get_context_string(user_text)
        
        # LLM streaming
        full_response = ""
        async for chunk in llm_service.generate_stream_async(...):
            full_response += chunk
            yield sse_event("token", {"text": chunk})
        
        # TTS (espera texto completo)
        audio_path = await tts_service.synthesize(full_response, ...)
        yield sse_event("audio", {"url": f"/audio/{audio_path.name}"})
        
        yield sse_event("done", {})
    
    return EventSourceResponse(event_generator())
```

**Dependencia:** Instalar `sse-starlette` para `EventSourceResponse`.

#### 2.3 Frontend: Consumir SSE
**Archivo:** `frontend/app.js`

Reemplazar `fetch` por `EventSource` o `fetch` con streaming:
```javascript
async function processRecording() {
    // ... preparar audio ...
    
    const response = await fetch(`${API_BASE}/api/conversation/${conversationId}/message/stream`, {
        method: 'POST',
        body: formData,
    });
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    
    // Crear div para mostrar texto en vivo
    const messageDiv = addMessage('candidate', '', null);
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        // Parsear SSE events
        for (const line of chunk.split('\n')) {
            if (line.startsWith('data: ')) {
                const event = JSON.parse(line.slice(6));
                if (event.type === 'transcription') {
                    addMessage('user', event.data.text);
                } else if (event.type === 'token') {
                    fullText += event.data.text;
                    messageDiv.querySelector('p').textContent = fullText;
                } else if (event.type === 'audio') {
                    // Agregar audio al mensaje
                    const audio = document.createElement('audio');
                    audio.src = event.data.url;
                    audio.controls = true;
                    audio.autoplay = true;
                    messageDiv.appendChild(audio);
                }
            }
        }
    }
}
```

**Entregable:** Usuario ve texto aparecer en tiempo real.

### Criterios de éxito Sprint 2
- Texto de respuesta aparece letra por letra (< 3s hasta primer token)
- Audio se reproduce al final (igual que antes)
- Latencia percibida: ~2-3s vs ~10s anterior

---

## Sprint 3: TTS por Oraciones (2-3h)

### Objetivo
Mientras el LLM sigue generando texto, empezamos a generar TTS de las oraciones completas. Reduce latencia de audio de ~10s a ~5-6s.

### Arquitectura

```
LLM streaming → detectar oración completa → TTS paralelo → audio chunk listo
     ↓                                            ↓
  sigue generando                          se guarda para streaming
```

### Tareas

#### 3.1 Detector de Oraciones
**Archivo:** `backend/services/llm.py` (nuevo helper)

```python
class SentenceBuffer:
    """Buffers LLM tokens and yields complete sentences."""
    
    def __init__(self):
        self.buffer = ""
        self.sentence_endings = {'.', '!', '?', '\n'}
    
    def add_token(self, token: str) -> Optional[str]:
        """Add token to buffer. Returns complete sentence if found."""
        self.buffer += token
        for i, char in enumerate(self.buffer):
            if char in self.sentence_endings:
                sentence = self.buffer[:i+1].strip()
                self.buffer = self.buffer[i+1:]
                if sentence:
                    return sentence
        return None
    
    def flush(self) -> Optional[str]:
        """Return remaining buffer as final sentence."""
        sentence = self.buffer.strip()
        self.buffer = ""
        return sentence if sentence else None
```

#### 3.2 TTS Paralelo por Oración
**Archivo:** `backend/services/tts.py`

Agregar método para sintetizar oración individual:
```python
async def synthesize_sentence(self, text: str, sentence_id: int) -> tuple[int, Path]:
    """Synthesize a single sentence, return (id, path)."""
    output_path = self.output_dir / f"sentence_{uuid.uuid4().hex}.mp3"
    communicate = edge_tts.Communicate(text, self.voice)
    await communicate.save(str(output_path))
    return sentence_id, output_path
```

#### 3.3 Pipeline Integrado con TTS Paralelo
**Archivo:** `backend/main.py`

En el endpoint streaming, mientras LLM genera, lanzar TTS en paralelo:
```python
async def event_generator():
    # ... STT, RAG ...
    
    sentence_buffer = SentenceBuffer()
    tts_tasks = []
    sentence_id = 0
    
    async for chunk in llm_service.generate_stream_async(...):
        yield sse_event("token", {"text": chunk})
        
        sentence = sentence_buffer.add_token(chunk)
        if sentence:
            # Lanzar TTS para esta oración en paralelo
            task = asyncio.create_task(
                tts_service.synthesize_sentence(sentence, sentence_id)
            )
            tts_tasks.append(task)
            sentence_id += 1
    
    # Flush última oración
    last_sentence = sentence_buffer.flush()
    if last_sentence:
        task = asyncio.create_task(
            tts_service.synthesize_sentence(last_sentence, sentence_id)
        )
        tts_tasks.append(task)
    
    # Esperar todos los TTS y stremear audio chunks
    for task in tts_tasks:
        sid, audio_path = await task
        yield sse_event("audio_chunk", {
            "id": sid,
            "url": f"/audio/{audio_path.name}"
        })
    
    yield sse_event("done", {})
```

#### 3.4 Frontend: Buffer de Audio Chunks
**Archivo:** `frontend/app.js`

Recibir chunks de audio y reproducirlos secuencialmente:
```javascript
let audioQueue = [];
let isPlaying = false;

async function playAudioChunks() {
    if (isPlaying || audioQueue.length === 0) return;
    isPlaying = true;
    
    while (audioQueue.length > 0) {
        const chunk = audioQueue.shift();
        const audio = new Audio(chunk.url);
        await new Promise(resolve => {
            audio.onended = resolve;
            audio.onerror = resolve;
            audio.play();
        });
    }
    
    isPlaying = false;
    // Auto-restart listening
    if (isInterviewActive) startListening();
}

// En el SSE handler:
if (event.type === 'audio_chunk') {
    audioQueue.push(event.data);
    playAudioChunks(); // Empieza a reproducir si no está reproduciendo
}
```

### Criterios de éxito Sprint 3
- Primera oración de audio se reproduce en ~3-4s
- Audio es continuo (sin gaps notables entre oraciones)
- Latencia total percibida: ~3-5s

---

## Sprint 4: Optimizaciones Avanzadas (opcional, 1-2h)

### 4.1 Pre-fetch RAG Context
Mientras STT procesa, pre-calcular embeddings de chunks candidatos.

### 4.2 Conexión Keep-Alive para Edge-TTS
Reutilizar sesión HTTP para múltiples llamadas TTS.

### 4.3 Audio Concatenation en Backend
En lugar de stremear chunks separados, concatenar en un solo archivo y stremear ese.

### 4.4 Whisper GPU (si disponible)
Si el usuario tiene GPU, cambiar `WHISPER_DEVICE=cuda` y `WHISPER_COMPUTE_TYPE=float16`.

---

## Resumen de Impacto Esperado

| Sprint | Latencia Total | Latencia Percibida | Esfuerzo |
|--------|----------------|-------------------|----------|
| Actual | ~10-13s | ~10-13s | - |
| Sprint 1 | ~8-10s | ~8-10s | 30 min |
| Sprint 2 | ~8-10s | ~2-3s (texto) | 1-2h |
| Sprint 3 | ~5-7s | ~3-4s (audio) | 2-3h |
| Sprint 4 | ~4-6s | ~3-4s | 1-2h |

**Meta final:** Conversación con latencia de ~3-5s, comparable a una videollamada con delay.

---

## Archivos a Modificar

### Sprint 1
- `backend/main.py` (timing logs, pre-warm)
- `backend/config.py` (max_tokens default)
- `backend/services/stt.py` (beam_size)
- `.env` (LLM_MAX_TOKENS, opcional WHISPER_MODEL)

### Sprint 2
- `backend/services/llm.py` (generate_stream)
- `backend/main.py` (SSE endpoint)
- `backend/requirements.txt` (sse-starlette)
- `frontend/app.js` (SSE consumer)

### Sprint 3
- `backend/services/llm.py` (SentenceBuffer)
- `backend/services/tts.py` (synthesize_sentence)
- `backend/main.py` (parallel TTS pipeline)
- `frontend/app.js` (audio queue)

### Sprint 4
- Varios, según optimización específica

---

## Criterios de Calidad (no negociables)

1. **Transcripción STT:** No reducir precisión por debajo de 95%
2. **Calidad LLM:** No cambiar modelo, solo optimizar consumo
3. **Calidad TTS:** Voz natural, sin cortes entre oraciones
4. **UX:** Usuario no debe notar "chunks", debe sentir conversación fluida

---

## Orden de Ejecución

1. **Sprint 1** → Medir y aplicar quick wins
2. **Sprint 2** → Streaming de texto (mayor impacto percibido)
3. **Sprint 3** → TTS por oraciones (impacto en audio)
4. **Sprint 4** → Solo si quedan cuellos de botella después de Sprint 3

---

## Notas para el Ejecutor

- Cada sprint es independiente y puede probarse antes de continuar
- Los tests deben pasar después de cada sprint
- Si Sprint 2 funciona bien, evaluar si Sprint 3 es necesario
- Documentar cualquier cambio en `.env.example`
