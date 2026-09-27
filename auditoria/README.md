# Auditoría en breve — InterviewTTS

Auditoría del 2026-09-27: **118 hallazgos** (1 CRÍTICO, 16 ALTO, 68 MEDIO, 33 BAJO),
remediados en 4 fases y después revisados por dos jueces ciegos.

**Veredicto:** el proyecto estaba bien construido y bien probado, pero en su estado
original **no se podía desplegar** (sin TLS) y tenía cuatro rutas rotas en el flujo
principal. Todo eso está corregido y verificado.

**Estado actual: 390 tests pytest + 38 node en verde** (partían de 258).

## Por dónde empezar

| Archivo | Qué contiene |
|---|---|
| [`RESUMEN.md`](RESUMEN.md) | El veredicto, los bugs que rompían el producto y lo que ya estaba bien |
| [`REVISION-ADVERSARIAL.md`](REVISION-ADVERSARIAL.md) | Lo que encontraron los dos jueces ciegos, y por qué dos tests fijaban el bug |
| [`PLAN.md`](PLAN.md) | Qué se corrigió, en qué orden, y lo que queda pendiente |
| [`HALLAZGOS.md`](HALLAZGOS.md) | Los 118 hallazgos, uno por línea, agrupados por severidad |
| [`VERIFICACION.md`](VERIFICACION.md) | Comandos para reproducir la suite y comprobar cada punto a mano |

Los cinco se entienden por sí solos. Para el detalle técnico completo de cada capa
está `reports/audit/` (7 informes) y el informe consolidado en
`AUDITORIA-2026-09-27.md`; se citan como referencia opcional.

## Lo que sigue pendiente

1. **Desplegar en el VPS**: emitir el certificado TLS y subir `nginx/interview.conf`
   y `interviewtts.service`. El redirect 80→443 va comentado a propósito en el
   fichero, y el orden importa.
2. **Tarea humana**: resolver los 16 `[TODO]` de la wiki y promover las 12 páginas
   `confidence: medium`. Requiere datos personales que solo tienes tú. El chunker ya
   no entrega los marcadores al LLM, así que el sistema no se auto-contamina mientras
   tanto.

## Una advertencia sobre el entorno

La segunda causa más frecuente de errores en este repositorio no es el código: es el
entorno. Una variable `PYTEST_DISABLE_PLUGIN_AUTOLOAD` contaminada hace que 8 tests
asíncronos parezcan rotos sin serlo, y ya costó una hora de trabajo en la auditoría.
Lee [`VERIFICACION.md`](VERIFICACION.md) antes de ejecutar pytest.
