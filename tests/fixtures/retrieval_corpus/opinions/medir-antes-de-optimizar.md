---
type: opinion
title: medir-antes-de-optimizar
created: 2026-05-20
updated: 2026-09-01
confidence: high
tags: [opinion, medicion, rendimiento]
related: [stories/primer-sistema-de-alertas.md, opinions/documentacion-para-quien-viene.md]
summary_1line: Un numero que no se ha medido no es un numero, es una opinion
---

# Medir antes de optimizar

## Mi opinion

No se optimiza lo que no se ha medido, y no se mide lo que no se ha
acordado. La mitad de las discusiones tecnicas que he visto en un taller
son dos personas defendiendo intuiciones.

## El caso que siempre cuento

Puse una alerta por correo cada vez que la temperatura se salia de
rango. Funcionaba, y era inutil: saltaba cuatro veces al dia en cada
cambio de carga y ninguna necesitaba a nadie. Cuando la gente empieza a
ignorar el canal, el canal entero queda muerto, incluido el aviso que
si habia que mirar.

## Que hago entonces

Escribo antes el criterio de exito, con la cifra y el plazo. Despues
mido, con la fuente de datos marcada. Y si despues no sale, lo
reconozco en voz alta, porque un mal resultado publicado vale mas que
un buen resultado escondido.

## Fuentes
- [[stories/primer-sistema-de-alertas]]
- [[stories/ensayo-mal-cocido]]

## Ver también
- [[opinions/oficios-vs-software]]
