---
name: team-health
description: 'Team health monitoring system — separates measurement from routing, uses shared evidence schema.'
version: 0.1.0
author: nyarlathotep
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [health, monitoring, automation, evidence]
    related_skills:
      - development-process
      - memchorus-pr-collab
---

# Team Health System

Lightweight health monitoring that separates measurement (health) from routing/coordination (a_edificio). One shared evidence record feeds both readers.

## Architecture Rules

1. **Separation of concerns:** Health emits evidence only. a_edificio reads evidence and routes work. No private scores.
2. **Phase gates:** A phase is not done until documented false-positive AND false-negative cases exist for every pillar.
3. **Auto-correction limits:** Health opens comments/cards only. Never modifies another profile's model, skills, or MemChorus config directly.
4. **Shared evidence schema:** All checks output `{profile, scope, check, result, time}` — no second scoreboard.

## Pillars (Phase 1)

| Pillar | Check Name | Verifies | Status Source |
|--------|-----------|----------|--------------|
| 1 | `memchorus-recall` | Actual recall injection in turns | Turn system prompt (not doctor-probe) |
| 2 | `polygraph-health` | Polygraph CLI usable, no blocked PRs | Polygraph results + repo state |
| 3 | `task-completion-rate` | % of tasks hitting completion gate | Kanban board evidence |
| 4 | `agent-communication` | Cross-agent DM success rate | Bot mode DM logs |

## Evidence Schema

See `health-rules.json` for complete schema. Each pillar outputs JSON matching:

```json
{
  "profile": "<agent profile name>",
  "scope": "<check scope tag>",
  "check": "<check identifier>",
  "result": {"status": "green|yellow|red", "details": "..."},
  "time": "ISO-8601 timestamp"
}
```

## Phase Gate Requirement

Before advancing Phase N to N+1:
- Document at least one false-positive (check said green, work failed)
- Document at least one false-negative (check said red/yellow, work succeeded)
- The miss becomes the next change to the check, not a chat note

## Pitfalls

- Doctor-probe status ≠ actual recall injection (#312 constraint)
- No silent config edits — auto-correction = new card
- Don't stack Phase N+1 without Phase N's documented misses
- Evidence schema must be reusable across all checks
