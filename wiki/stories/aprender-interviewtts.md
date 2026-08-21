---
type: story
title: aprender-interviewtts
created: 2026-06-14
updated: 2026-06-14
confidence: medium
tags: [interviewtts, learning, growth, fullstack, ai]
related: [projects/interview-tts.md, decisions/por-que-interviewtts.md, skills/backend.md, skills/devops.md]
summary_1line: EntrevistaTTS como vehiculo de aprendizaje forzado
---

# Aprender todo el stack de InterviewTTS

## Situation
Queria un proyecto de portafolio que se alejara de un CRUD y que demostrara habilidades reales en IA, backend y DevOps. El reto: todo el proyecto tenia que hacerse con alternativas gratuitas (Oracle Free Tier, Edge TTS, OpenRouter, faster-whisper).

## Task
Construir un asistente de voz con IA desde cero, integrando:
- Transcripcion por voz (Whisper)
- Generacion de respuestas contextuales (LLM + RAG)
- Sintesis de voz (Edge TTS)
- Un frontend interactivo con avatar 3D
- Despliegue completo en un VPS

## Action
- Me force a mejorar mi Python, especialmente asincronia con asyncio
- Aprendi a desplegar y gestionar un VPS en Oracle Free Tier desde cero
- Incorpore Docker para containerizar la aplicacion
- Trabaje en ajustar y elegir el LLM adecuado (OpenRouter, Google AI)
- Disene y alimente el pipeline RAG (embeddings, chunking, retrieval)
- Construi el flujo de recepcion, transcripcion y reproduccion de audio buscando el maximo realismo posible
- A medida que avanzaba, descubria nuevas optimizaciones para mejorar latencia y eficiencia

## Result
- Pipeline funcional: voz -> transcripcion -> RAG -> LLM -> TTS -> respuesta audible
- 84 tests pasando
- Despliegue funcional en Oracle Free Tier con Nginx + systemd
- Un portfolio que demuestra skills en backend, frontend, IA y DevOps
- Aprendizaje real de tecnologias que no habia tocado en DAM

## Fuentes
- [[projects/interview-tts]]
- [[decisions/por-que-interviewtts]]
- [[skills/backend]]
- [[skills/devops]]

## Ver tambien
- [[stories/...]]
