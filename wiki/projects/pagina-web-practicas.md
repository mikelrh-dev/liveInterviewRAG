---
type: project
title: pagina-web-practicas
created: 2026-06-13
updated: 2026-08-16
confidence: high
tags: [project, erp, velneo, documentation, dam, rag, faiss, telegram, ceesa]
related: [skills/backend.md, skills/frontend.md, profile/mikel.md, stories/autodidacta-fastapi-docker-async.md]
summary_1line: ERP de facturación en Velneo con IA, bot Telegram y RAG, en prácticas en Ceesa
---

# Prácticas en Ceesa — ERP de Facturación en Velneo

## What
Durante las prácticas (FCT) en **Ceesa**, me dediqué íntegramente a desarrollar un **ERP de facturación completo** con la plataforma **Velneo V37** (`facturacionmikel`, ~30 tablas). El proyecto cubre todo el ciclo comercial: compras, ventas, logística multi-almacén, inventarios (sistema Kardex unificado), gestión de caja (arqueos), control de IVA (Modelo 303) y auditoría asistida.

Esto me permitió aprender a desarrollar y entender qué soluciones tiene que ofrecer un ERP en el día a día: **fiscalidad, inventarios, logs, traspasos, tesorería/arqueos**.

## Why
- Proyecto final de las prácticas de 2º DAM (FP Superior Desarrollo de Aplicaciones Multiplataforma)
- Demostrar comprensión de arquitectura ERP, diseño de bases de datos y modelado de procesos de negocio
- Convertirse en mi laboratorio real de IA aplicada a negocio

## My role
- **Responsabilidad total:** desarrollo del ERP, integración de IA, documentación, UI
- **Equipo:** proyecto individual de prácticas en Ceesa, tutelado por el tutor de prácticas

## Stack
- **Plataforma:** Velneo V37 (vServer/vClient — formularios, procesos, triggers, informes)
- **Scripting ERP:** JavaScript (ES6+) para lógica compleja e integraciones
- **Dashboards:** HTML5 / CSS3 / Chart.js para KPIs en tiempo real
- **IA en el ERP:** detección de anomalías en inventarios, previsión de pedidos, integrada como utilidad para el negocio
- **Bot de Telegram:** dos cometidos — (1) alertas en tiempo real al administrador (arqueos con alta diferencia, compras que superan cierto importe, KPIs principales diarios); (2) bot asistente en Python con RAG (FAISS) que responde con información veraz y explícita sobre cualquier duda del ERP
- **Documentación vía markdowns:** wiki enlazada con wikilinks y enlaces bidireccionales, fuente del RAG
- **Página web:** documentación de funcionalidad y desarrollo del ERP, desarrollada con apoyo de IA siguiendo principios **SDD/TDD**

## Outcomes
- ERP de ciclo completo implementado: compras → stock → ventas → caja → impuestos → reporting
- Soluciones de IA integradas en el ERP (anomalías de inventario, previsión de pedidos)
- Bot de Telegram con alertas en tiempo real + asistente RAG con FAISS
- Proceso de documentación automatizado con agente + revisión manual posterior
- Scripts de Python propios para detectar wikilinks rotos y markdowns desactualizados, facilitando la revisión
- Página web complementaria con SDD/TDD
- [TODO: ask Mikel] — Métricas? (usuarios, transacciones, mejoras de rendimiento?)

## What I'd do differently
- [TODO: ask Mikel] — Reflexiones sobre Velneo? ¿Elegirías otro stack para un ERP hoy?
- [TODO: ask Mikel] — ¿Qué mejorarías del flujo de documentación?

## See also
- [[skills/backend]]
- [[skills/frontend]]
- [[profile/mikel]]
- [[stories/autodidacta-fastapi-docker-async]]