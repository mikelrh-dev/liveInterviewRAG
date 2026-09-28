---
type: story
title: primer-sistema-de-alertas
created: 2026-06-18
updated: 2026-09-05
confidence: high
tags: [story, alertas, monitorizacion, operacion]
related: [projects/horno-siete.md, skills/devops.md, skills/data.md]
summary_1line: Mi primer sistema de alertas y por que Ignore las mejores señales
---

# El primer sistema de alertas

## Situacion

Mi primer servicio en la planta avisaba por correo cuando la
temperatura de coccion se salia de rango. Lo deje configurado con mucho
cuidado y la primera semana recibi siete correos.

## Que hice

A la segunda semana entendi que la alerta era correcta y aun asi no
servia para nada. La temperatura se sale de rango en cada cambio de
carga, cuatro veces al dia, y en ninguno de esos casos hay que ir a
nada. quite el correo y dejo solo dos casos: caida sostenida de mas de
veinte minutos, y desviacion que no se recupera en tres ciclos.

## Resultado

Las alertas que quedan saltan cuatro o cinco veces al mes, y las cuatro
veces habia que bajar a la nave. Un aviso que aparece diez veces al dia
es ruido de infraestructura, y el ruido hace que la gente deje de
mirar el canal entero.

## Lo que aprendi

Que una alerta hay que medirse con la pregunta de cuantas veces habria
que actuar, no con la de cuantos datos se sale del rango.

## Fuentes
- [[projects/horno-siete]]
- [[skills/devops]]

## Ver también
- [[opinions/medir-antes-de-optimizar]]
