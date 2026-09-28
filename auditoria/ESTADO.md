# Estado — 2026-09-28 (tras reanudar)

**HEAD:** `191006d` · **596 pytest + 2 xfailed, 89 node, 0 fallos**

## ✅ Los 3 BLOCKER de los jueces: CERRADOS y verificados

Commits `1cc9ac6`, `15a5556`, `4f4d202`, `191006d`.

| # | Arreglo | Verificación |
|---|---|---|
| 1 | La CI ya no trata tu wiki como prerrequisito. Rama condicional real: si `wiki/` está, genera el índice; si no, emite `::notice::` visible en el resumen del run. No es fallo suprimido: no hay trabajo que pueda fallar | Clon limpio **con** wiki: 596 pasan. **Sin** wiki: 594 + 2 skipped |
| 2 | `pip install -e ".[dev]"` en vez de listar herramientas a mano. `tomli` ya estaba declarado; la CI ahora usa lo que el proyecto declara | El guard de empaquetado encuentra `tomli` en el extra |
| 3 | Los tests normalizan el separador como hace el resto de la suite | **Cero** separadores hardcodeados en `tests/` |

Y la cabecera de la CI ya no dice "está en rojo" ni prescribe `git add -f` de tus páginas.

## Nota sobre tu wiki

`wiki/` es un **repo git anidado**: 46 páginas rastreadas en el padre, 5 huérfanas en tu
disco. Un clon trae las 46. Por eso la rama condicional entra por `TRACKED` en Actions.
Las 5 huérfanas no afectan a la CI: la suite pasa con y sin ellas.

## Lo siguiente

El bloque visual (`BACKLOG.md` §3), **intacto**. Lo más grande: los 8-12s de espera
muestran una única frase incorrecta porque `setStatus` nunca se llama en `transcription`
ni en `token`.

## Sigue siendo tuyo

1. Desplegar en el VPS — el certificado TLS nunca se emitió.
2. Las 6 afirmaciones sin respaldo en `response_cache.py` (189, 269, 308, 230, 283, 202).
