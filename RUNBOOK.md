# RUNBOOK — Cómo probar InterviewTTS

## 1. Requisitos

- Python 3.10+
- `.env` configurado (copiar de `.env.example` y llenar API keys)

## 2. Instalar dependencias

```bash
venv\Scripts\activate
pip install -e ".[dev]"
```

## 3. Iniciar servidor

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Abrir `http://localhost:8000`

> **Por qué `127.0.0.1` y no `0.0.0.0`.** El bind a `0.0.0.0` expone la API a la
> red saltándose nginx, y con él el TLS, las cabeceras de seguridad y el rate
> limit. En local la diferencia es inocua; en el VPS es la diferencia entre
> tener esas protecciones o no. Si necesitas acceder a la API desde otra
> máquina en desarrollo, usa un túnel SSH en vez de abrir el bind.
>
> Si expones el backend detrás de otro proxy durante el desarrollo, añade
> `--proxy-headers --forwarded-allow-ips 127.0.0.1` para que el rate limit
> siga viendo la IP real del visitante. Nunca uses `--forwarded-allow-ips '*'`:
> permite a cualquiera falsificar su dirección y evadir el límite.

## 4. Tests

```bash
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
```

Usa siempre el intérprete del venv. El Python global no tiene `pydantic`.

> **Trampa del entorno.** Si en una terminal anterior se exportó
> `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, el plugin `pytest-asyncio` deja de
> cargarse y los 28 tests async fallan de forma falsa. Límpialo antes de correr
> la suite:
>
> ```powershell
> Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD -ErrorAction SilentlyContinue
> ```
>
> Fallan y no se saltan, y el motivo importa. Sin `pytest-asyncio`, un
> `async def test_x` sigue siendo una función que pytest recolecta, llama y de
> la que recibe un coroutine. El fallo viene de la comprobación de que el valor
> devuelto sea `None`, y su mensaje nombra el síntoma —"async def functions are
> not natively supported"— sin nombrar la causa, así que se lee como una
> limitación de pytest y no como un plugin que no cargó. Los otros 974 tests
> pasan, que es lo que permite diagnosticar "falta un plugin" en vez de "el
> entorno está roto".
>
> El 28 está medido, no estimado: con la variable puesta la suite completa da
> exactamente 28 fallos, todos con ese mensaje.
> `tests/test_async_test_count.py` lo cuenta con `ast` y falla si este número o
> el de `.github/workflows/tests.yml` dejan de ser 28.

Tests de frontend (contrato SSE, estado de turno, tokens, motion):

```bash
node --test "tests/frontend/*.test.mjs"
```
