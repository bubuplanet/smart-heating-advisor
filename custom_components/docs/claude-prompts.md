````markdown
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
| Blueprint logic change | ✅ | Run dry-run after (Prompt 3) |
| CHANGELOG update | ✅ | Use Prompt 5 |
| GitHub issue creation | ✅ | Use Prompt 6 template |
| Code review / audit | ✅ | Use Prompt 1 |
| Log analysis | ✅ | Use Prompt 12 |
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
8. After every session paste the prompt update reminder from
   CONTRIBUTING.md to keep this file current

---

## Prompt 1 — Full project audit

Use at the start of a new chat before any major change.
Gives Claude complete context of the project state.

````
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
````

---

## Prompt 2 — Reality check after every session

Paste this into Claude before committing anything.

````
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
13. Does the blueprint variables section contain any references to
    input_number.sha_* or input_boolean.sha_*? If yes remove them.
14. Does fixed_radiator_temperature resolve to the automation input
    value and not to 0 or unknown?
15. Is fixed_radiator_thermostats commanded in pre-heat, comfort
    and window close resume phases?

For any NO or PARTIAL explain exactly what is missing and fix it
now before I commit.
````

---

## Prompt 3 — Dry run test invocation

Use after any blueprint change. Requires docs/dry-run-scenarios.md.

````
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
````

---

## Prompt 4 — Blueprint notification and icon audit

Use before any blueprint UI or notification change.

````
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
````

---

## Prompt 5 — CHANGELOG update

Use after completing a dev session before committing.

````
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
````

---

## Prompt 6 — GitHub issue template

Use when creating a new issue. Always add label `enhancement` or
`bug` and priority `P1`, `P2` or `P3`.

Always wrap the entire issue body in a single code fence so it
is one clean copy paste into GitHub:

```markdown
## Description
Clear one paragraph description of the issue.

## Steps to reproduce
1. Step one
2. Step two
3. Observe result

## Expected behaviour
What should happen.

## Actual behaviour
What actually happens.

## Quick checks before investigating

    command or state to check

## Hypotheses
- Hypothesis 1
- Hypothesis 2

## Claude VS Code investigation prompt

Read ALL files carefully. Do not make any changes. Analysis only.

Files to read:
- file1
- file2

Answer each question with the exact code or template that
supports your answer.

1. Question one
2. Question two

Do not propose fixes. Analysis only.

## Environment
- HA Version:
- SHA Version:
- Room:

## Related
- Related issue or scenario
```

---

## Prompt 7 — Bug fix template

Use when reporting a bug to Claude with full context.

````
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
Provide the git commit message in this format:

  fix(scope): short description

  - Detail line 1
  - Detail line 2
````

---

## Prompt 8 — SubEntry implementation (room management)

Use when implementing or fixing HA SubEntry room management.

````
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
````

---

## Prompt 9 — Verify script runner

Use to quickly check the project state at any time.

````
Run ./scripts/verify_sha.sh and report the results.
For any failing check explain why it fails and propose a fix.
Do not make any changes until I confirm.
````

---

## Prompt 10 — Production deploy checklist

Use before merging dev to master for a release.

````
I am preparing to release SHA version [X.X.X] to HACS.
Read all files and verify the following release checklist:

1. All Phase 1–6 tests in docs/test-plan.md are documented as passing
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
11. No input_number.sha_* or input_boolean.sha_* references
    remain in the blueprint variables section
12. All blueprint variables that read SHA entities use the
    number.sha_* and switch.sha_* native entity patterns

For any item not confirmed as ready explain what is missing.
Do not make changes — report only.
````

---

## Prompt 11 — Align all version numbers

Use when you want to ensure all version references across the
entire project are consistent before a release or after bumping
the version.

````
Read all files in the project carefully before making any changes.

Find every place where a version number is mentioned across all
files and align them all to [TARGET VERSION].

Search in these locations:
1. manifest.json — field: "version"
2. blueprints/smart_heating_advisor.yaml — version: X.X.X
   in the description block
