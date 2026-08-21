---
type: decision
title: por-que-interviewtts
created: 2026-06-14
updated: 2026-06-14
confidence: medium
tags: [interviewtts, portfolio, ai, project]
related: [projects/interview-tts.md, profile/mikel.md]
summary_1line: Por que elegi construir un asistente de voz con IA como portfolio
---

# ¿Por que construiste InterviewTTS?

## Contexto
Queria un proyecto de portafolio que se alejara de un CRUD tipico. Me llama la atencion la IA generativa, y construir InterviewTTS requeria:
- Un pipeline RAG completo (embeddings, chunking, retrieval)
- Integracion con LLM (DeepSeek/Owl) para generar respuestas contextuales
- STT (Whisper) + TTS (Edge TTS) para la interaccion por voz
- Despliegue en Oracle Free Tier con Docker

## Alternativas consideradas
- **CRUD tipico** (blog, ecommerce, clone de Twitter): mas comun, no destacaba
- **App movil**: requeria dispositivos fisicos para testear, mas complejidad
- **Proyecto de data puro**: no mostraba habilidades full-stack

## Decision
Construir un asistente de voz con IA que ademas sirviera como primer contacto para que las empresas me conocieran de una forma mas personal que un CV.

## Resultado
Un proyecto que no solo demuestra skills tecnicas (backend, frontend, IA, DevOps) sino que tambien funciona como herramienta de entrevista en si misma. El reclutador no lee sobre mi — me escucha.

## Fuentes
- [[projects/interview-tts]]
- [[profile/mikel]]

## Ver tambien
- [[decisions/...]]
