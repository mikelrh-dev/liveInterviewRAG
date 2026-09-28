---
type: story
title: migracion-planillas-a-aguja
created: 2026-06-18
updated: 2026-09-05
confidence: high
tags: [story, migracion, datos, inventario]
related: [projects/aguja.md, skills/data.md, decisions/postgres-en-aguja.md]
summary_1line: Tres meses de planillas a las que nadie queria renunciar
---

# De las planillas a Aguja

## Situacion

El stock de los once telares llevaba seis años en tres hojas de calculo
que se copiaban entre si. Cuando vi que las dos primeras no
coincidian, el responsable me dijo que eso era normal. No lo fue: eran
las dos primeras hojas y no cuadraban ni entre ellas.

## Que hice

No importe las hojas y empece por el proceso: preguntar en el turno de
mañana como se anota una pieza que vuelve de reparacion, y en el de
tarde como se anota una que se rompe. Salieron tres casos que las hojas
no tenian, y por eso nunca cuadraban. Aguja se construyo con los tres
casos en la base desde el primer dia.

## Resultado

La primera carga tardo tres meses, con visitas semanales a cada turno.
El cierre de inventario, que eran cuatro dias, paso a una tarde.

## Lo que aprendi

Migrar datos sin entender el proceso es copiar el mismo error a un
sitio mas bonito y mas caro de mantener.

## Fuentes
- [[projects/aguja]]
- [[decisions/postgres-en-aguja]]

## See also
- [[skills/data]]
- [[opinions/oficios-vs-software]]
