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

## Pillar 1 — `memchorus-recall` (implemented)

**Recipe:**

```bash
python3 team-health/scripts/check_recall.py --profile <name>   # single profile
python3 team-health/scripts/check_recall.py --all --pretty --out <file>
```

**How it verifies (content-first, per #312):**
1. Collects the last N (default 3) captured turns from `request_dump_*.json` under the profile's `~/.hermes/<profiles>/<name>/sessions/` (or `~/.hermes/sessions/` for `default`). Dumps are API-failure captures — the only on-disk copy of a turn's real prompt.
2. Searches turn text for CONTENT markers: `[MemChorus injected context]` / `<!-- memchorus:recalled -->` (recall actually landed). The WRAPPER `[MemChorus Memory Recall]` alone means block-present-but-no-content — the #312 false-positive class, NOT a landing.
3. Runs `memchorus-doctor` (stdout parsed, NOT exit code) as contrast signal only. `inprocess_interpreter` is the #312 root-cause probe.

**Verdicts:**
- **GREEN** — content marker in ≥1 of the last N captured turns, landing within the freshness window (`--max-landing-age-hours`, default 72h).
- **YELLOW** — (a) #312 signature: probe green but no content landed; or (b) content landed but the newest landing is older than the freshness window (stale evidence cannot verify current recall state).
- **RED** — no content in the last N captured turns AND probe red/unknown; or zero captured turns with probe red.

**Documented misses (phase gate, live evidence from 2026-11-22 run, `evidence/recall-run-20261122.json`):**
- **False positive — `default` profile:** check initially went GREEN on a landing from 2026-11-03 (461h / 19 days old) while the profile had zero captured turns since. A green verdict built on 19-day-old evidence asserts a current recall state that is unverified — the check said green, but no work in the last 19 days actually used that recall. **Miss → check change:** freshness window added (`--max-landing-age-hours`, default 72h); `default` now downgrades to YELLOW (stale evidence) instead of GREEN.
- **False negative — `cthugha` profile:** check says RED (no content in last 3 captured turns; newest captured turn 2026-11-18, probe `inprocess_interpreter` red) while the cthugha session of 2026-11-22 is actively receiving an injected `[MemChorus injected context]` block in its turn prompt (visible in-session). The work is succeeding with recall; the check misses it because request dumps are only written on API-failure paths, so healthy turns never produce evidence, and the probe red reflects the gateway/tools interpreter (which cannot import memchorus) rather than the interpreter actually serving the session (install-env venv, where `import memchorus` succeeds). **Miss → check change (next):** add a second, live evidence source — an active in-process probe that executes the hook's `on_pre_llm_call` under the profile's actual Python and asserts non-empty `context_items`, so GREEN can be verified without waiting for an API failure. Until then, RED verdicts for profiles with an active session should be read as "no captured evidence of landing" rather than "recall is dead".

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
