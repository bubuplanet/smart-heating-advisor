# Changelog

All notable changes to this project will be documented in this file.

---

## [Unreleased]

### Added
- Area-based room discovery in config flow — select rooms from
  HA Areas instead of manually creating automations
- Config flow Step 3: multi-select HA Areas for room management
- Config flow Step 4: per-room entity confirmation with
  auto-detected temperature sensor and TRVs from the selected area
- Config flow Step 5: weather entity (moved from Step 3)
- Options flow: add or remove rooms after initial setup via
  Settings → Integrations → SHA → Configure
- Options flow Add Room: area selection + entity confirmation
  creates entities and blueprint automation without HA restart
- Options flow Remove Room: multi-select rooms to remove with
  full cleanup of entities, registry and automation
- SHA automatically creates all helper entities per room on setup
  without waiting for blueprint automation to run first
- SHA automatically creates a disabled blueprint automation per
  room pre-filled with auto-detected entities from the selected area
- switch.sha_ROOM_window_timeout_notified entity per room
- switch.sha_ROOM_preheat_notifications_enabled entity per room
- switch.sha_ROOM_target_notifications_enabled entity per room
- switch.sha_ROOM_standby_notifications_enabled entity per room
- switch.sha_ROOM_window_notifications_enabled entity per room
- switch.sha_ROOM_override_notifications_enabled entity per room
- sha.unregister_room service — removes a room from SHA registry,
  disables its blueprint automation and removes all SHA entities
- messages.md — notification message templates file
- Weekly analysis catch-up on HA restart if last run was
  over 7 days ago
- Persistent notification on room removal confirming what was
  cleaned up and linking to the disabled automation

### Changed
- Room registration no longer requires blueprint automation to
  run first — rooms are populated from config flow on setup
- sha.register_room service kept for backwards compatibility only
  and marked as deprecated in services.yaml
- Blueprint setup description updated to reflect new Area-based
  setup flow
- Log level changed from logging.WARNING to logging.NOTSET so
  SHA INFO logs are visible without enabling debug mode
- room_id derivation aligned between blueprint Jinja and
  coordinator Python — both now strip all non-alphanumeric
  characters using regex
- Weather entity moved to Step 5 of config flow
- sha.unregister_room now also disables the room automation
  and removes all entities — not just the registry entry
- Sensor "Heating Rate" renamed to "Heating Rate (Analysis)"
  to avoid duplicate name on device page

### Fixed
- Blueprint automation creation error: removed invalid 'enabled'
  key from automation dict passed to HA blueprint API
  (extra keys not allowed @ data['enabled'])
- target_temperature floored at 4.0°C — prevents invalid value
  error on TRVs when heating rate helper is unavailable
- schedule_changed added to override skip exclusion list —
  prevents schedule state changes from overwriting manual TRV
  settings during an active override
- Vacation mode now sends notify.notify when activated —
  previously only flipped the switch silently with no user
  notification
- window_timeout_notified switch now created per room — fixes:
  (a) window-open notification firing every 5 min with no dedup
  (b) window-closed notification never firing
- Blueprint filename constant corrected in const.py — was
  sha_unified_heating.yaml, now smart_heating_advisor.yaml
- FileNotFoundError on startup caused by missing messages.md
- Cannot add new room after initial setup — options flow now
  fully implements add and remove room flows
- Stale rooms remaining in registry after automation deletion —
  unregister_room now fully cleans up all associated entities

### Removed
- register_room call from blueprint first action — rooms are
  now managed exclusively by the config and options flow

---

## v0.0.2 — 2026-04-07

### Architecture changes
- Replaced blueprint-driven room registration (`sha.register_room`) with Area-based room discovery in the config flow — rooms are now configured once during SHA setup by selecting HA Areas, with no manual automation creation required.
- SHA automatically registers all rooms in its internal registry during `async_setup_entry`, without waiting for a blueprint automation to run.
- SHA automatically creates all helper entities (number, switch, sensor) per room immediately on setup.
- SHA automatically creates a disabled blueprint automation per room, pre-filled with the room name and the auto-detected temperature sensor and TRVs from the selected HA Area.
- `sha.register_room` service retained for backwards compatibility but is no longer called by the blueprint.

### New config flow steps
- **Step 3 — Select rooms:** multi-select HA Areas using the Area registry; skips gracefully if no areas exist.
- **Step 4 — Room entities:** per-room entity confirmation form (iterates once per selected area), showing auto-detected temperature sensor and TRVs; user can override or leave blank.
- **Step 5 — Weather entity:** unchanged functionality, renumbered from step 3.
- **Options flow extended:** new `add_rooms` / `add_room_entities` sub-flow to add rooms after initial setup; new `remove_rooms` sub-flow to remove rooms; changes are saved to `entry.data` and trigger an integration reload automatically.

