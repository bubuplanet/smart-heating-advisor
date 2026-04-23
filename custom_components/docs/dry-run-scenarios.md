```markdown
# SHA Blueprint — Dry Run Scenarios

**Version:** 0.0.2
**Last updated:** 2026-04-11
**Last run against:** blueprint v0.0.X

---

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Verified — passes against current blueprint |
| ⚠️ | Needs recheck — blueprint changed since last run |
| ❌ | Failed — bug confirmed |
| 🆕 | Regression scenario added from production testing |

---

## How to use this file

Paste this into Claude VS Code extension to run all scenarios:

    Read docs/dry-run-scenarios.md and
    blueprints/smart_heating_advisor.yaml completely before starting.
    Run all scenarios against the current blueprint version.
    For each scenario trace the logic using the defined values.
    Show calculations step by step using actual numbers.
    Flag any result that differs from the expected outcome.
    Flag any scenario no longer valid due to blueprint changes.
    Produce a pass/fail report and list any issues found.

---

## Known edge cases not yet covered

- [ ] Two windows open — one closes before the other
- [ ] Pre-heat starts but heating_rate entity is unavailable
- [ ] Schedule fallback temp used when name has no C suffix
- [ ] Ollama returns valid JSON but heating_rate is out of range
- [ ] fixed_radiator_thermostats is empty — fixed TRV blocks must skip gracefully

---

## Room configuration — used in all scenarios unless stated otherwise

| Parameter | Value |
|---|---|
| room_name | Bathroom |
| room_id | bathroom |
| heating_rate | 0.15 °C/min (from number.sha_bathroom_heating_rate) |
| radiator_thermostats | climate.bathroom_radiator |
| fixed_radiator_thermostats | climate.bathroom_heated_towel_rail |
| fixed_radiator_temperature | 35°C |
| schedule | "Morning Shower 26C" — 06:00 to 07:00 |
| default_hvac_mode | off |
| comfort_temp | 16°C |
| override_minutes | 120 min |
| window_open_reaction_time | 5 min |
| vacation_enabled | false |
| notification flags | all off (fresh state) |
| notifications enabled switches | all on |

---

## Base scenarios

---

### Scenario 1 — Normal pre-heat and schedule ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 05:30 |
| Room temp | 20.5°C |
| Windows | closed |
| Override | inactive |
| Schedule | off — starts at 06:00 |

**05:30 — control loop**

1. What does active_schedule evaluate to?
2. What does preheat_schedule evaluate to? Show mins_needed and mins_to_start.
3. Does the 0.5°C delta guard pass? (26 - 20.5 = 5.5 > 0.5)
4. What is in_preheat_bool?
5. What action is sent to the main radiator TRV?
6. What action is sent to the fixed TRV — commanded to 35°C?
7. Which notifications fire?

**06:00 — schedule ON (schedule_changed/on)**

8. Main TRV → heat at 26°C. Fixed TRV → heat at 35°C.
9. Starting Comfort Phase notification fires?
10. sha_schedule_notified turns ON. Does sha_target_notified also turn ON? (Must not — B1 fix)

**06:15 — target reached**

11. target_reached evaluates to?
12. sha_target_notified is off — Target Reached notification fires?
13. Switch state changes?

**07:00 — schedule OFF**

14. Both sha_schedule_notified AND sha_target_notified reset?
15. Main TRV → off. Fixed TRV → off.
16. Standby notification fires?

---

### Scenario 2 — Window closed before reaction time ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 05:45 |
| Room temp | 23.0°C |
| preheat_notified | on |
| Window | opens 05:46, closes 05:49 (3 min — before reaction time) |

1. Does window_airing_start ever fire? Why or why not?
2. Does airing_mode switch turn on?
3. Is any notification sent?
4. Do both TRVs continue heating normally?

---

### Scenario 3 — Window open past reaction time ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 05:46 |
| Room temp | 23.0°C |
| preheat_notified | on |
| Window | opens 05:46, stays open |

**05:51 — window_airing_start fires**

1. What explicit TRV commands fire? Show both main and fixed TRV.
2. Are these explicit commands or does the sequence rely on target_mode variable?
3. window_timeout_notified turned ON as independent action outside notification block?
4. Window open notification sent?

**05:55 — control loop (window still open)**

5. target_mode with windows_open = true?
6. Notification gate: window = SEND or skip?

**06:10 — window closes**

7. All-windows-closed check result?
8. airing_mode turns off?
9. Window closed notification — gate condition? Is window_timeout_notified_on true?
10. Both TRVs resume — main at what temperature, fixed at 35°C?

---

### Scenario 4 — Manual override during active schedule ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 06:20 |
| Room temp | 25.5°C |
| Schedule | active |
| target_reached | false (0.5°C below target) |
| Override | inactive |

**06:20 — user changes TRV**

1. Guard conditions: windows_open, override_active, context.parent_id, value differs from target?
2. sha.start_override called?
3. Override notification sent?

**06:25 — control loop**

4. Override active — control loop skipped?

**07:00 — schedule ends**

5. schedule_changed in override skip list?
6. Notification flags affected?

**08:20 — override expires**

7. sha_override_ended event fires — which trigger matches?
8. target_mode at 08:20 (no active schedule)?
9. TRV action and notification?

---

### Scenario 5 — Two consecutive back-to-back schedules ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 06:59 |
| Room temp | 25.8°C |
| Schedule 1 | Morning Shower 26C — ends 07:00 |
| Schedule 2 | Morning Routine 22C — starts 07:00 |
| sha_schedule_notified | on |
| sha_target_notified | on |

**07:00 — both triggers fire simultaneously**

1. OFF trigger: standby notification blocked? in_comfort_bool at 07:00?
2. Are both sha_schedule_notified and sha_target_notified reset?
3. ON trigger: comfort_temp = 22°C. Main TRV → 22°C. Fixed TRV → 35°C.
4. Starting Comfort Phase fires — sha_schedule_notified was just reset so it is off?
5. No spurious standby notification between schedules?

---

### Scenario 6 — Heating rate edge case with 0.5°C delta guard ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 05:55 |
| Room temp | 25.8°C |
| Target | 26°C |
| Delta | 0.2°C |

1. temp_delta = 26 - 25.8 = 0.2. Does 0.2 > 0.5? Pre-heat blocked?
2. Is this correct behaviour?
3. Repeat with room at 25.0°C: delta = 1.0 > 0.5 → passes. mins_needed = (1.0 / 0.15) | int, apply max(..., 5)? Does pre-heat trigger at 05:55 (5 min to start)?

---

### Scenario 7 — Pre-heat suspended by window, closes after schedule starts ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 05:55 |
| Room temp | 20.0°C |
| Window | open (past reaction time — airing_mode already on) |

1. in_preheat_bool with window open?
2. Pre-heat suspended notification — switch states?
3. 06:00 schedule ON — is sha_preheat_notified reset?
4. 06:05 window closes — closed notification fires?
5. Both TRVs resume at correct temperatures?

---

### Scenario 8 — Room already at target when schedule starts (B1 fix verification) ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 06:00 (schedule_changed/on) |
| Room temp | 25.8°C (above 25.7 threshold) |
| sha_schedule_notified | off |
| sha_target_notified | off |

1. Starting Comfort Phase fires — sha_schedule_notified ON, sha_target_notified unchanged?
2. Main TRV → 26°C. Fixed TRV → 35°C.
3. 06:05 control loop: target_reached = true, sha_target_notified still off → fires?
4. Both notifications independent in same schedule period — B1 fix confirmed?
5. slot_end_time uses `.strftime('%H:%M')` method — NOT `| strftime` filter?

---

### Scenario 9 — Two windows open, one closes first ⚠️

**Context**

| Parameter | Value |
|---|---|
| sensor_A | open |
| sensor_B | open |
| airing_mode | on |
| window_timeout_notified | on |

**sensor_B closes**

1. All-closed check: sensor_A still open → count > 0?
2. airing_mode stays on, no notification, no TRV resume?

**sensor_A closes**

3. All-closed check passes?
4. airing_mode off, closed notification, both TRVs resume?

---

### Scenario 10 — heating_rate entity unavailable ⚠️

**Context**

| Parameter | Value |
|---|---|
| sha_bathroom_heating_rate | state = unavailable |
| Fallback | 0.15 °C/min |

1. `states(sha_heating_rate) | float(0.15)` → fallback value?
2. mins_needed for 5.5°C gap at 0.15: (5.5 / 0.15) | int, apply max(..., 5)?
3. Pre-heat triggers normally with fallback?

---

### Scenario 11 — Schedule fallback temp when target_temp not set ⚠️

Note: target temps are now configured per-schedule in the room wizard.
This scenario tests the fallback path in the blueprint for schedules
that have no temperature in their name AND no wizard-configured temp —
e.g. schedules migrated from old installs without re-saving.

**Context**

| Parameter | Value |
|---|---|
| Schedule name | "Morning Routine" (no temperature suffix) |
| schedule_fallback_temp | 21°C |

1. regex_findall returns nothing — target_temp = fallback = 21°C?
2. delta guard: 21 - 20.0 = 1.0 > 0.5 → passes?
3. mins_needed = (1.0 / 0.15) | int, apply max(..., 5)?
4. Pre-heat triggers at 05:30 (30 min to start)?

---

### Scenario 12 — Override active when window opens ⚠️

**Context**

| Parameter | Value |
|---|---|
| Override | active |
| Schedule | active |
| Window | opens at 06:20 |

1. window_airing_start fires — explicit TRV off for both main and fixed?
2. control_loop: override_active and windows_open both true — which wins?
3. TRV off command re-triggers manual_override? Show context.parent_id check.
4. State: airing_mode = on, override = on — what controls the TRV?

---

### Scenario 13 — Vacation mode activates mid-schedule ⚠️

> ⚠️ **Partially stale** — SHA vacation is now configured via a subentry
> (date range or manual toggle), not via a calendar entity. The blueprint
> still reads `vacation_active_bool` from the binary_sensor.sha_vacation
> entity which SHA creates. Replace the calendar event trigger below with:
> SHA subentry vacation enabled = true → binary_sensor.sha_vacation = on.

**Context**

| Parameter | Value |
|---|---|
| Schedule | active (comfort phase) |
| binary_sensor.sha_vacation | off → turns on at 06:30 |
| vacation_mode | off (no heating) |

1. vacation_active_bool reads binary_sensor.sha_vacation — evaluates to true?
2. target_mode: vacation_active + mode = off → off?
3. Both TRVs commanded off?
4. vacation_notified fires once only — subsequent loops skip?

---

### Scenario 14 — Override expires while window is open ⚠️

**Context**

| Parameter | Value |
|---|---|
| Override | expires at 08:20 |
| airing_mode | on (window still open) |
| Schedule | none |

1. sha_override_ended event → override_ended trigger matches?
2. target_mode: windows_open = true → off?
3. Both TRVs stay off (window still open)?
4. Override ended notification fires?
5. Window notification NOT re-fired — window_timeout_notified_on still on?

---

### Scenario 15 — Room already overheated when schedule starts (B1 fix verification) ⚠️

**Context**

| Parameter | Value |
|---|---|
| Time | 06:00 (schedule_changed/on) |
| Room temp | 27.5°C (above 26°C target) |
| sha_schedule_notified | off |
| sha_target_notified | off |

1. Starting Comfort Phase fires — sha_schedule_notified ON?
2. Main TRV → heat at 26°C. Fixed TRV → heat at 35°C.
3. 06:05: target_reached = true, sha_target_notified off → fires?
4. Both notifications in same period — B1 fix confirmed?
5. TRV commanded to heat when room already above target — correct behaviour?

---

## Regression scenarios — added from production testing 2026-04-11

---

### Scenario 16 — slot_end_time uses .strftime() method not filter 🆕

**Context**

| Parameter | Value |
|---|---|
| Trigger | schedule_changed/on |
| next_event | 2026-04-11T07:00:00+01:00 |

**Root cause confirmed in production:** `| strftime` filter raises `TemplateRuntimeError: No filter named 'strftime' found` and aborts the entire schedule_changed ON branch.

1. slot_end_time template uses `(as_datetime(nev) | as_local).strftime('%H:%M')` — NOT `| strftime`?
2. slot_end_time evaluates to `07:00`?
3. Included in Starting Comfort Phase notification body?

---

### Scenario 17 — fixed_radiator_temperature variable shadowing 🆕

**Context**

| Parameter | Value |
|---|---|
| Blueprint input | fixed_radiator_temperature = 35 |
| Variables section | check for redefinition |

**Root cause confirmed in production:** Variable in variables section read from `input_number.sha_bathroom_fixed_radiator_temperature` which returned `unknown` → `| float(0)` → towel rail commanded to 0°C.

1. Variables section does NOT redefine fixed_radiator_temperature?
2. If defined — reads from input_number.sha_* → returns unknown → 0?
3. After fix: climate action uses `| float(30)` fallback?
4. Towel rail correctly commanded to 35°C in pre-heat and comfort phases?

---

### Scenario 18 — Pre-heat notification fires every 5 minutes 🆕

**Context**

| Parameter | Value |
|---|---|
| sha_preheat_notified | off |
| Pre-heat | active |

**Root cause confirmed in production:** `switch.turn_on sha_preheat_notified` nested inside notification `if/then` block — never fired when notification was suppressed.

1. turn_on sha_preheat_notified is independent block OUTSIDE notification if/then?
2. After notification fires at 05:20 — sha_preheat_notified = on?
3. 05:25 control loop — already_sent = on → preheat = skip?

---

### Scenario 19 — window and override gates always showing SEND 🆕

**Context**

| Parameter | Value |
|---|---|
| Windows | closed |
| Override | inactive |
| Phase | comfort active |

**Root cause confirmed in production:** Gate conditions only checked `notify_window_effective` (always true) rather than also requiring `windows_open_bool`.

**Expected notification gates during normal comfort phase**

    preheat  = skip
    target   = skip or SEND (depends on target_reached)
    standby  = skip
    window   = skip    ← must be skip when no window open
    override = skip    ← must be skip when no override active

1. window gate: requires `notify_window_effective AND windows_open_bool AND NOT window_timeout_notified_on`?
2. override gate: requires `notify_override_effective AND override_active_bool AND NOT override_notified_on`?
3. During normal comfort phase both show skip?

---

### Scenario 20 — Fixed TRV not commanded in comfort phase 🆕

**Context**

| Parameter | Value |
|---|---|
| Phase | comfort (in_comfort_bool = true) |
| fixed_radiator_thermostats | climate.bathroom_heated_towel_rail |
| fixed_radiator_temperature | 35°C |

**Root cause confirmed in production:** Climate action block for fixed TRVs missing from comfort branch.

1. Comfort branch has explicit fixed TRV block with count > 0 guard?
2. system_log.write confirms block executing?
3. climate.set_hvac_mode heat + climate.set_temperature 35°C?
4. Log entry: `SHA [Bathroom] Commanding fixed TRV(s) → heat at 35.0°C`?

---

### Scenario 21 — Spurious Heating Ended on schedule edit 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 16:39 (outside any heating window) |
| in_comfort_bool | false |
| in_preheat_bool | false |
| Trigger | User edits schedule helper and saves — HA fires schedule_changed/off |

**Root cause confirmed in production:** schedule_ended notification fired when schedule helper was saved outside a heating window.

1. schedule_changed/off fires at 16:39. in_comfort_bool = false, in_preheat_bool = false.
2. Before fix: does the notification guard check in_comfort_bool or in_preheat_bool? Notification fires?
3. After fix: guard requires `(in_comfort_bool or in_preheat_bool)` — does it fire now? Why not?
4. Is the SCHEDULE ENDED system_log.write also suppressed by the same guard?

**Expected result after fix**

- No Heating Ended notification sent
- No SCHEDULE ENDED log entry
- TRV commanded to standby as normal with no user-visible disruption

---

### Scenario 22 — Heating Ended fires correctly during real schedule end 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 07:00 (schedule end) |
| in_comfort_bool | true (room was actively heating) |
| in_preheat_bool | false |
| Trigger | Saturday Morning Shower 26C turns off at 07:00 |

**Verify fix does not suppress real notifications.**

1. schedule_changed/off fires at 07:00. in_comfort_bool = true.
2. After fix: guard `(in_comfort_bool or in_preheat_bool)` passes?
3. Heating Ended notification fires correctly?
4. SCHEDULE ENDED log entry present?
5. TRV commanded off?
6. sha_schedule_notified and sha_target_notified both reset?

**Expected result after fix**

- Heating Ended notification sent ✅
- SCHEDULE ENDED log entry present ✅
- Both dedup switches reset ✅

---

### Scenario 23 — Heating Ended suppressed during pre-heat to comfort transition 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 06:00 (schedule turns on — pre-heat was active) |
| in_preheat_bool | true at moment schedule turns on |
| Trigger | schedule_changed/on fires for the same schedule being pre-heated |

1. At 06:00 does schedule_changed/off also fire? Or only schedule_changed/on?
2. If OFF fires: in_comfort_bool = false, in_preheat_bool = true. After fix: guard passes?
3. Should Heating Ended fire during a pre-heat → comfort transition?

**Expected result**

- No Heating Ended notification during pre-heat → comfort transition ✅

---

### Scenario 24 — Control loop debug shows all schedule details 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 17:05 (outside any heating window) |
| Schedule configured | schedule.saturday_morning_shower_26c |
| Schedule state | off |
| next_event | 2026-04-11T17:30:00+01:00 |

1. Control loop fires at 17:05. Does the debug log show the schedule name?
2. Does it show the schedule state (OFF) and next_event time?
3. Does it show how many schedules are configured?
4. Expected log entry:

       Schedules configured: 1
         ► schedule.saturday_morning_shower_26c: OFF (next: 17:30)
       Active now: none
       Pre-heat: no

5. If two schedules are configured do both appear in the log?

---

### Scenario 25 — Pre-heat countdown INFO log 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 17:05 (25 min before schedule at 17:30) |
| Room temp | 23.6°C |
| Target | 26°C |
| heating_rate | 0.15 °C/min |
| in_preheat_bool | false |
| in_comfort_bool | false |

1. mins_to_start = 25. Is 25 <= 60? Countdown log fires?
2. delta = 26 - 23.6 = 2.4°C. mins_needed = (2.4 / 0.15) | int, apply max(..., 5)?
3. Pre-heat start time = 17:30 - mins_needed = ?
4. Expected log entry at INFO level:

       SHA [Bathroom] Pre-heat countdown:
       Schedule "Saturday Morning Shower 26C" starts in 25 min (target 26°C at 17:30).
       Room now: 23.6°C — gap 2.4°C.
       At 0.15°C/min needs 16 min to heat.
       Pre-heat will start at: 17:14.

5. At 17:10 (20 min to start) countdown fires again with updated values?
6. At 17:14 in_preheat_bool = true → countdown condition false → stops firing?

---

### Scenario 26 — Pre-heat countdown suppressed in wrong states 🆕

**Context A — comfort phase active**

| Parameter | Value |
|---|---|
| in_comfort_bool | true |
| in_preheat_bool | false |

1. Countdown log fires? (Must not — already in comfort phase)

**Context B — pre-heat already active**

| Parameter | Value |
|---|---|
| in_comfort_bool | false |
| in_preheat_bool | true |

2. Countdown log fires? (Must not — already pre-heating)

**Context C — no schedule within 60 minutes**

| Parameter | Value |
|---|---|
| mins_to_start | 90 min |

3. Countdown log fires? (Must not — too far away)

**Context D — no schedules configured**

| Parameter | Value |
|---|---|
| schedules | all empty |

4. Countdown log fires? (Must not — nothing to count down to)

---

### Scenario 27 — Redundant pre-heat suspended notification removed 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 05:50 (pre-heat active) |
| in_preheat_bool | true |
| Window | opens at 05:51 (past reaction time) |

1. window_airing_start fires — TRVs commanded off.
2. Window Open — Heating Suspended notification sent ✅
3. Is a second pre-heat suspended notification also sent? (Must NOT be — removed)
4. User receives exactly ONE notification for this event.
5. preheat_notified switch state — not affected by window open sequence?

**Expected result after fix**

- Only one notification: Window Open — Heating Suspended ✅
- No pre-heat suspended duplicate ✅
- preheat_notified not turned on during window open ✅

---

### Scenario 28 — Window closed notification fires in comfort phase 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 17:20 (comfort phase active) |
| in_comfort_bool | true |
| Schedule | Saturday Morning Shower 26C |
| window_timeout_notified | on |
| Window | closes at 17:20 |

**Root cause confirmed in production:** WINDOW CLOSED log appeared but no notification was sent.

1. window_airing_end fires. All windows closed check passes.
2. airing_mode turns off.
3. TRVs resume — main → heat at 26°C, fixed → heat at 35°C.
4. WINDOW CLOSED — HEATING RESUMED log entry appears ✅
5. window_timeout_notified_on = true (set unconditionally in window_airing_start)?
6. notify_window_effective = true? Notification condition passes?
7. Expected notification:

       🪟 Bathroom — Heating Resumed
       All windows closed in Bathroom. Heating resumed.
       Schedule "Saturday Morning Shower 26C" active — target 26°C.
       Current: 23.6°C.

8. window_timeout_notified turned OFF after notification?

**Expected result**

- Notification sent immediately when window closes ✅
- Message includes schedule name and target temperature ✅
- window_timeout_notified reset to off ✅

---

### Scenario 29 — Window closed notification fires in pre-heat phase 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 05:52 (pre-heat active) |
| in_preheat_bool | true |
| in_comfort_bool | false |
| window_timeout_notified | on |
| Window | closes at 05:52 |

1. window_airing_end fires. Resume mode = pre-heat.
2. TRVs resume — main at preheat_temp, fixed at 35°C.
3. Notification fires with pre-heat context:

       🪟 Bathroom — Heating Resumed
       All windows closed in Bathroom. Heating resumed.
       Pre-heating for "Morning Shower 26C" — target 26°C.
       Current: 20.5°C.

---

### Scenario 30 — Window closed notification fires in standby phase 🆕

**Context**

| Parameter | Value |
|---|---|
| Time | 10:00 (no schedule active) |
| in_preheat_bool | false |
| in_comfort_bool | false |
| window_timeout_notified | on |
| Window | closes at 10:00 |

1. window_airing_end fires. Resume mode = standby.
2. Notification fires with standby context:

       🪟 Bathroom — Heating Resumed
       All windows closed in Bathroom. Heating resumed.
       Returning to standby at 16°C.
       Current: 19.0°C.

---

### Scenario 31 — Window timeout notified set unconditionally 🆕

**Context**

| Parameter | Value |
|---|---|
| Trigger | window_airing_start fires |
| notify_window_effective | false (window notifications disabled by user) |

**Root cause of scenarios 28–30:** window_timeout_notified was inside the notification block — never turned on if notification was suppressed.

1. Notification block skipped — notify_window_effective = false.
2. Before fix: window_timeout_notified never turned on → closed notification permanently blocked.
3. After fix: turn_on window_timeout_notified fires as independent block outside notification.
4. switch.sha_bathroom_window_timeout_notified = on even when notifications disabled ✅
5. When window closes — window_timeout_notified_on = true → closed notification fires (if notify_window_effective = true at close time) ✅

---

## Results table

Fill in after each dry run:

| # | Scenario | Variables | Actions | Notifications | Status |
|---|---|---|---|---|---|
| 1 | Normal pre-heat and schedule | | | | |
| 2 | Window closed before reaction time | | | | |
| 3 | Window open past reaction time | | | | |
| 4 | Manual override during active schedule | | | | |
| 5 | Two consecutive back-to-back schedules | | | | |
| 6 | Heating rate edge case | | | | |
| 7 | Pre-heat suspended by window | | | | |
| 8 | Room at target when schedule starts | | | | |
| 9 | Two windows open one closes first | | | | |
| 10 | heating_rate entity unavailable | | | | |
| 11 | Schedule name with no C suffix | | | | |
| 12 | Override active when window opens | | | | |
| 13 | Vacation mode activates mid-schedule | | | | |
| 14 | Override expires while window open | | | | |
| 15 | Room overheated when schedule starts | | | | |
| 16 | slot_end_time strftime method | | | | |
| 17 | fixed_radiator_temperature shadowing | | | | |
| 18 | Pre-heat notification every 5 minutes | | | | |
| 19 | window and override gates always SEND | | | | |
| 20 | Fixed TRV not commanded in comfort | | | | |
| 21 | Spurious Heating Ended on schedule edit | | | | |
| 22 | Heating Ended fires on real schedule end | | | | |
| 23 | Heating Ended suppressed during pre-heat end | | | | |
| 24 | Control loop debug shows schedule details | | | | |
| 25 | Pre-heat countdown INFO log | | | | |
| 26 | Pre-heat countdown suppressed correctly | | | | |
| 27 | Redundant pre-heat suspended notification removed | | | | |
| 28 | Window closed notification fires in comfort phase | | | | |
| 29 | Window closed notification fires in pre-heat phase | | | | |
| 30 | Window closed notification fires in standby phase | | | | |
| 31 | Window timeout notified set unconditionally | | | | |
```