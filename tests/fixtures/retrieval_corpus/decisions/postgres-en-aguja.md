---
type: decision
title: postgres-en-aguja
created: 2024-08-30
updated: 2026-07-19
confidence: high
tags: [decision, base-de-datos, postgres, inventario]
related: [projects/aguja.md, skills/data.md, stories/migracion-planillas-a-aguja.md]
summary_1line: Por que inventario en PostgreSQL y no en otra cosa
---

# Por que PostgreSQL para el inventario

## La decision

Aguja guarda el stock de los telares en PostgreSQL, sin mas. No hay
Redis para el estado, no hay un segundo almacen, y no hay un servicio de
mensajeria.

## Por que

El volumen es de unos pocos miles de movimientos al dia y las consultas
que importan son sumas con filtros. PostgreSQL resuelve eso sin
problemas y encima me da transacciones de verdad, que es lo que
necesito cuando tres personas dan de alta la misma pieza a la vez. Un
almacen de clave-valor habria sido mas rapido para un problema que aqui
no existe.

## Que perdi

La portabilidad. Un dia quiero cambiar de servidor de base de datos y
tengo que rehacer los indices. Lo acepto.

## Que haria distinto

Pondria desde el principio el historical de movimientos en tablas de
solo insercion, aunque no lo consulte nadie. Rehacerlo despues fue
caro.

## Fuentes
- [[projects/aguja]]
- [[decisions/por-que-horno-siete]]

## See also
- [[skills/backend]]
- [[skills/data]]
