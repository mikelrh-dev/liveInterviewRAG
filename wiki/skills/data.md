---
type: skills
title: data
created: 2026-06-13
updated: 2026-06-13
confidence: high
tags: [skill-domain, database, sql, mysql, postgresql, mongodb]
related: [projects/interview-tts.md, projects/pagina-web-practicas.md, projects/fraud-detector.md]
summary_1line: Databases and data management — SQL, MySQL, PostgreSQL, MongoDB
---

# Databases & Data

| Skill | Level | Last used | Where demonstrated |
|-------|-------|-----------|--------------------|
| MySQL | confident | 2026-04 | DAM coursework, database design projects |
| PostgreSQL | working | 2026-04 | DAM coursework |
| MongoDB | working | 2026-04 | DAM coursework — NoSQL document modeling |
| SQL (general) | confident | 2026-04 | Across all database coursework |
| PL-SQL | working | 2026-04 | Oracle PL-SQL coursework |
| SQLAlchemy ORM | working | 2026-08 | [[projects/fraud-detector]] — 10 modelos, async via asyncpg |
| Alembic migrations | working | 2026-08 | [[projects/fraud-detector]] — schema versioning |

## Notes
- Strong SQL foundation from DAM coursework — joins, subqueries, aggregation, normalization
- Experience with both relational (MySQL, PostgreSQL) and NoSQL (MongoDB) databases
- InterviewTTS currently uses file-based document storage (RAG), not a traditional database backend
- Fraud Detector uses PostgreSQL in production with SQLAlchemy async ORM and Alembic migrations
- [TODO: ask Mikel] — Do you have a preferred database for personal projects? Any experience with database migration tools or ORMs beyond Hibernate?
