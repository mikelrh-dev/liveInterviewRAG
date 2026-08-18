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
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Abrir `http://localhost:8000`

## 4. Tests

```bash
python -m pytest tests/ -v
```
