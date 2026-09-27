# Revisión adversarial — 2026-09-27

Dos jueces ciegos independientes (`jd-judge-a` y `jd-judge-b`) revisaron las Fases 1-3
sobre el rango `922225d..HEAD`. No modifyaron nada: solo leyeron y juzgaron.

**Resultado: 2 CRITICAL, ambos introducidos por la Fase 1 y la Fase 2, ambos ya corregidos.**

---

## 1. Los dos CRITICAL

### C-1 · El rate limit era evadible por cabecera falsificada

**Ambos jueces lo encontraron de forma independiente.** Es el hallazgo más grave
de toda la auditoría, y lo introduje yo al especificar el fix.

Mi `resolve_client_ip` confiaba en cualquier peer que "pareciera infraestructura"
(loopback, RFC1918, link-local, unspecified, o no parseable) y después recorría
`X-Forwarded-For`. Con nginx añadiendo `$remote_addr` a la **derecha** de lo que
envió el cliente:

```
cliente en 192.168.1.50 envía  X-Forwarded-For: 9.9.9.9
nginx produce                9.9.9.9, 192.168.1.50
mi walk inverso: salta 192.168.1.50 por ser "infraestructura"
devuelve                     9.9.9.9        ← bucket nuevo
```

Rotando la cabecera, el atacante obtenía un cubo nuevo por petición. Y como
`/api/.../message/stream` dispara STT + LLM + TTS sin otra cuota, eso es **gasto
ilimitado de LLM de pago**.

Peor: una cadena donde todos los hops parecen infraestructura devolvía
`hops[0]` — la cadena arbitraria que el atacante eligiera, como clave de dict
sin límite de cardinalidad.

**Mi error fue de especificación.** Dije "confía en la cabecera solo si el peer
es loopback" y implementé "cualquier peer no-público". El test que yo mismo
aprobé fijaba el comportamiento roto.

**El fix correcto no es_parche_de_esta lógica**: uvicorn's `--proxy-headers` ya
resuelve `scope['client']` bien — recorre en inverso saltando solo hosts de
`--forwarded-allow-ips`, y un host no parseable se compara contra lasiterals
de confianza así que **nunca** puede ser elegido. Mi segunda opinión a nivel de
aplicación re-introducía la ruta falsificable que la capa correcta ya había
eliminado.

Ahora: `resolve_client_ip` lee `request.client.host` y no parsea la cabecera.
La decisión de confianza vive en el unit file, donde pertenece.

Verificado en 12 variantes de peer y cabecera antes y después.

### C-2 · El filtro de placeholders borraba respuestas reales

`strip_placeholders` descartaba la **línea entera** cuando el marcador iba al
principio:

```
- [TODO: metricas] Reduje la latencia un 40%
        ↓
(nada — el 40% desaparece)
```

Además `_MD_NOISE_RE` borraba los `#` de un encabezado ( fusionando secciones y
moviendo los límites de chunk) y los `_` de identificadores reales
(`snake_case_name` → `snakecasename`).

**El principio:** la sanitización no decide qué es una respuesta. El texto tras
un `[TODO]` es indistinguible de una respuesta real sin un juicio semántico que
esta capa no debe hacer. Quitar el marcador es tarea del dueño.

Ahora solo se elimina el marcador.

---

## 2. Lo que los juecesmarks seHighlightaron

### Tres tests fijaban el bug en vez de detectarlo

- `test_rate_limit_ip`: el caso "cadena toda-privada" fijaba el fallback a
  `hops[0]`, la ruta falsificable.
- `test_rag`: `assert "Any metrics?" not in joined` fijaba la pérdida de contenido.
- `test_max_body`: quedó **vacuo** tras el split — escaneaba solo `main.py`, que
  ya no contiene la lógica, así que pasaba con cualquier contenido.

Un test que afirma comportamiento roto es peor que no tener test: convierte el
defecto en especificación. Este fue el hallazgo de mayor valor de la revisión,
porque no lo Produce ningún análisis de código — lo produce leer qué affirms.

### ERROR en cada desconexión normal

`GeneratorExit` es `BaseException`, así que el `except Exception` del generador
nunca lo ve. El `finally` registraba **ERROR** cada vez que el usuario cerraba
la pestaña. Entrenar al lector a ignorar esa línea es justo lo contrario de lo
que un log de error debe hacer. Ahora bifurca en INFO vs ERROR.

### `config.HOST` muerto y contradictorio

Defaults a `0.0.0.0` con un comentario que afirmaba que era intencional,
mientras el unit file ya binds a `127.0.0.1`. Dos copias de la misma decisión
con valores distintos. `RUNBOOK.md` también documentaba `--host 0.0.0.0`.
Eliminado: el bind es del unit file.

### Lo que los jueces verificaron y estava bien

- El invariante SSE "exactamente un evento terminal" **se sostiene** en todas las
  rutas vivas (trazadas una por una).
- `record_turn` es idempotente y memoria/disco concuerdan.
- El guard `CHUNK_FILTER_VERSION` de la caché de embeddings es correcto: una
  caché pre-fix se rechaza, no se sirve sin filtrar.
- El split de módulos no captura instancias en import time; `patch` sigue
  funcionando porque los servicios se leen de `main` en tiempo de llamada.
- WCAG recalculado a mano: `--outline-variant` pasa 3:1 en las 8 superficies.
- Todo `var(--x)` de `style.css` resuelve a una declaración en `:root`.

### Jueces que no pudieron verificar

Ninguno de los dos tuvo shell: **no pudieron correr los tests ni git**. Ambos
lo declararon de entrada, y por eso el recuento 380/38 quedó sin confirmar por
ellos. Lo verifiqué aparte: **390 pytest + 38 node en verde**.

---

## 3. Cómo verificar

```powershell
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD -ErrorAction SilentlyContinue
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider   # 390 passed
node --test "tests/frontend/*.test.mjs"                            # 38 pass
```

## 4. Pendiente de los jueces (no bloqueante)

- Los 4 tests de `periodic_cleanup` siguen parcheando `backend.main.asyncio` /
  `backend.main.config` cuando el código vive en `backend/maintenance.py`.
  Pasan por identidad de módulo, no porque el patch apunte al código. El día
  que ese módulo importe `sleep` por valor, dejarán de probar lo que dicen.
- `store_is_configured` alcanza un atributo privado (`persistence._enabled`).
  Funciona, pero un rename silencioso cambiaría la semántica del fallback.
- El docstring de `record_turn` promete "never duplicates" y el `INSERT` de
  `messages` sí duplica en un reintento. El test lo fija como intencional; el
  docstring es lo que miente.
- `app.js` sigue con un `requestAnimationFrame` de 60fps que escribe
  `--avatar-blend` cada frame. El fix de reduced-motion cubre el orbe WebGL,
  no este medio.
- El turno de despedida se persiste pero no se cuenta en el sidebar.
- Tests que escriben en el `data/interviewtts.db` y `reports/` reales
  (378 directorios hex acumulados).

Todo esto está en `auditoria/HALLAZGOS.md` con su `file:line`.
