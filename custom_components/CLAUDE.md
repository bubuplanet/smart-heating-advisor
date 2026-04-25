# CLAUDE.md — Smart Heating Advisor (SHA)

> This file is the authoritative context for Claude Code. Read it before touching any file.

---

## Project Overview

SHA is a Home Assistant custom integration that uses AI (Ollama/phi4) to learn how a home heats and automatically adjusts radiator TRV setpoints. It runs daily self-correcting analysis via InfluxDB data and weekly plain-language reports.

- **Repo:** https://github.com/bubuplanet/smart-heating-advisor
- **Active branch:** `dev` — always work here; `master` is releases only
- **Version:** `0.0.1` (in `manifest.json`)

---

## Infrastructure

| Component   | Value                                        |
|-------------|----------------------------------------------|
| HA version  | 2026.4.3, Supervisor, OS 17.2, RPi4 aarch64 |
| Ollama      | http://192.168.187.195:11434, model: `phi4`  |
| InfluxDB v2 | http://192.168.187.195:8086, org: `Jhome`, bucket: `home_assistant` |
| Lab rooms   | Entrance, Bedroom Parent                     |
| Prod room   | Bathroom (separate HA install)               |

---

## File Structure

```
custom_components/smart_heating_advisor/
  __init__.py              # setup, migrations, automation lifecycle
  coordinator.py           # data fetch, analysis orchestration
  analyzer.py              # session detection, heating rate calc
  config_flow.py           # wizard UI — setup and room config
  number.py                # heating_rate, trv_setpoint, comfort_temp
  switch.py                # airing_mode, override switches
  binary_sensor.py         # window_open (per room), vacation (global)
  sensor.py                # stub — reserved for future prediction sensors
  ollama.py                # Ollama HTTP client
  prompt_loader.py         # loads prompts from /config/smart_heating_advisor/prompts/
  text_store.py            # loads setup notification from messages.md
  const.py                 # ALL constants live here
  strings.json             # HA UI strings — SOURCE OF TRUTH
  translations/en.json     # runtime strings — MUST mirror strings.json exactly
  manifest.json            # version 0.0.1
  blueprints/smart_heating_advisor.yaml   # internal blueprint (never UI-visible)
  prompts/daily_analysis.md              # Ollama daily prompt template
  prompts/weekly_analysis.md             # Ollama weekly prompt template
```

---

## Integration Architecture

- **integration_type:** `"service"`, **single_config_entry:** `true`
- Config uses **subentries**: one per room + one for vacation
- Automations are written inline to `automations.yaml` — the blueprint file lives in `custom_components` and is never surfaced in the HA UI
- All analysis is time-driven: daily at 00:01, weekly Saturday at 06:00

---

## Entities Per Room

| Entity | Kind | Updated by |
|--------|------|------------|
| `number.sha_{room}_heating_rate` | number | AI daily |
| `number.sha_{room}_trv_setpoint` | number | AI daily |
| `number.sha_{room}_comfort_temp` | number | user via wizard |
| `switch.sha_{room}_airing_mode` | switch | user |
| `switch.sha_{room}_override` | switch | user |
| `binary_sensor.sha_{room}_window_open` | binary_sensor | only if window sensors configured |
| `binary_sensor.sha_vacation` | binary_sensor | global, not per room |

**Entity prefix rule:** always `sha_{room_id}` — no exceptions.  
**room_id rule:** snake_case derived from room name, e.g. `"Bedroom Parent"` → `bedroom_parent`.

---

## Config Flow Conventions

The room wizard has exactly **4 steps**:

1. Room name (area dropdown + free text), temperature sensor, main TRVs, fixed TRVs *(collapsed)*, manual override *(collapsed)*, humidity monitoring *(collapsed)*
2a. Select schedules — `EntitySelector`, multi-select  
2b. Set target temp per schedule — `NumberSelector`, dynamic  
3. Window sensors + airing duration — `DurationSelector`  
4. Review and confirm

**Schema population:**
- Always use `add_suggested_values_to_schema()` to pre-fill, **never** `default=`
- Never pass `None` or empty string to `EntitySelector` in suggested values

**Reconfigure:** use `_get_reconfigure_subentry()` + `async_update_and_abort`

**Sections:** use HA `data_entry_flow.section()`. Collapsible section keys: `fixed_trvs_section`, `override_section`, `humidity_section`.

---

## Schedules Data Format

Stored in subentry as a list of dicts — **never** a plain string list:

```python
schedules: [{"entity_id": "schedule.xxx", "target_temp": 26.0}]
```

- Target temp is always read from the dict, never parsed from the schedule entity name
- Migration handles the legacy string-list format (Phase 4) — do not remove that migration guard

---

## Vacation Logic

Priority: **date range** (today within range) → **manual toggle**

Subentry fields: `vacation_enabled`, `vacation_mode` (`frost` / `eco` / `off`), `vacation_start_date`, `vacation_end_date` (ISO date strings).

- Coordinator re-evaluates hourly
- Active vacation **blocks pre-heat entirely** — it is priority #2 in the control loop

---

