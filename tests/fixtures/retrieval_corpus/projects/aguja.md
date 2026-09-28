---
type: project
title: aguja
created: 2024-06-20
updated: 2026-07-19
confidence: high
tags: [project, inventario, postgres, fullstack]
related: [decisions/postgres-en-aguja.md, skills/backend.md, skills/frontend.md, stories/migracion-planillas-a-aguja.md]
summary_1line: Inventario de agujas y repuestos de los telares del taller
---

# Aguja

## Que es

Aguja sustituyo a las hojas de calculo con las que se llevaba el stock de
agujas, canillas y repuestos de los once telares del taller. Cada
movimiento de pieza entra con el parte del operario y se ve al momento en
la pantalla de la nave.

## Como esta hecho

API en FastAPI sobre PostgreSQL. El modelo distingue entre el existente,
el pedido a proveedor y la pieza en reparacion, que es la distincion que
las hojas de calculo no tenian y por la que siempre terminaban
descuadradas. El frontend es Vue con una tabla de filtros guardados, y
los avisos de stock bajo salen por correo al encargado de compras.

## Resultados medidos

- Las compras de emergencia por rotura de stock pasaron de once al mes a
  dos.
- El cierre de inventario paso de cuatro dias a una tarde.

## Que haria distinto

- Pondria los permisos desde el primer dia. Ahora mismo cualquiera que
  entre en la nave puede dar de baja una pieza, y eso ya ha pasado.

## Fuentes
- [[decisions/postgres-en-aguja]]
- [[stories/migracion-planillas-a-aguja]]

## Ver también
- [[skills/frontend]]
- [[skills/backend]]
- [[...]]
