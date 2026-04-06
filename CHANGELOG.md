# Changelog

All notable changes to this project will be documented in this file.

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
