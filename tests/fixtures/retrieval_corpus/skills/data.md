---
type: skills
title: data
created: 2026-04-11
updated: 2026-08-22
confidence: high
tags: [skills, datos, sql, pandas]
related: [projects/ceniza.md, projects/horno-siete.md, skills/backend.md]
summary_1line: SQL y Python para datos de proceso, con Power BI para el reporting
---

# Datos

## Que manejo bien

- SQL intermedio y avanzado: ventanas, agregaciones condicionadas, CTE y
  consultas que se pueden leer seis meses despues por otra persona.
- Python para datos con pandas, y DuckDB cuando el fichero ya no cabe de
  sobra en memoria.
- Power BI para el informe de planta, y un criterio claro sobre que va en
  un tableau y que no.

## Como lo aplico

En Horno Siete los datos crudos del controlador llegan con huecos. El
principio que uso es que un dato que falta se marca como que falta y se
puede consultar, nunca se rellena con la media del vecindario sin
decirlo. Un panel que miente es peor que un panel vacio, porque el que
miente tambien se usa.

## Modelo minimo antes de construir

Cuando llego a un conjunto de datos nuevo hago tres cosas antes de
escribir la primera consulta de verdad: listingar las variables con su
unidad y quien las produce, medir cuantas hay y cada cuanto se mide de
verdad, y sentarme con quien las escribe para preguntarle que pasa
cuando falta un dia entero. La tercera es la que evita el noventa por
ciento de los errores, y la que mas tiempo lleva porque pide que la
persona se quede contigo media hora.

## Fuentes
- [[projects/ceniza]]
- [[projects/horno-siete]]

## See also
- [[skills/backend]]
- [[stories/primer-sistema-de-alertas]]
