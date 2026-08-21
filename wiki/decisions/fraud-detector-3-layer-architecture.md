---
type: decision
title: fraud-detector-3-layer-architecture
created: 2026-08-19
updated: 2026-08-19
confidence: high
tags: [fraud-detector, architecture, ml, ensemble, rules-engine, llm]
related: [projects/fraud-detector.md, skills/backend.md, profile/mikel.md]
summary_1line: Por que una arquitectura de 3 capas (reglas + ML + LLM) para deteccion de fraude
---

# Arquitectura de 3 Capas para Fraud Detector

## Contexto
El proyecto necesitaba detectar fraude en transacciones financieras. Las alternativas eran:
- Solo reglas deterministicas (simple pero limitado)
- Solo ML (potente pero "caja negra")
- ML + LLM para decision (peligroso — alucinaciones en decisiones financieras)
- Arquitectura hibrida con separacion de responsabilidades

## Decision
Implementar 3 capas con responsabilidades claras:

**Capa 1 — Reglas (60% del score):** 9 reglas deterministicas que capturan patrones conocidos de fraude. Rapido, explicable, auditado. Si una regla salta, el analista sabe exactamente por que.

**Capa 2 — ML (25% del score):** XGBoost con 10 features engineered. Detecta anomalias que las reglas no cubren. Entrenado en PaySim (50k transacciones, 1% fraude).

**Capa 3 — Contexto (15% del score):** Score basado en contexto del usuario (historial, geografia, patrones). Ponderado dinamicamente por tier de monto.

**LLM (fuera del scoring):** Ollama genera informes tecnicos para analistas. NO participa en la decision de fraude. Solo explica POR QUE algo fue marcado.

## Alternativas consideradas

### Solo reglas
- Pros: Simple, auditado, rapido
- Cons: No detecta anomalias nuevas, requiere actualizacion manual constante
- Rechazado: Demasiado limitado para fraude real

### Solo ML
- Pros: Detecta patrones complejos
- Cons: Caja negra, sin explicabilidad para analistas, riesgo de alucinaciones
- Rechazado: En fraude financiero necesitas explicar cada decision

### ML + LLM para decision
- Pros: Todo automatizado
- Cons: LLMs alucinan, no son deterministas, no se pueden auditar
- Rechazado: Ningun regulatorio aceptaria que un LLM decida bloquear transacciones

### Ensemble hibrido (elegido)
- Pros: Cada capa hace lo que mejor sabe, fallback si una falla, explicable
- Cons: Mas complejo de implementar, pesos que tunear
- Aceptado: Balance optimo entre precision y explicabilidad

## Consecuencias
- El analista siempre puede explicar por que algo fue marcado (reglas son transparentes)
- El ML complementa las reglas sin reemplazarlas
- El LLM genera valor sin riesgo de decision incorrecta
- Los pesos del ensemble se pueden tunear sin reentrenar el modelo
- Si una capa falla, las otras compensan (robustez)

## Fuentes
- [[projects/fraud-detector]]
- [[profile/mikel]]

## Ver tambien
- [[decisions/por-que-interviewtts]]
