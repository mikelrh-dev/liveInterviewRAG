# 🎤 Plan: Entrevista de Voz con IA

> Proyecto de portfolio para junior DAM — Fullstack + IA + Voz
> Estado: Planificación | Inicio: Junio 2026

---

## 📋 Visión general

Una aplicación web que simula una entrevista de trabajo con IA. El usuario habla por el micrófono, la IA transcribe, genera una pregunta/respuesta como entrevistador, y responde con voz.

**Objetivo**: Portfolio impactante que demuestra fullstack + IA + procesamiento de voz.

---

## 🎯 Alcance inicial (Fase 1 — sin RAG)

### Lo que SÍ incluye
- Frontend web con micrófono
- STT: Faster Whisper (voz → texto)
- LLM: DeepSeek V4 Flash (genera preguntas/respuestas)
- TTS: Edge TTS (texto → voz)
- Backend: Python FastAPI
- Deploy en VPS con Nginx
- 10-15 preguntas predefinidas por tipo de entrevista
- Feedback básico al final

### Lo que NO incluye (fases posteriores)
- RAG con tu CV
- Clonación de voz (OpenVoice)
- Llamada telefónica real
- Evaluación automática de respuestas

---

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    NAVEGADOR (tu PC)                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐  │
│  │ Micrófono│───▶│  Audio   │───▶│  Reproductor     │  │
│  │ (input)  │    │  (blob)  │    │  (voz IA)        │  │
│  └──────────┘    └────┬─────┘    └────────▲─────────┘  │
│                       │                    │             │
│                       │  POST /api/chat    │  SSE audio  │
│                       │  (audio blob)      │  (stream)   │
└───────────────────────┼────────────────────┼─────────────┘
                        │                    │
                        ▼                    │
┌─────────────────────────────────────────────────────────┐
│                    VPS (Ubuntu ARM64)                    │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Nginx (puerto 80/443)               │   │
│  │         (sirve frontend + proxy a FastAPI)       │   │
│  └──────────────────────┬──────────────────────────┘   │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────┐   │
│  │              FastAPI (puerto 8000)               │   │
│  │                                                  │   │
│  │  POST /api/chat                                  │   │
│  │    1. Recibe audio (webm/ogg)                    │   │
│  │    2. Faster Whisper → texto                     │   │
│  │    3. DeepSeek → respuesta texto                 │   │
│  │    4. Edge TTS → audio mp3                       │   │
│  │    5. Devuelve audio + texto                     │   │
│  │                                                  │   │
│  │  GET /api/health                                 │   │
│  │  GET /api/questions (lista de preguntas)         │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │Faster Whisper│  │ DeepSeek API │  │  Edge TTS    │  │
│  │  (local CPU) │  │  (OpenRouter)│  │  (Microsoft) │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🛠️ Stack tecnológico

| Componente | Tecnología | Coste | Notas |
|---|---|---|---|
| **Frontend** | HTML + CSS + JS (vanilla) | 0€ | Sin frameworks pesados |
| **Backend** | Python FastAPI | 0€ | Ligero, rápido |
| **STT** | Faster Whisper (int8) | 0€ | CPU, ~1.4GB RAM |
| **LLM** | DeepSeek V4 Flash | 0€ | Via OpenRouter API |
| **TTS** | Edge TTS | 0€ | Voces naturales ES/EN |
| **Servidor** | Nginx | 0€ | Proxy + static files |
| **VPS** | Oracle Free Tier | 0€ | 4 cores, 24GB RAM |
| **Dominio** | (opcional) | ~10€/año | Para HTTPS |

**Total: 0€** (o ~10€/año si quieres dominio)

---

## 📁 Estructura del proyecto

```
~/projects/interview-voice/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── requirements.txt     # Dependencias
│   ├── services/
│   │   ├── stt.py           # Faster Whisper
│   │   ├── llm.py           # DeepSeek API
│   │   └── tts.py           # Edge TTS
│   ├── prompts/
│   │   └── interviewer.py   # System prompt del entrevistador
│   └── config.py            # Configuración
├── frontend/
│   ├── index.html           # Página principal
│   ├── style.css            # Estilos
│   └── app.js               # Lógica del chat de voz
├── nginx/
│   └── interview-voice.conf # Config Nginx
├── docker-compose.yml       # (opcional)
├── PLAN.md                  # Este archivo
└── README.md                # Documentación
```

---

## 🔄 Flujo de usuario

