---
type: skills
title: devops
created: 2026-06-13
updated: 2026-09-29
confidence: medium
tags: [skill-domain, devops, git, github, scrum, agile, virtualization]
related: [projects/interview-tts.md]
summary_1line: DevOps and tools — Git, SCRUM, virtualization, CI fundamentals
---

# DevOps & Tools

| Skill | Level | Last used | Where demonstrated |
|-------|-------|-----------|--------------------|
| Git / GitHub | confident | 2026-06 | [[projects/interview-tts]] — feature branches, PRs, commit discipline |
| SCRUM / Agile | working | 2026-06 | [[projects/interview-tts]] — sprint planning, backlog management |
| Trello | working | 2026-06 | Project management across personal projects |
| VMWare / VirtualBox | working | 2025-12 | DAM coursework — virtualized development environments |
| Nginx | familiar | 2026-06 | [[projects/interview-tts]] — reverse proxy, TLS and rate limiting |
| systemd | working | 2026-06 | [[projects/interview-tts]] — hardened unit running uvicorn, deploy + rollback scripts |
| Docker | familiar | 2026-06 | [[projects/fraud-detector]] — docker-compose, 7 services, multi-stage builds |

## Notes
- InterviewTTS uses git actively — the repo shows structured commit history with conventional commits
- Deployment: Oracle Free Tier VPS, Nginx + systemd running uvicorn directly on the host. **No containers** — InterviewTTS is not containerised.
- Docker experience comes from [[projects/fraud-detector]], not from InterviewTTS
- [TODO: ask Mikel] — Experience level with Docker beyond docker-compose? Any CI/CD experience (GitHub Actions, etc.)?