### New entities per room
- `switch.sha_ROOM_window_timeout_notified` — deduplication flag for window open/close notifications.
- `switch.sha_ROOM_preheat_notifications_enabled` — runtime toggle for pre-heat notifications.
- `switch.sha_ROOM_target_notifications_enabled` — runtime toggle for target-reached notifications.
- `switch.sha_ROOM_standby_notifications_enabled` — runtime toggle for standby notifications.
- `switch.sha_ROOM_window_notifications_enabled` — runtime toggle for window open/close notifications.
- `switch.sha_ROOM_override_notifications_enabled` — runtime toggle for override notifications.

### New services
- `sha.unregister_room` — remove a room from the persistent registry; helper entities disappear after the next reload.

### Fixed
- `target_temperature` floored at `4.0°C` across all blueprint branches (comfort, preheat, vacation frost, default) — prevents invalid TRV set-point values.
- `schedule_changed` added to the override skip list — schedule transitions no longer incorrectly stop an active override.
- Vacation mode now sends a `notify.notify` user notification before setting the `sha_vacation_notified` flag, so the notification is guaranteed to fire on activation.
- `window_timeout_notified` switch was missing from the entity platform — window-close resume notifications were never firing and window-open deduplication was broken.
- Log level on disable changed from `logging.WARNING` to `logging.NOTSET` — SHA `INFO`-level logs (room discovery, analysis results, etc.) are now visible when debug mode is off.
- `room_id` derivation aligned between blueprint Jinja2 template and `coordinator._room_name_to_id()` — entity IDs now always match between the automation and the integration.

### Blueprint
- Version bumped to **0.0.6** (triggers auto-update with backup on existing installs).
- `register_room` action removed from blueprint `action:` block — rooms are now fully managed by the config flow.
- Setup description updated to reflect the new Area-based wizard flow.

---

## v0.0.2 — 2026-04-06

### Audit: Full Compliance Review — All 34 checks passed ✅

This release closes all gaps identified across three structured audit rounds covering operational workflow logic, notification independence, and data context in every notification.

---

#### Part 1 · Operational Workflow Logic

| # | Requirement | Result | Change made |
|---|---|:---:|---|
| 1 | Daily AI analysis triggered exactly at **00:01** | ✅ | `DAILY_ANALYSIS_HOUR=0`, `DAILY_ANALYSIS_MINUTE=1` in `const.py`; README updated in 3 places |
| 2 | Dynamic pre-heat: start = `schedule_start − AI_duration` | ✅ | `mins_needed = max((target − room) / heating_rate, 5)` recalculated live every 5 min |
| 3 | Schedule temperature maintained during active period | ✅ | `schedule_changed` + `default:` branch push `target_mode`/`target_temperature` idempotently |
| 3b | TRVs revert to default when schedule ends | ✅ | `schedule_changed → off` branch re-applies TRV control via `target_mode` |
| 4a | Heating stops immediately when window opens | ✅ | `window_airing_start` sets `sha_airing_mode`; `target_mode` evaluates `windows_open → off` |
| 4b | Heating stays off while window remains open | ✅ | All timed triggers re-evaluate `windows_open` — state maintained every 5 min |
| 4c | Heating resumes **immediately** when window closes | ✅ | `window_airing_end` clears airing_mode then applies inline `resume_target_mode` without waiting for next tick |
| 4c-valid | Resume only if within valid pre-heat or schedule period | ✅ | `resume_target_mode` evaluates `in_comfort`, `in_preheat`, `vacation_active`, `default_hvac_mode` |
| 5a | Immediate TRV lock on manual change | ✅ | Trigger fires on `attribute: temperature` and `attribute: hvac_mode` state changes |
| 5b | Override auto-expires after configured duration | ✅ | `SHAOverrideSwitch.async_start()` → `async_call_later` → fires `sha_override_ended` event |
| 5c | Correct heating state restored on override expiry | ✅ | `override_ended` bypasses `override_active` stop; falls into `default:` which re-evaluates full state |

---

#### Part 2 · Notification System — Logic Independence & Event-Driven Behaviour

