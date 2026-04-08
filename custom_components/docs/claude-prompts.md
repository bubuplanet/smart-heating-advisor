# SHA — Claude Prompts Reference

Reusable prompts for Claude VS Code extension and GitHub Copilot.
Always start a new chat for major architectural changes.
Always run `./scripts/verify_sha.sh` after Claude finishes.

---

## When to use Claude

| Task | Use Claude | Notes |
|---|---|---|
| New feature implementation | ✅ | Use Prompt 1 audit first |
| Bug fix | ✅ | Provide error log + file context |
| Blueprint logic change | ✅ | Run dry-run after (Prompt 4) |
| CHANGELOG update | ✅ | Use Prompt 5 |
| GitHub issue creation | ✅ | Use Prompt 6 template |
| Code review / audit | ✅ | Use Prompt 1 |
| Git operations | ❌ | Do manually |
| Production deployment | ❌ | Do manually |
| Merging to master | ❌ | Do manually |

---

## Rules when working with Claude

1. Start a new chat for every major architectural change
2. Ask Claude to read all files before making any changes
3. Run `./scripts/verify_sha.sh` after every session
4. Run Prompt 2 reality check before every commit
5. Review `git diff` before committing — reject unexpected changes
6. If Claude modifies a file not in scope ask why before accepting
7. Document major AI-assisted decisions in `docs/architecture.md`

---

## Prompt 1 — Full project audit

Use at the start of a new chat before any major change.
Gives Claude complete context of the project state.

```
You are working on a Home Assistant HACS custom integration called
Smart Heating Advisor (SHA). Before making any changes perform a
complete audit of the project and tell me your understanding of
how everything works. Do not modify any files yet.

Read every file in the project then produce a structured report:

Section 1 — Project structure
List every file with a one-line description. Flag missing or
misplaced files.

Section 2 — SHA integration (Python component)
For each Python file explain what it does, what HA platforms it
registers, what services it exposes and what scheduled tasks run.

Section 3 — Blueprint
Explain: what triggers fire, how room_id is derived, what SHA
entities the blueprint reads and writes, how active_schedule is
determined, how preheat_schedule is calculated, how override works
end to end, how window detection works, how vacation mode works.

Section 4 — Entity registry
List every entity SHA creates grouped by domain. For each entity:
entity ID pattern, domain, purpose, who reads it, who writes it,
default value, whether it persists across restarts.

Section 5 — Notification audit
For every notification: trigger condition, what prevents it firing
more than once, what resets the flag, any risk of zero/multiple fires.

Section 6 — Service audit
For every service: parameters, steps, failure modes, who calls it.

Section 7 — Room discovery audit
How rooms are discovered, what happens when a room is added or
deleted, what happens on HA restart.

Section 8 — Daily and weekly analysis audit
Step by step for each: when it runs, data fetched, prompt building,
result application, failure handling.

Section 9 — Identified bugs or risks
List any bugs, race conditions or missing logic found. For each:
severity, description, file and line if possible, suggested fix.

Section 10 — Confidence assessment
Rate each section: High / Medium / Low with notes.

Do not make any changes. Audit and report only.
```

---

## Prompt 2 — Reality check after every session

Paste this into Claude before committing anything.

```
Before I commit this, answer YES / NO / PARTIAL for each item:

1. Does messages.md exist and does text_store.py load it without
   crashing?
2. Does BLUEPRINT_FILENAME in const.py match the actual blueprint
   filename?
3. Do all .py files pass python3 -m py_compile with zero errors?
4. Does switch.py create window_timeout_notified per room?
5. Does switch.py create all 5 notifications_enabled switches per
   room defaulting to on?
6. Is target_temperature floored at 4.0°C in the blueprint?
7. Is schedule_changed in the override skip exclusion list?
8. Does the vacation block send notify.notify before flipping the
   switch?
9. Are strings.json and translations/en.json in sync?
10. Is the blueprint version bumped?
11. Are there any TODO or placeholder comments left in the code?
12. Are any files modified that were not in scope for this task?

For any NO or PARTIAL explain exactly what is missing and fix it
now before I commit.
```

---

## Prompt 3 — Dry run test invocation

Use after any blueprint change. Requires `docs/dry-run-scenarios.md`.

```
Read docs/dry-run-scenarios.md and
blueprints/smart_heating_advisor.yaml carefully.

The blueprint may have changed since the scenarios were last run.
Run all scenarios in dry-run-scenarios.md against the current
blueprint version.

For each scenario:
- Trace the logic using the values defined in the scenario
- Show your working at each step
- Flag any result that differs from the expected outcome
- Flag any scenario that is no longer valid due to blueprint changes
- Suggest new scenarios for any new features added since last run

Produce a test run report showing pass/fail per scenario.
List any bugs or unexpected behaviours found while tracing.
```

---

## Prompt 4 — Blueprint notification and icon audit

Use before any blueprint UI or notification change.

