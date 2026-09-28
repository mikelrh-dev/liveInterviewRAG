---
type: faq
title: strongest-backend-conversation
created: 2026-08-12
updated: 2026-09-19
confidence: high
tags: [faq, english, backend, conversation]
related: [skills/backend.md, faq/area-preferida.md]
summary_1line: An English backend question, answered in English
---

# tell me about a backend problem you solved

## Short answer

A telemetry collector kept losing the first reading after every
controller restart, which made a two year history quietly wrong at
exactly one point per restart. The fix was to persist the controller
boot id alongside every row and treat a change in it as a hard
boundary, so a partial reading is never stitched onto the previous
session.

## Long answer

The symptom looked like a data gap and everyone wanted to fill the gap.
The cause was that the collector did not know when a controller had
rebooted, so the ramp-up readings of a new session were appended to
the tail of the old one. Persisting the boot id and rejecting a row
whose session changed mid series took two days. The lesson was that
"we lost a reading" is almost never a missing number, it is a missing
boundary.

## Fuentes
- [[skills/backend]]
- [[skills/data]]

## See also
- [[skills/testing]]
