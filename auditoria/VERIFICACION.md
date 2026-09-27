# Verificación

Cómo reproducir los datos de la auditoría y cómo comprobar cada bug a mano. Todo lo de aquí se ha ejecutado y sus resultados son los que aparecen en `RESUMEN.md` y `HALLAZGOS.md`.

## 1. Entorno: siempre el intérprete del venv

| Qué | Comando | Por qué |
|---|---|---|
| **Correcto** | `venv\Scripts\python.exe` | Es el único con `pydantic` y el resto de dependencias. |
| **Incorrecto** | `python` (el global) | Falla al importar; produce errores que no existen. |

Desde la raíz del repo, en PowerShell:

```powershell
.\venv\Scripts\python.exe --version
```

## 2. La suite de tests

```powershell
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD -ErrorAction SilentlyContinue
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
```

Esperado: **`258 passed`** en unos 2 minutos.

### La trampa del entorno — léela antes de la primera ejecución

En este entorno, el valor de `PYTEST_DISABLE_PLUGIN_AUTOLOAD` **se filtra entre llamadas al shell**. Si está activo, el plugin de tests asíncronos no se carga y **8 tests asíncronos aparecen falsamente como rotos** (error de `async def` sin plugin). Los tests no tienen ese problema: el fallo es de configuración local.

Antes de cada ejecución, limpia la variable:

```powershell
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD -ErrorAction SilentlyContinue
```

