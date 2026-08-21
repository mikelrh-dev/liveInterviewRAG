---
type: project
title: fraud-detector
created: 2026-08-19
updated: 2026-08-19
confidence: high
tags: [project, ai, ml, fraud-detection, fastapi, xgboost, llm, portfolio, fullstack]
related: [skills/backend.md, skills/data.md, skills/frontend.md, skills/testing.md, profile/mikel.md]
summary_1line: Sistema hibrido de deteccion de fraude con reglas + ML + LLM local para informes
---

# Fraud Detector

## What
Sistema hibrido de deteccion de fraude en transacciones financieras que combina tres capas de analisis: un motor de reglas determinista (9 reglas), un modelo de machine learning (XGBoost con 10 features), y un LLM local (Ollama) que genera informes tecnicos explicativos para analistas. El sistema NO usa el LLM para decidir — solo para explicar. Incluye monitoring con drift detection (Evidently), audit trail inmutable con checksums SHA-256, y un dashboard React para analistas.

**URL:** [TODO: ask Mikel — deployed URL?]

## Why
Queria un proyecto de portfolio que demostrara ML en produccion, no solo notebooks. La mayoria de proyectos de ML en portafolios se quedan en "entrene un modelo y muestre la matriz de confusion". Fraud Detector va mas alla: feature engineering real, ensemble scoring, monitoring con drift detection, y un LLM que genera informes para humanos. Tambien queria practicar arquitectura de microservicios con workers async y una cola Redis.

## My role
- **Responsabilidad total:** concepcion, arquitectura, implementacion, tests, deployment
- **Equipo:** proyecto individual de portfolio
- **Lo que NO hice:** los modelos pre-entrenados de LLM (Ollama/Llama), el dataset (PaySim de Kaggle), y las librerias de ML (scikit-learn, XGBoost, SHAP)

## Stack
- **Backend:** Python 3.11 + FastAPI (async completo)
- **Base de datos:** PostgreSQL 16 (async via asyncpg)
- **Cache/Queue:** Redis 7
- **ML:** XGBoost + scikit-learn (Isolation Forest) + SHAP (explicabilidad)
- **Feature Engineering:** 10 features: amount vs avg, velocity, geo, time, MCC
- **LLM:** Ollama (Llama 3.2 3B) — solo genera informes, NO decide
- **Monitoring:** Evidently AI (drift detection)
- **Frontend:** React 19 + TypeScript + Vite + Recharts + Zustand
- **Graph Analysis:** NetworkX (deteccion de redes de fraude)
- **Auth:** JWT (python-jose) + bcrypt
- **Audit:** SHA-256 checksums en cada entrada de auditoria
- **Contenedores:** Docker + docker-compose (7 servicios)
- **Testing:** pytest + pytest-asyncio + pytest-cov (88% coverage)
- **CI/CD:** GitHub Actions (5 jobs: lint, test, build, docker)

## Arquitectura

```
Layer 1: Rule Engine (9 reglas deterministicas)
    → Score: 0-100 (cap)
    → high_amount, high_velocity, velocity_burst, unusual_merchant,
      card_mismatch, unusual_hours, off_hours_crypto, country_mismatch, near_fraud

Layer 2: ML Model (XGBoost + FeatureEngine)
    → 10 features, supervised (entrenado en PaySim)
    → Score: 0-100 (normalizado)

Layer 3: Ensemble Scoring
    → Weighted: rules 60% + ML 25% + context 15%
    → Clasificacion: legitimate | review | fraud

LLM Worker (async via Redis Queue)
    → Genera informe tecnico en espanol
    → Retry con exponential backoff (3/9/27s, max 3)

Workers adicionales:
    → SHAP Worker: atribuciones de feature importance
    → Embedding Worker: embeddings de merchant para deteccion de spoofing
```

## Metricas
| Metrica | Valor |
|---------|-------|
| API endpoints | 26 (21 bajo `/api/v1/`) |
| Modelos SQLAlchemy | 10 (9 dominio + 1 base) |
| Reglas deterministas | 9 |
| Features ML | 10 |
| Servicios Docker | 7 |
| Paginas frontend | 8 |
| Componentes frontend | 7 |
| Workers async | 3 |
| Servicios de negocio | 14 |
| Tests | ~246 (con parametrize) |
| Coverage | 88% |
| Dependencias Python | 33 |

## Outcomes
- Arquitectura de 3 capas funcionando end-to-end: transaccion → scoring → alerta → informe LLM
- 9 reglas deterministicas que cubren patrones conocidos de fraude
- ML ensemble que detecta anomalias no obvias
- LLM genera informes tecnicos en espanol para analistas (no decide)
- Monitoring con drift detection (Evidently) y retraining triggers
- Audit trail inmutable con SHA-256 para compliance
- Frontend React con dashboard, transacciones, alertas y visualizaciones
- CI/CD completo con GitHub Actions (lint + test + build + docker)
- Testing riguroso: 88% coverage, 30 archivos de test

## What I'd do differently
- [TODO: ask Mikel] — Reflexiones sobre la arquitectura? Cambiarias los pesos del ensemble?
- [TODO: ask Mikel] — El LLM local (Ollama) fue suficiente o考虑arias un LLM cloud para informes mas complejos?
- [TODO: ask Mikel] — Alguna feature que quieras agregar? (real-time streaming, alertas push, etc.)
- [TODO: ask Mikel] — El proyecto esta deployado? Metricas de produccion?

## See also
- [[skills/backend]]
- [[skills/data]]
- [[skills/frontend]]
- [[skills/testing]]
- [[decisions/fraud-detector-3-layer-architecture]]
- [[profile/mikel]]