```
Please do two things. Read all files carefully before starting.
Do not change any files yet — audit and report only.
Wait for my explicit approval before making any changes.

Task 1 — Audit all notifications
Read every notify.notify and persistent_notification.create call in:
- blueprints/smart_heating_advisor.yaml
- custom_components/smart_heating_advisor/__init__.py
- custom_components/smart_heating_advisor/coordinator.py

For each notification produce a table with: where it is sent,
title, message content, icon used, notification type (mobile or
persistent), and whether the content is clear and actionable.

Assess: Is the message clear? Does the title accurately describe
what happened? Is there any redundancy? Is anything missing?

Task 2 — Audit all icons and emojis
Read blueprints/smart_heating_advisor.yaml and README.md.

For every emoji in the blueprint: section headers, input names,
notification titles, description block — state where it appears,
what it marks, whether it adds value or is noise, and recommended
action: keep / remove / replace.

Apply these rules:
- Mandatory section headers: ⚠️ + section name only — no second emoji
- Optional section headers: one topic emoji + section name only
- Description block lines must use the EXACT same emoji as their
  corresponding section header
- Notification titles: maximum one emoji per notification
- No two emojis on the same header ever

Show a before/after comparison table for every emoji that appears.
```

---

## Prompt 5 — CHANGELOG update

Use after completing a dev session before committing.

```
Please update CHANGELOG.md to document all changes made in this
session.

Read the following before writing anything:
1. Read CHANGELOG.md to understand the existing format and style
2. Read manifest.json to get the current version number
3. Run: git log --oneline -20
4. Run: git diff HEAD~1 HEAD --stat

This is still a development version — not yet released.
Add or update the [Unreleased] section at the top of CHANGELOG.md.
If an [Unreleased] section already exists replace it entirely.
If it does not exist create it.

Follow the Keep a Changelog format:
https://keepachangelog.com/en/1.0.0/

Use ### Added, ### Changed, ### Fixed, ### Removed subsections.
Do not add a date to [Unreleased].
Do not change any existing entries below [Unreleased].
```

---

## Prompt 6 — GitHub issue template

Use when creating a new issue. Always add label `enhancement` or
`bug` and priority `P1`, `P2` or `P3`.

```markdown
## User story
As a user, I want [goal] so that [reason].

## Background
Currently [describe current behaviour]. By [proposed change] we
can [benefit].

## Acceptance criteria

**Criteria name**
Description of what must be true for this to be complete.

## Technical notes
Implementation hints, HA API references, edge cases to consider.

## Related
- Relates to [other issue or file]
```

---

## Prompt 7 — Bug fix template

Use when reporting a bug to Claude with full context.

```
I have a bug in Smart Heating Advisor.

Error from HA logs:
[paste error here]

File affected: [filename]
Expected behaviour: [describe]
Actual behaviour: [describe]

Read all files before proposing a fix.
Show me the exact lines that need to change.
Do not modify any other files.
After the fix tell me how to verify it is resolved.
```

---

## Prompt 8 — SubEntry implementation (room management)

Use when implementing or fixing HA SubEntry room management.

```
You are working on the Smart Heating Advisor HACS custom integration
for Home Assistant 2026.4.1. Read ALL existing files before making
any changes.

The correct HA SubEntry pattern for this version requires:
- A SEPARATE class extending ConfigSubentryFlow (not methods on the
  main config flow class)
- async_get_supported_subentry_types classmethod on the main flow
- async_setup_subentry and async_unload_subentry at module level
  in __init__.py
- Translations under config_subentries key in strings.json (NOT
  under config)
- manifest.json needs single_config_entry: true but NOT
  subentries: true (not a valid manifest key)

SubEntry flow steps for SHA room management:
- async_step_user: choose method (select area or manual)
- async_step_area: select existing HA Area (auto-detects entities)
- async_step_manual: free-text room name + optional entity selectors
- async_step_entities: confirm auto-detected entities for area path

On subentry creation (async_setup_subentry):
1. Register room in coordinator registry
2. Create all entities for the room
3. Create disabled blueprint automation
4. Send persistent notification with link to automation

On subentry deletion (async_unload_subentry):
1. coordinator.async_unregister_room()
2. Remove all entities from entity registry
3. Remove device from device registry
4. Disable blueprint automation (do not delete)
5. Send persistent notification confirming cleanup

[describe specific change needed]
```

---

## Prompt 9 — Verify script runner

Use to quickly check the project state at any time.

```
Run ./scripts/verify_sha.sh and report the results.
For any failing check explain why it fails and propose a fix.
Do not make any changes until I confirm.
```

---

## Prompt 10 — Production deploy checklist

Use before merging dev to master for a release.

```
I am preparing to release SHA version [X.X.X] to HACS.
Read all files and verify the following release checklist:

1. All Phase 1–6 tests in docs/test-plan.md are documented as
   passing
2. All scenarios in docs/dry-run-scenarios.md pass against the
   current blueprint
3. manifest.json version is [X.X.X]
4. Blueprint version in description block is [X.X.X]
5. CHANGELOG.md [Unreleased] section is complete and accurate
6. README.md installation instructions are up to date
7. hacs.json is at repo root (not inside custom_components)
8. No TODO comments left in any file
9. No debug-only code committed
10. ./scripts/verify_sha.sh passes with zero failures

For any item not confirmed as ready explain what is missing.
Do not make changes — report only.
```