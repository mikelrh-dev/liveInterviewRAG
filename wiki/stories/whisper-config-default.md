---
type: story
title: whisper-config-default
created: 2026-06-16
updated: 2026-06-16
confidence: high
tags: [error, debugging, config, learning, deployment, stt]
related: [projects/interview-tts.md, decisions/por-que-interviewtts.md, skills/backend.md, skills/devops.md]
summary_1line: Se me pasó el default del config al migrar Whisper y el sistema siguió días corriendo con el modelo viejo sin que me diera cuenta
---

# Whisper config default escondido

## Situation

Todo el proyecto vive en un VPS gratis de Oracle, sin GPU. Eso significa que cada cosa que agrego tiene un costo: si uso un modelo de Whisper más grande, el pipeline se alarga y el recruiter espera. Si uso uno muy chico, el español se escucha mal. Es un equilibrio constante.

Decidí cambiar `tiny` por `small` justamente por eso — notaba que el `tiny` se equivocaba mucho con palabras técnicas en español y eso le llegaba al recruitador.

## Task

Migrar el modelo de Whisper de `tiny` a `small` para mejorar la precisión en español sin romper el pipeline ni aumentar la latencia más de lo aceptable.

## Action

Hice el cambio en el código, los tests andaban, todo bien.

El tema es que no me di cuenta de que el valor que realmente usaba el sistema en producción no era el que yo había cambiado. Era otro default, más escondido, en el archivo de configuración. Así que el sistema siguió días corriendo con `tiny`, los tests verdes, el health check ok, y yo pensando que ya estaba funcionando con `small`. No se rompió nada, pero no estaba haciendo lo que yo creía.

## Result

Fue una de esas cosas que cuando las ves decís "claro, obvio", pero que en el día a día se te pasan. Ahora el default está en un solo lugar y el test lo verifica ahí. Y cada vez que hago un cambio de estos, anoto por qué — calidad, velocidad, lo que sea — así no me vuelve a pasar.