Si ves exactamente 250 passed / 8 failed con el error de funciones asíncronas, **no toques los tests**: es esto. Comprobación rápida:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD     # debe estar vacía
```

> El toolchain de tests **no está roto**: no hay ningún problema que arreglar en él. Una afirmación anterior en ese sentido se retiró por ser un falso positivo, y la única causa de "8 tests rotos" es esa variable.

## 3. Validador de la wiki

```powershell
venv\Scripts\python.exe scripts/wiki/validate.py --wiki wiki/
```

Esperado: **`OK: 37 files valid, 65 warnings`**. Los 65 warnings son todos del mismo tipo (`link asymmetry`, `scripts/wiki/validate.py:114-129`); ninguno se refiere a `[TODO]`, wikilinks ni codificación.

Lo que este validador **no** comprueba, y por eso da 0 errores con 16 `[TODO]` y 4 wikilinks rotos en el corpus: marcadores `[TODO` en el cuerpo, wikilinks del cuerpo, sincronía de `index.md`, desfase de `updated` y caracteres no esperados.

## 4. CodeGraph

Para reindexar el grafo de símbolos tras cambiar código de forma relevante:

```powershell
codegraph init
```

Índice actual: 40 archivos, 897 nodos, 2022 edges.

## 5. Los 6 bugs, uno a uno

Cada uno se comprueba con un comando o una observación. Todos están citados en `RESUMEN.md` con su `file:line`.

### 5.1 No hay TLS (CRÍTICO)

```powershell
Select-String -Path "nginx\interview.conf" -Pattern "listen|ssl|return 301"
```

Esperado: **una sola** coincidencia, `listen 80;` en la línea 2. Cero `ssl`, cero `return 301 https://`, cero `Strict-Transport-Security`.

### 5.2 La ruta cacheada nunca se habla

```powershell
Select-String -Path "backend\main.py" -Pattern '"audio_url"'
Select-String -Path "frontend\app.js" -Pattern 'audio_url'
```

Esperado: en `main.py`, el emisor en la línea 773 (`"audio_url", {"url": ...}`) más las apariciones del docstring y del resto de rutas. **En `frontend\app.js`: 0 coincidencias.** El frontend no menciona `audio_url` en ningún punto del fichero. Su dispatcher maneja `token` (`app.js:972`) y `audio_chunk` (`:981`), y no tiene esa rama.

Comprobar en navegador: pregunta algo del FAQ ("preséntate", "cuéntame sobre ti"). Se lee el texto y **no se oye nada**.

### 5.3 La despedida nunca sintetiza TTS

```powershell
Select-String -Path "backend\main.py" -Pattern '"audio_url": ""'
```

Esperado: `main.py:789`, dentro de la rama de farewell. Ninguna llamada a `tts_service` en las líneas 777-805.

Comprobar en navegador: di "gracias, eso es todo". El texto se escribe, el avatar se apaga y **no suena nada**.

### 5.4 Se pierden turnos bajo carrera

```powershell
Select-String -Path "backend\services\persistence.py" -Pattern "UNIQUE|INSERT INTO turns|ON CONFLICT"
```

Esperado: `UNIQUE(conversation_id, n)` en la línea 37 e `INSERT INTO turns` en la 232. `ON CONFLICT` aparece **4 veces** en el fichero, pero **ninguna sobre `turns`**: están en las tablas `conversations` (líneas 200 y 225) y `reports` (línea 355). Es decir, el resto del fichero sí sabe upsertear; el `INSERT` de turnos es el único que no. El `except Exception` de `persistence.py:264-265` reduce el `IntegrityError` a un `logger.warning`.

Reproducción del caso observado: dos peticiones concurrentes sobre el mismo `conversation_id` → 1 turno en SQLite, 2 en el diccionario en memoria.

Comprobar a mano: abre dos pestañas en la misma conversación, envía un turno en cada una, reinicia el backend y mira el historial. Debe conservar los mismos turnos que se oyeron.

### 5.5 El RAG entrega `[TODO]` al LLM

Reproduce el pipeline real (mismo camino que `main.py:150`):

```powershell
$env:RAG_CACHE_DIR = "$env:TEMP\opencode\ragprobe"
venv\Scripts\python.exe -c @"
from backend.config import config
from backend.services.candidate import CandidateProfile
from backend.services.rag import RAGPipeline
prof = CandidateProfile(config.CANDIDATE_DIR, wiki_dir=config.WIKI_DIR)
prof.load()
p = RAGPipeline(cache_dir=config.RAG_CACHE_DIR)
p.ingest_documents(prof.documents)
print('documents =', len(prof.documents))
print('chunks    =', len(p.chunks))
print('con [TODO =', sum(1 for c in p.chunks if '[TODO' in c.content))
"@
```

Esperado: **`documents = 38`, `chunks = 214`, `con [TODO = 11`**. Verificado. Los 11 caen en secciones tituladas "Outcomes" y "What I'd do differently". Redirigir `RAG_CACHE_DIR` a temporal evita tocar el caché real; el primer arranque carga el modelo de embeddings y tarda unos segundos.

Confirma la causa raíz en el código:

```powershell
Select-String -Path "backend\services\rag.py" -Pattern "confidence"
```

Esperado: **0 coincidencias**. `confidence` no aparece en `rag.py` en absoluto: ni el chunker lo consulta ni el prompt lo pasa. El campo viaja en el frontmatter y `parse_frontmatter` lo descarta, así que la distinción `high`/`medium` es invisible para el LLM.

### 5.6 Tres de los ocho filtros `doc_type` no matchean

```powershell
Select-String -Path "backend\services\rag.py" -Pattern '"stories"|"opinions"|"decisions"|"project"'
```

Esperado: el patrón devuelve 10 líneas. Las que importan son las **37-39**: `QUERY_TYPE_KEYWORDS` usa claves **en plural** (`stories`, `opinions`, `decisions`) mientras el frontmatter `type:` de la wiki usa **en singular** (`story`, `opinion`, `decision`). Las líneas 26-28 y 36 son las variantes de `project`, y las 438-444 la normalización, que **solo arregla `project`** (`rag.py:437-447`). Ese es exactamente el bug: la normalización existe y los autores solo la generalizaron a un tipo de ocho.

Medido: **4 de 9 formulaciones naturales** devuelven 0 chunks con filtro y 3 sin él.

## 6. Comprobar que la suite sigue verde tras cada cambio

```powershell
Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD -ErrorAction SilentlyContinue
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
venv\Scripts\python.exe scripts/wiki/validate.py --wiki wiki/
```

257 o menos de 258 significa que has roto algo. 250 con 8 errores de async significa que olvidaste limpiar la variable del punto 2.

## 7. Lo que ninguna verificación automática cubre

El **render**. Los hallazgos de Capa 6 (V6-01 a V6-07) se apoyan en aritmética cerrada del CSS, verificada numéricamente pero nunca vista en pantalla. Ábrelo a 1366×768, 900×600 y 1024×768, en claro y en oscuro, y comprueba:

- El transcript no colapsa a 0 px y la línea de estado se ve entera.
- El anillo orbital está centrado sobre el avatar.
- La tarjeta del disclaimer tiene fondo y el CTA se lee con claridad.
- El avatar no se ve recortado en la franja de 769 a 1051 px.
