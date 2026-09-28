---
type: project
title: ceniza
created: 2025-09-14
updated: 2026-08-30
confidence: medium
tags: [project, reciclaje, planificacion, python]
related: [projects/horno-siete.md, skills/data.md, stories/reciclaje-ceniza-piloto.md]
summary_1line: Planificador de recogidas para el vidrio que sobra de la linea
---

# Ceniza

## Que es

Ceniza es un planificador que decide cuando recoge la cooperativa el
resto de vidrio y de cuanto queda de cada linea. Se apoya en el peso que
ya miden las balanzas de la nave de clasificacion, asi que no hace falta
que nadie apunte nada a mano.

## Como esta hecho

Un servicio en Python calcula la prediccion de resto por linea a partir
del peso real de la semana anterior y del calendario de encaros, y
publica una ruta de recogida. La interfaz es una tabla sencilla con
edicion manual: el planificador propone y la persona de la cooperativa
corrige. Guarda el plan en PostgreSQL con su version, de modo que
siempre se puede responder por que se recogio un camion un dia
concreto.

## Resultados medidos

- Las dos recogidas de urgencia al mes pasaron a cero.
- [TODO: ask Nuria] — Falta el coste por tonelada.

## Que haria distinto

- Empezaria por la version manual. Tardo tres meses mas, pero la primera
  version que salio de produccion ya no necesitaba correccion a mano.

## Fuentes
- [[stories/reciclaje-ceniza-piloto]]
- [[skills/data]]

## Ver tambien
- [[projects/horno-siete]]