```
1. Usuario entra a la web
2. Selecciona tipo de entrevista:
   - Técnica (Java, Python, SQL...)
   - Comportamental (trabajo en equipo, liderazgo...)
   - Mixta
3. Click en "Iniciar entrevista"
4. La IA presenta la primera pregunta con voz
5. Usuario habla su respuesta por micrófono
6. Sistema transcribe → envía a LLM → genera siguiente pregunta
7. Repite 8-10 veces
8. Al final: feedback general con puntos fuertes y mejorar
```

---

## 📝 Preguntas de ejemplo

### Entrevista técnica (Java)
1. "¿Cuál es la diferencia entre una clase abstracta y una interfaz?"
2. "Explícame el polimorfismo con un ejemplo"
3. "¿Cómo funciona la recolección de basura en Java?"
4. "¿Qué es un Optional y cuándo lo usarías?"
5. "Diferencia entre ArrayList y LinkedList"

### Entrevista comportamental
1. "Cuéntame sobre ti y por qué quieres este puesto"
2. "Describe un conflicto en equipo y cómo lo resolviste"
3. "¿Cuál es tu mayor debilidad?"
4. "¿Por qué dejaste tu último trabajo?"
5. "¿Dónde te ves en 5 años?"

---

## 🧠 Prompt del entrevistador

```
Eres un entrevistador técnico profesional y amigable.
Estás entrevistando a un desarrollador junior.

Reglas:
- Haz UNA pregunta a la vez
- Espera la respuesta antes de continuar
- Sé constructivo, no crítico
- Si la respuesta es vaga, pide más detalles
- Al final, da feedback general

Tipo de entrevista: {tipo}
Nivel: junior
Idioma: español

Historial de la conversación:
{historial}

Siguiente acción:
```

---

## 📅 Plan de desarrollo

### Sprint 1 — Backend base (Semana 1)
- [ ] Crear estructura del proyecto
- [ ] FastAPI con endpoint `/api/health`
- [ ] Integrar Faster Whisper (STT)
- [ ] Integrar DeepSeek API (LLM)
- [ ] Integrar Edge TTS (TTS)
- [ ] Endpoint `/api/chat` funcional
- [ ] Probar flujo completo por terminal

### Sprint 2 — Frontend (Semana 2)
- [ ] HTML/CSS base (diseño limpio)
- [ ] Captura de micrófono (MediaRecorder API)
- [ ] Reproducción de audio (Audio API)
- [ ] Conexión con backend (fetch + SSE)
- [ ] Indicador de "escuchando" / "pensando"
- [ ] Historial de conversación en pantalla

### Sprint 3 — Pulido y deploy (Semana 3)
- [ ] Añadir tipos de entrevista
- [ ] Mejorar prompt del entrevistador
- [ ] Feedback final al terminar
- [ ] Configurar Nginx
- [ ] Deploy en VPS
- [ ] Tests básicos
- [ ] README con screenshots

### Sprint 4 — Extras (opcional)
- [ ] RAG con tu CV (personalizar preguntas)
- [ ] Modo inglés
- [ ] Grabación de entrevistas
- [ ] Exportar feedback a PDF

---

## ⚠️ Limitaciones conocidas

| Limitación | Impacto | Mitigación |
|---|---|---|
| Faster Whisper en CPU | ~2-3s de latencia | Aceptable para entrevista |
| Rate limit DeepSeek | ~60 req/min | No es problema (1 pregunta cada 30s) |
| Sin GPU | Whisper más lento | int8 reduce RAM a 1.4GB |
| Web Speech API | Solo Chrome/Edge | Documentar requisito |
| Latencia total | ~4-6 segundos | Aceptable para conversación |

---

## 🎯 Criterios de éxito

- [ ] La web carga en < 2 segundos
- [ ] La IA responde en < 5 segundos
- [ ] La voz suena natural (no robótica)
- [ ] Las preguntas son relevantes para junior
- [ ] Funciona en móvil y desktop
- [ ] Código limpio y documentado en GitHub
- [ ] README con instrucciones de uso

---

## 📊 Impacto esperado en recruiters

| Aspecto | Valor |
|---|---|
| **Stack demostrado** | Python, FastAPI, IA, Voz, Nginx, VPS |
| **Diferenciación** | 95% de juniors no tienen proyecto de IA |
| **Visualidad** | "Mira, esta app me entrevista" → wow |
| **Conversación** | Abre tema en entrevistas técnicas |
| **GitHub** | Código público, bien documentado |

---

*Plan creado por OWL — Junio 2026*
*Última actualización: 2026-06-09*
