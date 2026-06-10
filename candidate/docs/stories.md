# Mikel — Historias

## El Origen de InterviewTTS

### Situación
Los recruiters pasan ~6 segundos en un CV antes de decidir si llamar. Las llamadas de screening inicial suelen hacer las mismas preguntas sobre experiencia, proyectos y habilidades. Pensé: ¿y si construyo un gemelo digital por voz que maneje la primera ronda de preguntas de forma natural?

### Tarea
Crear un MVP funcional de un portfolio de voz con IA — un sistema donde recruiters puedan tener conversaciones por voz con una versión digital de mí. Las respuestas debían estar basadas en mi CV y proyectos reales, no en respuestas genéricas de IA.

### Acción
Diseñé y construí InterviewTTS desde cero:
1. **Pipeline STT**: Usé Faster Whisper para transcripción precisa de la voz del recruiter
2. **Sistema RAG**: Construí un pipeline de recuperación en memoria que busca en mi CV real, documentos de proyectos y habilidades
3. **Integración LLM**: Conecté a Owl API para generar respuestas naturales como yo, usando el contexto recuperado
4. **Salida TTS**: Integré Edge TTS para entregar respuestas habladas con voz profesional
5. **Frontend Limpio**: Construí una UI responsive con grabación de micrófono y reproducción de audio

### Resultado
Creé un MVP funcional que demuestra habilidades prácticas de ingeniería de IA. El sistema procesa entrada de voz a través de un pipeline completo STT → RAG → LLM → TTS. Muestra no solo capacidad técnica, sino la habilidad de identificar un problema real y construir una solución completa.

## Aprendiendo Construyendo

### Situación
Al empezar con FastAPI y Python asíncrono, me di cuenta de que la documentación sola no era suficiente — necesitaba construir algo real para entender los conceptos.

### Tarea
Profundizar mi comprensión del desarrollo backend moderno con Python construyendo una aplicación de calidad profesional.

### Acción
Construí InterviewTTS como vehículo de aprendizaje:
- Implementé patrones asíncronos correctos con httpx
- Diseñé límites de servicio limpios (STT, LLM, TTS, RAG como servicios independientes)
- Usé configuración basada en entorno para diferentes contextos de despliegue
- Escribí tests exhaustivos con APIs externas mockeadas

### Resultado
Gané experiencia práctica con FastAPI, programación asíncrona y arquitectura limpia — habilidades directamente aplicables al trabajo profesional de desarrollo.

## De CRUD a Proyecto Diferenciador

### Situación
Mis proyectos de GitHub eran CRUDs genéricos de clase — bibliotecas, enrolamientos, inventarios. Todos funcionales, pero iguales a los de cualquier otro estudiante. Necesitaba un proyecto que realmente marcara la diferencia.

### Tarea
Construir algo que combinara todo lo aprendido: backend, frontend, IA, voz, y despliegue — y que además resolviera un problema real.

### Acción
En lugar de hacer "otro CRUD más", diseñé InterviewTTS combinando:
- Backend con FastAPI y procesamiento asíncrono
- IA con Whisper (STT), Owl API (LLM), y Edge TTS (TTS)
- Pipeline RAG con embeddings semánticos
- Frontend profesional con interacción por voz
- Preparado para deploy en VPS con Nginx

### Resultado
InterviewTTS se convirtió en mi proyecto estrella. No solo demuestra habilidades técnicas, sino que ES un ejemplo de lo que sé hacer. Los recruiters no leen mi CV — hablan con él.
