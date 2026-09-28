---
type: story
title: ensayo-mal-cocido
created: 2026-08-03
updated: 2026-09-05
confidence: high
tags: [story, error, ensayo, aprendizaje]
related: [skills/testing.md, stories/cliente-pide-plazo-diez-dias.md, opinions/medir-antes-de-optimizar.md]
summary_1line: Un ensayo mal hecho, un cliente dolido y la leccion que todavia uso
---

# El ensayo mal cocido

## Situacion

En 2019 monte un control de curva de coccion para una serie nueva de
gres. El control estaba bien, pero mi error fue no probarlo con la
mezcla real: lo probé con los datos de la serie anterior, que tenian
menosshrink y otro beige.

## Que hice

Cuando entramos en produccion, un 22% de la serie salio por debajo de
medida. Hacia que el cliente me llamara para pedirle las piezas que
estaban mas cerca sin explicacion. Saque trescientas piezas de la
reserva y las mande a menor, sin cobrar la diferencia, y escribi
despues una carta de seis lineas explicando que habia ocurrido.

## Resultado

El cliente acepto y la relacion sigue. Por dentro fue el peor mes de
mi trabajo, porque la pieza habia salido bien segun mis propios
numeros, y mis numeros estaban hechos con los datos equivocados.

## Lo que aprendi

Desde entonces no dejo pasar un dato de entrada sin preguntar de donde
viene y cuando se midio por ultima vez. Un control que no se puede
cuestionar no es un control.

## Fuentes
- [[skills/testing]]
- [[opinions/medir-antes-de-optimizar]]

## See also
- [[stories/cliente-pide-plazo-diez-dias]]