3. const.py — any VERSION constant if it exists
4. README.md — any version badges or version references
5. hacs.json — check for version field
6. CHANGELOG.md — do NOT change, version history must stay as-is

For each file found with a version number show me:
- File path
- Current version found
- Line number
- What it will be changed to

All versions must be exactly: [TARGET VERSION]
No other format acceptable (not v0.0.1, not 0.0.1-dev).

After showing me the list wait for my confirmation before making
any edits. Once confirmed apply all changes then verify no syntax
errors. Show me the final state of each changed line before
committing.

Replace [TARGET VERSION] with the version you want to align to
before using this prompt.
````

---

## Prompt 12 — Production log analysis

Use when you have HA logs from a test session and want Claude
to identify what is working and what is failing.

````
Analyse the following HA log extract from a SHA production test.
Do not make any changes. Analysis and report only.

Read blueprints/smart_heating_advisor.yaml before analysing
so you understand the expected behaviour at each step.

Log extract:
[paste log here]

Test context:
- Room: [room name]
- Schedule: [schedule name and time slot]
- What was being tested: [describe scenario]
- What was expected: [describe expected behaviour]

For each SHA log entry found answer:
1. What event does this entry represent?
2. Is this the expected behaviour at this point in the test?
3. If unexpected — what does it indicate is wrong?

Then produce a summary table:

| Time | Event | Expected | Actual | Status |
|---|---|---|---|---|

Then list all issues found with:
- What failed
- Most likely root cause based on the blueprint logic
- Which file and section to investigate

Do not propose fixes. Analysis only.
````

---

## Prompt 13 — Old architecture cleanup

Use when removing input_number or input_boolean helper
references left over from the old SHA architecture.

````
Read ALL files carefully before making any changes:
- blueprints/smart_heating_advisor.yaml
- custom_components/smart_heating_advisor/switch.py
- custom_components/smart_heating_advisor/number.py
- custom_components/smart_heating_advisor/__init__.py
- custom_components/smart_heating_advisor/coordinator.py
- custom_components/smart_heating_advisor/const.py

Audit and remove all old architecture leftovers following
these rules:

REMOVE — old architecture patterns:
- input_number.sha_ROOM_* references in blueprint variables
- input_boolean.sha_ROOM_* references in blueprint variables
- Any variable that reads from these helpers instead of
  using !input or SHA native entities directly

KEEP — new architecture patterns:
- number.sha_ROOM_heating_rate — needed by coordinator
  for AI analysis and by blueprint for pre-heat calculation
- switch.sha_ROOM_* — all notification flag switches
- sensor.sha_ROOM_* — all read-only coordinator sensors

For each old reference found:
- Show the current code
- Confirm whether a replacement is needed
- If yes show the replacement using the correct pattern
- If no remove entirely

Do not change anything until you have shown me the full list
of what will be removed and replaced.
After my confirmation apply all changes and provide the
git commit message.
````

---

## Prompt 14 — Blueprint variable shadowing audit

Use when suspecting a blueprint input value is being
overwritten by a variable with the same name.

````
Read blueprints/smart_heating_advisor.yaml carefully.
Do not make any changes. Analysis only.

A blueprint variable that has the same name as a blueprint
input will overwrite the input value for the entire automation
run. This causes silent failures where the input shows the
correct value in the UI but the wrong value is used at runtime.

For every entry in the blueprint variables section:
1. Does a blueprint input exist with the same name?
2. If yes what does the variable template evaluate to?
3. Is the variable template reading from an entity that
   exists and has the correct value?
4. Could the variable ever resolve to unknown, unavailable
   or 0 due to a missing entity?

Produce a table:

| Variable name | Input with same name | Variable reads from | Risk of shadowing |
|---|---|---|---|

Flag any variable where:
- It has the same name as an input AND
- It reads from input_number.sha_* or input_boolean.sha_* AND
- That entity may not exist or may return unknown

These are the highest priority fixes.
````
````