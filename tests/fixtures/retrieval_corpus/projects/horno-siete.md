---
type: project
title: horno-siete
created: 2025-03-02
updated: 2026-09-10
confidence: high
tags: [project, telemetria, hornos, python]
related: [profile/nuria-belvis.md, skills/backend.md, skills/data.md, decisions/por-que-horno-siete.md]
summary_1line: Panel de telemetria para los ocho hornos de la planta
---

# Horno Siete

## Que es

Horno Siete es un panel web que dibuja la temperatura, la presion y el
consumo de los ocho hornos de la planta en una sola pantalla, con
historial de hasta dos anos. Antes de el, un operario tenia que bajar a
sala de hornos cada veinte minutos a apuntar seis numeros en una hoja.

## Como esta hecho

Backend en Python con FastAPI y PostgreSQL. Cada horno tiene un
controlador industrial que publica por MQTT y un servicio que lo lee,
normaliza y guarda en una tabla particionada por dia. El panel es Vue
con una libreria de graficos y un refresco por Server-Sent Events, sin
polling agresivo porque la red de la planta va justa.

## Resultados medidos

- El tiempo de parada no planificado por temperatura se redujo un 31% en
  nueve meses.
- El consumo de gas por pieza bajo un 9% en el primer trimestre.
- [TODO: ask Nuria] — Falta la cifra de horas de operario ahorradas.

## Que haria distinto

- Reservaria desde el dia uno la parte de los datos historicos, porque el
  primer prototipo descarto tres semanas de medidas ya recogidas.
- Escribiria los tests del normalizador antes que el resto del servicio.

## Fuentes
- [[decisions/por-que-horno-siete]]
- [[skills/backend]]
- [[faq/...]]

## See also
- [[skills/data]]
- [[projects/ceniza]]
