# Estado al dejar — 2026-09-28

**HEAD:** `b0ecd87` · **595 pytest + 2 xfailed, 89 node, 0 fallos** · árbol limpio.

La tarea de corrección se **canceló antes de tocar nada**. Esto es lo que queda.

---

## ⚠️ Los 3 BLOCKER de los jueces, SIN ARREGLAR

Los dos jueces ciegos, trabajando **sin ver el trabajo del otro**, convergieron en los
mismos tres. Los verifiqué con evidencia. Todos están en la CI.

### 1. La CI muere antes de correr pytest

`.github/workflows/tests.yml:162` corre `generate_index.py --wiki wiki` sin condición.
Ese script devuelve **exit 1** si el directorio no existe (`generate_index.py:65-67`),
y `.gitignore:77` ignora `/wiki/`. **El job muere antes del paso de pytest.**

Este paso se volvió incorrecto cuando entró el corpus sintético: antes la wiki *era* el
corpus. **Arreglo:** quitarlo o generar el índice del fixture. **NO** `continue-on-error`.
**NO** `git add -f` de la wiki — eso es exactamente lo que el dueño descartó.

### 2. El guard de empaquetado no puede correr en su propia CI

El workflow fija **Python 3.10** (`:101`) pero instala las herramientas a mano (`:125`).
`tomli` no está instalado, y `pyproject.toml:41` lo declara para `<3.11`.
`tests/test_packaging.py:106-119` lanza `AssertionError` sin él → **3 tests fallan por un
motivo ajeno**, y la cabecera afirma que ese fichero "falla el build si `backend/` importa
una dependencia no declarada", lo cual es falso en la plataforma que usa la CI.

**Arreglo:** instalar el extra `dev` declarado, no listar herramientas a mano.

### 3. Un test peta con `KeyError` en cualquier checkout POSIX

`tests/test_rag_chunk_size_sweep.py:600` codifica una barra invertida en una clave:
```python
key = FAQ_REGRESSION_PAGE.replace("/", "\\")
```
`candidate.py:78` construye las claves como `str(rel_path)` — invertida en Windows, normal
en POSIX. En `ubuntu-latest` eso es `KeyError`.

Todos los demás tests normalizan vía `_norm_source` (`test_rag.py:1569-1571`). **Arreglo:**
igual que el resto, y hacer grep de hermanos.

### 4. La cabecera de la CI miente

`:18-56` sigue diciendo "IT IS CURRENTLY RED", cita `557 passed / 7 failed`, y **prescribe
`git add -f` de cuatro páginas personales** — lo que el dueño rechazó explícitamente.
Quien lea la CI ahora recibe instrucciones de filtrar datos personales a un repo público.

---

## Notas de los jueces (no bloquean)

- **Los floors de retrieval se recalibraron hacia ARRIBA** (recall@3 0.653 → 0.776) sobre un
  corpus cuyas preguntas se escribieron *después* de las páginas que nombran. El guard ahora
  se cumple a sí mismo: detecta regresiones de código, no de retrieval sobre la wiki real.
- El README del fixture **desdescribe la puerta de aislamiento**: dice "exits non-zero on any
  shared phrase" cuando solo se verifican las entidades nombradas.
- Varios docstrings siguen citando números del corpus real, y el `rag.py:371-396` afirma una
  garantía (`al menos 12 de 125`) que el propio test nuevo dice que es falsa en el fixture.
- `blocking.py:37` conserva un parámetro `is_first_substantive` ya sin lector.

---

## Después: el bloque visual, intacto

`auditoria/BACKLOG.md` §3. Lo más grande: **los 8-12s de espera muestran una única frase
incorrecta** porque `setStatus` nunca se llama en los eventos `transcription` ni `token`.

---

## Sigue siendo tuyo

1. **Desplegar en el VPS** — el certificado TLS nunca se emitió.
2. **Las 6 afirmaciones sin respaldo** en `response_cache.py` (líneas 189, 269, 308, 230, 283, 202).
