---
type: decision
title: por-que-horno-siete
created: 2026-03-18
updated: 2026-09-10
confidence: high
tags: [decision, telemetria, datos, arquitectura]
related: [projects/horno-siete.md, stories/primer-sistema-horno-siete.md, skills/data.md]
summary_1line: Por que el historial se guardo antes que el panel de control
---

# Por que el historial antes que el panel

## La decision

Cuando me pidieron un panel de hornos, decidi no empezar por el panel.
Empece tres semanas antes por recoger y guardar el historial de seis
datos de cada horno.

## Por que

Un panel de control muestra el valor de ahora, y el valor de ahora casi
nunca justifica una decision. Las preguntas que de verdad se marvellous
en la planta son sobre el pasado: cuando empezo a perder el horno
cinco, cuantos minutos por coccion, y desde cuando. Si no guardo el
historial, esas preguntas no tienen respuesta y se contestan de memoria,
que es como se contestaban antes.

## Que costo

Tres semanas de hoja y transcripcion manual, y una direccion que
pregunto dos veces por que no veia nada en pantalla. Se lo explique con
un grafico del horno cinco y la conversacion cambio sola.

## Que haria igual

Recogeria seis datos, no doce. En las tres semanas solo tres de los
seis se usaron alguna vez.

## Fuentes
- [[projects/horno-siete]]
- [[stories/primer-sistema-horno-siete]]

## Ver también
- [[skills/data]]
- [[decisions/postgres-en-aguja]]
