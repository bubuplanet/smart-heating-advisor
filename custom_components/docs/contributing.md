# Contributing to Smart Heating Advisor

Thank you for considering a contribution. Please read this document before opening a pull request.

---

## Table of Contents

- [Project structure](#project-structure)
- [Development setup](#development-setup)
- [Logging standards](#logging-standards)
  - [Python logging (integration code)](#python-logging-integration-code)
  - [Blueprint logging (YAML automations)](#blueprint-logging-yaml-automations)
- [Pull request checklist](#pull-request-checklist)

---

## Project structure

```
custom_components/smart_heating_advisor/
  __init__.py          — integration setup, service handlers, blueprint installer
  coordinator.py       — room registry, InfluxDB queries, AI analysis orchestration
  analyzer.py          — heating session analysis, prompt builders (pure functions)
  ollama.py            — Ollama HTTP client
  config_flow.py       — UI config and options flows
  sensor.py            — sensor platform stub, reserved for future use
  switch.py            — boolean flag switches and override switch
  number.py            — heating rate number entity
  text_store.py        — notification message template loader
  const.py             — all constants
  messages.md          — default notification text templates
  blueprints/
    smart_heating_advisor.yaml  — per-room heating automation blueprint
```

---

## Development setup

1. Clone the repository and copy `custom_components/smart_heating_advisor/` into your HA config directory.
2. Restart Home Assistant.
3. Enable **Debug logging** in the integration options (**Settings → Devices & Services → Smart Heating Advisor → Configure**).
4. Tail the HA log and filter by `smart_heating_advisor`:
   ```
   grep smart_heating_advisor /config/home-assistant.log
   ```

---

## Logging standards

SHA uses two separate logging mechanisms that must both be kept consistent.

### Python logging (integration code)

All Python files use the stdlib `logging` module via:

```python
_LOGGER = logging.getLogger(__name__)
```

The package-level logger (`custom_components.smart_heating_advisor`) is the parent of all submodule loggers. Its effective level is controlled at runtime by the **Debug logging** toggle in the integration options — no `configuration.yaml` changes needed.

#### Level rules

| Level | When to use | Examples |
|---|---|---|
| `INFO` | One or two lines per major lifecycle event | `"[Bathroom] Daily analysis complete. New rate: 0.130"`, `"SHA blueprint updated v0.0.1 → v0.0.2"` |
| `DEBUG` | Raw data payloads, internal decision traces | Full AI prompts, raw JSON responses, InfluxDB CSV content, entity ID lists |
| `WARNING` | Recoverable degradation — system continues | Missing entity, insufficient InfluxDB readings, no rooms discovered |
| `ERROR` | Non-recoverable failure that aborts the current operation | InfluxDB query failure, invalid AI JSON, blueprint file missing |

Never use `CRITICAL` — HA itself will elevate to that if integration setup fails.

#### Contextual metadata

Every log entry **must** include `room_name` or `entity_id` as the first substitution argument, using the `[room]` bracket prefix:

```python
# ✅ correct
_LOGGER.info("[%s] Daily analysis complete. New rate: %.3f", room.room_name, new_rate)
_LOGGER.debug("[%s] Schedule entity %s not found in HA states", room.room_name, entity_id)

# ❌ wrong — no room context
_LOGGER.info("Daily analysis complete. New rate: %.3f", new_rate)
```

#### Lazy evaluation for heavy DEBUG payloads

Gate any log call that builds a large string (AI prompts, raw API responses, list comprehensions over datasets) with an explicit `isEnabledFor` guard. This prevents string-formatting overhead when the debug toggle is off:

```python
# ✅ guarded — string only built when DEBUG is active
if _LOGGER.isEnabledFor(logging.DEBUG):
    _LOGGER.debug("[%s] Raw AI prompt (%d chars):\n%s", room_name, len(prompt), prompt)

# ❌ eager — prompt string built on every call regardless of log level
_LOGGER.debug("[%s] Raw AI prompt (%d chars):\n%s", room_name, len(prompt), prompt)
```

Apply the guard when the log call involves any of:
- A multiline string (AI prompt, raw HTTP response body)
- A list comprehension: `[r.room_name for r in rooms]`
- A `dict(...)` copy or `str(obj)` on a large object

Simple scalar substitutions (`%s`, `%d`, `%.3f`) do **not** need a guard.

#### Secret and token redaction

Never log API keys, tokens, or credentials — even at `DEBUG` level.
Use `_mask_secret()` from `coordinator.py` for any potentially sensitive string:

```python
from .coordinator import _mask_secret

_LOGGER.debug("Connecting to InfluxDB — token: %s", _mask_secret(token))
# Output example: "Connecting to InfluxDB — token: ****************abc1"
```

`_mask_secret(value, visible=4)` shows only the last `visible` characters. Returns `'<empty>'` for falsy input.

#### Truncating unbounded external content on ERROR paths

When logging content received from an external system (AI response, HTTP body) on an error path, always cap the length to prevent multi-kilobyte garbage from flooding the log:

```python
# ✅ safe — limits to 200 chars
_LOGGER.error("[%s] Invalid Ollama response (first 200 chars): %s", room_name, (response or "")[:200])

# ❌ unbounded — HTML error pages or unexpected content can flood the log
_LOGGER.error("[%s] Invalid Ollama response: %s", room_name, response)
```

---

### Blueprint logging (YAML automations)

The blueprint uses `system_log.write` to emit log entries directly into the HA log. All blueprint log entries appear under `custom_components.smart_heating_advisor` alongside the Python logs.

#### Template

```yaml
- action: system_log.write
  data:
    message: "SHA [{{ room_name }}] Your message here."
    level: info        # info | warning | error | debug
    logger: custom_components.smart_heating_advisor
```

#### Level rules (blueprint)

| Level | When to use |
|---|---|
| `info` | User-visible state transitions: pre-heat start, schedule ON/OFF, override start/end, window open/close |
| `debug` | High-frequency internal state: control loop tick (every 5 min), variable dump for troubleshooting |
| `warning` | Unexpected but recoverable conditions detectable in the blueprint |
| `error` | Should not occur in normal operation |

Never use `info` for the control loop tick — it fires every 5 minutes per room and would make the HA log unreadable. Use `debug` so it only appears when the SHA debug toggle is on.

#### Message format

Messages must follow this pattern:

```
SHA [<room_name>] <Event description>. <Key values>. Context: <room_temp>°C.
```

Examples:
```
SHA [Bathroom] Pre-heat started for 'Morning Shower 26C' — target 26°C, ETA 28 min, current 19.5°C, rate 0.150°C/min.
SHA [Living Room] Schedule 'Evening 20C' ON — commanding heat to 20.0°C. Current: 17.3°C.
SHA [Bathroom] Control loop: mode=heat, target=26.0°C, room=19.5°C, in_comfort=False, in_preheat=True.
```

The `SHA [room]` prefix makes all blueprint log lines grep-friendly:

```bash
grep "SHA \[Bathroom\]" /config/home-assistant.log
```

#### Where to place blueprint log actions

- Place `system_log.write` **before** the service calls it describes — so the intent is visible in the log even if the service call fails.
- For `debug`-level control loop entries, place them at the very start of the `default:` branch, before the climate service calls.
- Do not add log entries inside notification `if/then` blocks — those already have user-visible feedback via `notify.notify`.

---

## Pull request checklist

- [ ] Every new Python log entry has `[room_name]` or `entity_id` context
- [ ] Heavy DEBUG payloads are guarded with `isEnabledFor(logging.DEBUG)`
- [ ] No API keys, tokens, or secrets appear in any log statement
- [ ] Unbounded external content on ERROR paths is truncated to 200 chars
- [ ] New blueprint actions that represent state transitions include a `system_log.write` at `info` level
- [ ] Control loop or high-frequency blueprint actions use `debug` level only
- [ ] `_mask_secret()` is used for any token or credential that must appear in a debug trace


---

## Branch strategy

| Branch | Purpose |
|---|---|
| `dev` | Active development — all daily work |
| `master` | Stable releases only — never commit directly |

---

## Commit message format

| Prefix | When to use |
|---|---|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `chore:` | Maintenance |
| `docs:` | Documentation only |
| `refactor:` | Code restructure, no behaviour change |
| `test:` | Test plan or dry-run scenario changes |
| `wip:` | Work in progress |

Example:
  fix(blueprint): add schedule_changed to override skip list

---

## Before every commit

  cd custom_components/smart_heating_advisor
  for f in *.py; do python3 -m py_compile "$f" && echo "✅ $f" || echo "❌ $f"; done
  python3 -c "import json; json.load(open('strings.json'))" && echo "✅ strings.json"
  python3 -c "import json; json.load(open('translations/en.json'))" && echo "✅ en.json"

All files must compile and all JSON must parse without errors.

---

## Before every merge to master

- All Phase 1–8 tests in docs/test-plan.md pass with zero critical failures
- Dry-run scenarios in docs/dry-run-scenarios.md pass against current blueprint
- No errors in HA logs for 48 hours on production
- Version bumped in manifest.json
- SHA_AUTOMATION_VERSION bumped in const.py if blueprint changed
- CHANGELOG.md [Unreleased] section updated

---

## Release process

  git checkout master
  git merge dev
  git push origin master
  git tag -a v0.0.1 -m "First stable release"
  git push origin v0.0.1
  # Create GitHub Release in UI
  git checkout dev
  git merge master

---

## AI assistance

SHA uses Claude for development. See docs/claude-prompts.md
for all reusable prompts and rules.

Key rules:
- Start a new chat for every major architectural change
- Run py_compile on all .py files after every Claude session
- Run the reality check prompt before every commit
- Review git diff before committing
- Document major decisions in docs/architecture.md

## Keeping docs/claude-prompts.md up to date

At the end of any Claude session that produced a new reusable prompt
or improved an existing one paste this into the chat:

  Review this entire conversation and identify any prompts that
  were used or created that are not yet in docs/claude-prompts.md.

  Read docs/claude-prompts.md first to understand the existing
  structure and format.

  For each new prompt found in this conversation:
  - Give it a clear number and title
  - Add a one-line description of when to use it
  - Add the full prompt text in a code block
  - Place it in the correct section or create a new section

  Also update any existing prompts if this session produced an
  improved version of something already there.

  Do not remove any existing prompts.
  Do not change the format or structure of existing entries.
  Only add or improve.

  Then commit:
  git add docs/claude-prompts.md
  git commit -m "docs: update claude prompts from session YYYY-MM-DD"
  git push origin dev