---
type: skills
title: testing
created: 2026-04-11
updated: 2026-09-19
confidence: high
tags: [skills, testing, pytest, tdd]
related: [skills/backend.md, skills/frontend.md, stories/cliente-pide-plazo-diez-dias.md]
summary_1line: Pytest, tests de contrato y TDD donde compensa
---

# Testing

## Que manejo bien

- Pytest: parametros, fixtures, ``pytest-cov`` y los markers para separar
  lo lento de lo rapido.
- Tests de contrato entre servicios: lo que el cliente de la planta espera
  de cada endpoint, probado sin levantar el servidor real.
- TDD, pero solo donde compensa. En el nucleo de calculo del planificador
  de recogidas, en la normalizacion de telemetria y en el calculo de
  retrabajo, el test va primero.

## Donde no lo uso

En las pantallas puramente de edicion, el esfuerzo no se devuelve: un
formulario de tres campos tiene tres caminos y ninguno es interesante.
Prefiero ahi un buen trabajo de validacion y una prueba manual con la
persona que lo va a usar.

## Fuentes
- [[stories/cliente-pide-plazo-diez-dias]]
- [[skills/backend]]

## See also
- [[opinions/oficios-vs-software]]
- [[opinions/medir-antes-de-optimizar]]
