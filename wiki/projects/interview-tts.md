---
type: project
title: interview-tts
created: 2026-06-13
updated: 2026-06-13
confidence: high
tags: [project, ai, voice, portfolio, fullstack]
related: [skills/backend.md, skills/frontend.md, skills/devops.md, profile/mikel.md]
summary_1line: Voice-based AI interview digital twin for recruiter conversations
---

# InterviewTTS

## What
A voice-based AI interview simulation that lets recruiters have voice conversations with a candidate's digital twin. Users speak into their browser microphone, the system transcribes (Faster Whisper), generates context-aware responses via LLM (DeepSeek/Owl), and replies with synthetic voice (Edge TTS). Built as a portfolio project to demonstrate full-stack + AI + voice capabilities.

**URL:** [TODO: ask Mikel — deployed URL?]

## Why
The core insight: recruiters spend ~6 seconds per CV. A voice conversation is infinitely more memorable than a piece of paper. Mikel wanted a portfolio project that:
- Demonstrates multiple skill domains (backend, frontend, AI, voice, deployment)
- Stands out from 95% of junior portfolios (most don't have AI projects)
- Creates a "wow" factor — "Mira, esta app me entrevista"
- Opens conversation topics in technical interviews

## My role
- **Full responsibility:** Solo developer — conception, architecture, implementation, deployment
- **What I didn't do:** The LLM models (DeepSeek/Owl), Whisper, and Edge TTS are third-party services/integrations
- **Context:** Personal portfolio project, not a team project. Designed to showcase individual capability.

## Stack
- **Backend:** Python FastAPI (REST API, SSE streaming)
- **Frontend:** Vanilla HTML + CSS + JavaScript (no frameworks)
- **STT:** Faster Whisper (int8 quantized, CPU-friendly)
- **LLM:** DeepSeek V4 Flash (via OpenRouter API) / Owl API
- **TTS:** Edge TTS (Microsoft, free voices in ES/EN)
- **RAG:** Sentence Transformers for embeddings, custom chunking
- **Server:** Nginx reverse proxy
- **Deployment:** Oracle Free Tier VPS (4 cores, 24GB RAM, ARM64)
- **Containerization:** Docker / docker-compose

## Outcomes
- Pipeline completo: voz → transcripción → LLM → síntesis de voz → respuesta audible
- RAG pipeline that retrieves context from candidate documents for accurate twin responses
- Clean, professional UI responsive on mobile and desktop
- [TODO: ask Mikel] — Any metrics? (e.g., response latency, number of conversations handled, user testing results?)

## What I'd do differently
- [TODO: ask Mikel] — What would you change about InterviewTTS if starting over? Architecture, tech choices, deployment approach?
- [TODO: ask Mikel] — Any features you plan to add next? (Voice cloning, phone integration, auto-evaluation?)

## See also
- [[skills/backend]]
- [[skills/frontend]]
- [[skills/devops]]
- [[profile/mikel]]
