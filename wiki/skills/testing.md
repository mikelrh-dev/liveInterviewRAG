---
type: skills
title: testing
created: 2026-08-16
updated: 2026-08-16
confidence: high
tags: [testing, tdd, pytest, quality, skills]
related: [projects/interview-tts.md, opinions/importancia-tests.md, profile/mikel.md]
summary_1line: "Testing autodidacta: pytest unitario e integración tras cada cambio"
---

# Testing

## Enfoque
- **Autodidacta:** aprendí testing fuera del DAM, aplicándolo directamente en proyectos reales.
- **Habitual:** hago tests después de cada cambio significativo, tanto si escribo código a mano como si lo hago con agente.
- **Tipos:** uso tanto tests **unitarios** como de **integración**.
- **Frameworks:** pytest (Python), JUnit básico (Java — DAM).

## Por qué tests
- Me ahorran tiempo detectando problemas rápido que de otra manera pasarían por alto.
- Sin tests, refactorizar o cambiar código es una caja negra — con tests, cada cambio tiene una red de seguridad.
- En un entorno con IA generando código rápido, los tests son la única garantía de que lo que genera funciona realmente.

## Qué testeo
- Funciones aisladas (unitarios): lógica de negocio, helpers, parsers
- Endpoints API (integración): request/response, status codes, errores
- Mocking de servicios externos: APIs de LLM, TTS, STT — para no depender de servicios reales en tests
- En InterviewTTS: suite de 84 tests cubriendo config, RAG, LLM, STT, TTS, API y memoria de conversación

## See also
- [[projects/interview-tts]]
- [[opinions/importancia-tests]]
- [[profile/mikel]]