## Control Loop (every 5 min, strict priority order)

```
1. Window open         → TRV OFF
2. Vacation active     → vacation_mode temp
3. Airing mode         → TRV OFF for airing_duration
4. Manual override     → pause all control
5. Schedule active     → schedule target_temp
6. Pre-heat window     → heat to trv_setpoint
7. Comfort temp on     → comfort_temp
8. Nothing             → frost (4 °C)
```

**Pre-heat fires ONCE per session.** The `preheat_already_running` check reads TRV state to avoid re-triggering every 5-minute loop iteration.

---

## Blueprint / Automation Rules

- Blueprint file: `custom_components/.../blueprints/sha.yaml`
- On room creation, `__init__.py` reads the blueprint, expands `!input room_id` inline, strips the `blueprint:` header, and appends the result to `automations.yaml`
- Automation alias format: `"SHA — {room_name}"`
- Automation version constant: `SHA_AUTOMATION_VERSION = "0.0.20"` in `const.py`
- SHA detects outdated automations on startup and **recreates** them automatically
- Automation is enabled immediately on creation
- Automation is **deleted** when the room subentry is deleted
- **All** SHA automations are deleted on integration uninstall

---

## AI Analysis

### Daily (00:01)
- Reads 30 days from InfluxDB
- Detects heating sessions, calculates observed rate
- Sends enriched prompt (from `prompts/daily_analysis.md`) to Ollama
- Updates `number.sha_{room}_heating_rate` and `number.sha_{room}_trv_setpoint`
- **Critical rule:** Ollama must use the **observed rate exactly** — it must not smooth or invent values
- Rate bounds: min `0.01 °C/min`, max `0.30 °C/min`
- Sends one persistent HA notification per room (`notification_id: sha_daily_{room_id}`)

### Weekly (Saturday 06:00)
- 30-day performance report via `prompts/weekly_analysis.md`
- Root cause taxonomy (use these exact strings as constants):
  - `HARDWARE_INSUFFICIENT`
  - `PREHEAT_TOO_SHORT`
  - `TRV_SETPOINT_TOO_LOW`
  - `HEAT_LOSS_HIGH`
  - `RECENT_DEGRADATION`
- Sends one persistent HA notification per room every Saturday (`notification_id: sha_weekly_{room_id}`)
- Notification title uses ✅ when on target, ⚠️ when `consistent_miss` is True
- Notification content is the plain-language `report_text` from Ollama
- **One notification per room** — notifications never clobber each other across rooms

---

## Persistent Notifications

Three notification touchpoints. All use `pn_async_create` imported from
`homeassistant.components.persistent_notification`.

| Touchpoint | Where | `notification_id` | Fires |
|---|---|---|---|
| Integration install | `__init__.async_setup_entry` | `sha_setup_complete` | Once, guarded by `setup_notification_sent` flag |
| Daily analysis | `coordinator.async_run_daily_analysis` | `sha_daily_{room_id}` | Every daily run, per room |
| Weekly analysis | `coordinator.async_run_weekly_analysis` | `sha_weekly_{room_id}` | Every Saturday run, per room |

**Rules:**
- IDs must be room-scoped (include `room_id`) for daily and weekly so notifications across rooms never clobber each other and can be dismissed individually
- One notification per room per run — never one global notification for all rooms
- Weekly fires regardless of `consistent_miss` status; `consistent_miss` only affects the title emoji

---

## Migrations — Do Not Break

| Phase | What it does |
|-------|--------------|
| 3 | Adds new wizard fields to existing subentries |
| 3b | Renames `airing_duration_minutes` → HH:MM:SS; `default_temp` → `comfort_temp` |
| 4 | Converts schedules from string list → list of dicts with `target_temp` |

**Migration guards** for `default_temp` references exist in `__init__.py` and `number.py`. These are **intentional backward-compat code** — do not remove them even if they look redundant.

---

## Strings Convention

`strings.json` is the **source of truth** for all UI strings.  
`translations/en.json` must mirror it exactly.  
When you add or change any string: update **both files** in the same commit. Drift between them causes HA validation failures.

All constants go in `const.py` — no magic strings scattered through other files.

---

## Known Issues (do not re-introduce fixes)

1. **Automation not linked to device card** — cosmetic, low priority, leave it
2. **comfort_temp restores 18 °C default** instead of wizard value on new room creation — fix committed to `dev`, pending deploy; do not re-open
3. **Icon** — new 256×256 `icon.png` committed, needs live verification

---

## Planned Phases (do not implement speculatively)

| Phase | Topic |
|-------|-------|
| 6 | Ambient accuracy measurement in `analyzer.py` |
| 7 | Replace InfluxDB with HA statistics API (recorder) |
| 8 | Replace Ollama with HA native LLM API |
| 9 | Lovelace dashboard card |
| 10 | Device triggers (heating started, override activated) |

Only implement a phase when explicitly instructed. Do not anticipate or scaffold future phases.

---

## Developer References

- HA integration dev docs: https://developers.home-assistant.io/
- HA design system: https://design.home-assistant.io/
- HA data platform: https://data.home-assistant.io/