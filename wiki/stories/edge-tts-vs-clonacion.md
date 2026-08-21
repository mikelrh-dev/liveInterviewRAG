---
type: story
title: edge-tts-vs-clonacion
created: 2026-06-16
updated: 2026-06-16
confidence: high
tags: [tts, tradeoffs, optimization, streaming, deployment, voice]
related: [projects/interview-tts.md, decisions/por-que-interviewtts.md, skills/backend.md, skills/devops.md]
summary_1line: Elegir Edge TTS gratis en vez de clonación de voz o ElevenLabs para no sacrificar fluidez en el VPS
---

# Edge TTS vs clonación de voz

## Situation

El proyecto necesitaba voz sintetizada. Las opciones eran muchas: ElevenLabs calidad bestial con clonación, modelos open-source como Piper, o Edge TTS gratis. El problema es que todo corre en un VPS gratis sin GPU, y la latencia del pipeline ya era justa.

## Task

Elegir una solución de TTS que sonara bien, corriera en el VPS sin matar la fluidez de la conversación, y no costara plata.

## Action

Se probó a clonar la voz con modelos locales como Piper. La clonación era un éxito — sonaba muy bien. Pero al ponerlo en el VPS, el costo de procesamiento era tal que la conversación perdía toda fluidez. Y eso que ya habíamos metido varias optimizaciones:

- **Streaming SSE**: el audio empieza a reproducirse antes de que termine de sintetizarse, en vez de esperar el archivo completo
- **Caché de frases frecuentes**: respuestas comunes se cachean y ni siquiera llaman al TTS
- **Síntesis en paralelo**: respuestas largas se parten en párrafos y se sintetizan concurrentemente

Incluso con todo eso, añadir Piper encima rompía el balance. La opción óptima habría sido ElevenLabs — calidad superior, sin carga en el VPS, clonación real de voz — pero es paga. Al final se eligió Edge TTS: gratuita, corre local sin depender de APIs externas, latencia mínima, y con las optimizaciones de streaming y caché la fluidez es aceptable.

## Result

Edge TTS con streaming y caché corre estable en el VPS. Las voces no son mías pero son profesionales y el recruiter no nota el cambio. La fluidez de la conversación se mantiene por debajo de los segundos que la hacen natural. No es la solución perfecta, pero es la solución que funciona dentro de las constraints — y saber elegir eso también es un skill.
