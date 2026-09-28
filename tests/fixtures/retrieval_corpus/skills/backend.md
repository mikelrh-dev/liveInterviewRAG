---
type: skills
title: backend
created: 2026-04-11
updated: 2026-09-19
confidence: high
tags: [skills, backend, python, fastapi]
related: [projects/aguja.md, projects/horno-siete.md, skills/testing.md]
summary_1line: Python y FastAPI sobre PostgreSQL, con algo de Redis y Celery
---

# Backend

## Que manejo bien

- Python 3.10 o superior, tipado con anotaciones y fichero de proyecto en
  vez de un requirements a mano.
- FastAPI para servicios HTTP, con el esquema generado y validacion con
  Pydantic en el borde, no en el controlador.
- PostgreSQL: consultas normales, indices, vistas y una buena parte del
  ajuste de planes de ejecucion.
- Redis para colas cortas y cache de lectura, y Celery para trabajos de
  minutos que no pueden bloquear una peticion.

## Donde me cuesta mas

- Ajustar una consulta que ya esta en produccion sin degradar nada mas.
  Uso ``EXPLAIN ANALYZE`` y leo los planes, y tengo la costumbre de
replicar la consulta contra un subconjunto antes de tocar el indice,
pero no soy la persona a la que llamaria para un problema de carga
serio en un sistema grande. Lo he hecho bien cuando el sistema era mio y
podia medir el antes y el despues; en casa ajena prefiero medir antes
de tocar nada.

- El codigo heredado sin pruebas. Ahi lo que hago es escribir primero la
  prueba del comportamiento actual, no la del comportamiento deseado, y
  refactorizar con esa red debajo. Si la prueba legacy falla una vez,
  la duda es si la prueba o el programa tienen razon, y casi siempre es
  el programa, pero no siempre.

## Como trabajo

Escribo el contrato antes que el servicio, y la prueba del contrato antes
que la implementacion. Los endpoints que dependen de la red de la planta
se prueban con una maquina de estados para el reconexionado, no solo con
un mock de exito, porque el fallo interesante es siempre el que ocurre
cuando la conexion se cae a mitad de una respuesta.

En la practica eso significa que la prueba mas cara que escribo no es la
del camino feliz, sino la que corta la conexion tres veces seguidas y
comprueba que el cliente se recupera sin pedirle nada. Ese camino es el
que en una fabrica se da dos veces al dia, y el que nunca aparece en los
ejemplos de la documentacion. Cuando esa prueba falla, la solucion casi
nunca esta en el servidor: esta en que el cliente sepa que esta mirando
algo antiguo, y que por lo tanto no decida.

## Fuentes
- [[projects/aguja]]
- [[skills/testing]]

## See also
- [[skills/data]]
- [[skills/devops]]