| # | Requirement | Result | Change made |
|---|---|:---:|---|
| N1 | Heating logic runs regardless of notification toggle | ✅ | TRV control always runs first; all notifications wrapped in `if notify_*_effective` |
| N2 | Per-type enable/disable toggle respected for every notification | ✅ | `notify_*_effective` checks both blueprint input and HA runtime switch |
| N3 | Daily/weekly report toggle respected | ✅ | `_async_notify_daily/weekly_room_result` early-returns if report disabled for room |
| N4 | Pre-heat notification fires **only when windows are closed** | ✅ | Added `not windows_open` to preheat notification guard |
| N5 | **New:** "Pre-heat Suspended" notification when window blocks pre-heat | ✅ | New `if` block: `in_preheat and windows_open`; includes open `{{ window_name }}`; new `tpl_preheat_suspended_body` input added |
| N6 | SHA's own TRV writes do NOT trigger manual override | ✅ | Value comparison: `incoming_temp != target_temperature OR incoming_mode != target_mode` |
| N7 | HA startup synthetic state event does NOT trigger override | ✅ | `trigger.from_state is not none` guard |
| N8 | Window-pause notification fires with room + entity context | ✅ | `window_airing_start` includes `{{ window_name }}` and `{{ room_temp }}` |
| N9 | Window-close/resume notification fires only after a real pause | ✅ | Guarded by `sha_window_timeout_notified == on` |
| N10 | Standby notification fires only when no other period is active | ✅ | Guard: `not in_comfort and not in_preheat and not vacation_active and not windows_open` |
| N11 | Override-ended notification fires exactly once per expiry | ✅ | Guarded by `trigger.id == 'override_ended'` in `default:` branch |
| N12 | No double-fire across branches | ✅ | All notifications deduped by `sha_*_notified` switches; reset on opposite event |

---

#### Part 3 · Data Context — Room, Entity, Live Temp vs Target in Notifications

| Notification | `room_name` | `room_temp` | `target_temp` | Entity |
|---|:---:|:---:|:---:|:---:|
| Pre-heat Started | ✅ | ✅ | ✅ `preheat_temp` | — |
| Pre-heat Suspended *(new)* | ✅ | ✅ | ✅ `preheat_temp` | ✅ `window_name` |
| Schedule Started | ✅ | ✅ | ✅ `comfort_temp` | — |
| Target Reached | ✅ | ✅ | ✅ `comfort_temp` | — |
| Schedule Finished / Standby | ✅ | ✅ | ✅ `default_temp` | — |
| Window Paused | ✅ | ✅ | — | ✅ `window_name` |
| Window Resumed | ✅ | ✅ | — | ✅ `window_name` |
| Override Active | ✅ | ✅ | — | ✅ `device_name` |
| Override Ended | ✅ | ✅ | ✅ `target_temperature` | — |
| Daily Report | ✅ | — | ✅ rates | ✅ sensor |
| Weekly Report | ✅ | — | ✅ rates | ✅ sensor |

---

#### Summary

| Category | Checks | ✅ Pass | ❌ Gap |
|---|:---:|:---:|:---:|
| Operational logic | 11 | 11 | 0 |
| Notification independence | 12 | 12 | 0 |
| Data context per notification | 11 | 11 | 0 |
| **Total** | **34** | **34** | **0** |

---

### Added
- `tpl_preheat_suspended_body` input in `schedule_section` — customisable message body for the new pre-heat suspended notification.
- "Pre-heat Suspended" notification fires when a pre-heat window is active but a window is open; includes open window entity name.

### Changed
- Pre-heat notification guard now includes `not windows_open` — prevents the "Pre-heat Started" notification firing while a window is open.
- `tpl_schedule_started_body` description updated: added `{{ target_temp }}` variable.
- `tpl_target_reached_body` description updated: added `{{ target_temp }}` variable.
- `tpl_override_ended_body` description updated: added `{{ target_temp }}` variable.
- All three message replace-chains updated to inject `{{ target_temp }}` at render time.

### Fixed
- Override not suppressing control-loop triggers correctly on first automation load (startup `from_state is None` guard).
- Spurious override triggered by SHA's own TRV writes (value-comparison guard).
- Vacation section not collapsing in UI (`default: ""` added to `vacation_calendar`; `vacation_calendar | length > 0` trigger guard).
- Daily analysis scheduled at 02:00 instead of 00:01 (`const.py` corrected).

## v0.0.2

### Added
- Per-room daily and weekly persistent analysis reports in Home Assistant.
- Per-room toggles for daily/weekly report notifications.
- Per-room runtime notification enable switches (preheat, target, standby, window, override).
- Externalized setup notification text templates via JSON (`messages.json`) with optional user overrides.
- Verbose debug logging controls and expanded diagnostics.

### Changed
- Moved room discovery to a persistent room registry model.
- Updated blueprint onboarding flow: create automation, run once to register room, then reload integration.
- Improved setup notification content with clearer step-by-step guidance.
- Improved switch labels and explanatory attributes for better UX.

### Fixed
- Fixed room discovery for section-based blueprint inputs.
- Fixed discovery reliability across startup/loading timing scenarios.
- Fixed debug logger scope for integration modules.
- Fixed override event filtering and number entity restore behavior.
- Applied Home Assistant developer compliance updates across manifest, config flow, sensors, services, and translations.
- Miscellaneous README and documentation corrections.
