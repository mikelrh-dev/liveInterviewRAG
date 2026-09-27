# Auditoría en breve — InterviewTTS

Resumen corto y legible de la auditoría del 2026-09-27: **118 hallazgos** (1 CRÍTICO, 16 ALTO, 68 MEDIO, 33 BAJO).

**Veredicto en una frase:** el proyecto está bien construido y bien probado, pero en su estado actual **no se puede desplegar** porque no tiene TLS, y en uso real tiene cuatro rutas rotas (respuestas mudas, despedida en silencio, turnos que se pierden y datos inventados).

## Por dónde empezar

| Archivo | Qué contiene | Líneas |
|---|---|---|
| [`RESUMEN.md`](RESUMEN.md) | El veredicto, los 5 bugs que rompen el producto y lo que ya está bien | ~110 |
| [`HALLAZGOS.md`](HALLAZGOS.md) | Los 118 hallazgos, uno por línea, agrupados por severidad | ~200 |
| [`PLAN.md`](PLAN.md) | Qué arreglar y en qué orden, en 4 fases | ~90 |
| [`VERIFICACION.md`](VERIFICACION.md) | Comandos para reproducir la suite y comprobar cada bug a mano | ~90 |

Estos cuatro archivos se entienden por sí solos. Para el detalle técnico completo de cada capa está `reports/audit/` (7 informes) y el informe consolidado en `AUDITORIA-2026-09-27.md`; se citan como referencia opcional, no hacen falta para entender nada de esta carpeta.

## Una advertencia antes de tocar nada

La suite de tests está **sana: 258 pasan, 0 fallan**. Hay red de seguridad, así que cada corrección se puede verificar. La segunda causa más frecuente de errores en este repositorio no es el código: es el entorno. Lee [`VERIFICACION.md`](VERIFICACION.md) antes de ejecutar pytest, porque una variable de entorno contaminada hace que 8 tests asíncronos parezcan rotos sin serlo.
