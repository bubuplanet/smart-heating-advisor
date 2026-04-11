```markdown
# SHA Blueprint — Dry Run Scenarios

Version: 0.0.2
Last updated: 2026-04-11
Last run against: blueprint v0.0.X
Status: ✅ = verified ⚠️ = needs recheck ❌ = failed

---

## How to use this file

Paste this invocation prompt into Claude VS Code extension:

  Read docs/dry-run-scenarios.md and
  blueprints/smart_heating_advisor.yaml carefully.
  Run all scenarios against the current blueprint version.
  For each scenario trace the logic using the defined values.
  Flag any result that differs from the expected outcome.
  Flag any scenario no longer valid due to blueprint changes.
  Suggest new scenarios for any new features added since last run.
  Produce a pass/fail report and list any issues found.

---

## Known edge cases not yet covered

- [ ] Two windows open — one closes before the other
- [ ] Pre-heat starts but heating_rate entity is unavailable
- [ ] Schedule fallback temp used when name has no C suffix
- [ ] Ollama returns valid JSON but heating_rate is out of range
- [ ] fixed_radiator_thermostats is empty — all fixed TRV blocks
      must be skipped gracefully with no error

---

## Room configuration for all scenarios

- room_name: Bathroom
- room_id: bathroom
- temperature_sensor: sensor.bathroom_thermostat_temperature
- heating_rate: 0.15 (from number.sha_bathroom_heating_rate)
- radiator_thermostats: climate.bathroom_radiator
- fixed_radiator_thermostats: climate.bathroom_heated_towel_rail
- fixed_radiator_temperature: 35
- schedule: "Morning Shower 26C" — active 06:00 to 07:00
- default_hvac_mode: off
- default_temp: 16
- override_minutes: 120
- window_open_reaction_time: 5 minutes
- vacation_enabled: false
- All notification flags: off (fresh state)
- All notifications enabled switches: on

---

## Scenario 1 — Normal pre-heat and schedule

Current time: 05:30
Room temperature: 20.5°C
Windows: closed
Override: inactive
Schedule "Morning Shower 26C": off (starts at 06:00)

Trace:
1. What does active_schedule evaluate to?
2. What does preheat_schedule evaluate to?
   Show the full mins_needed and mins_to_start calculation.
   Apply the 0.5°C delta guard — does pre-heat trigger?
3. What is in_preheat_bool?
4. What is target_temperature?
5. What is target_mode?
6. What action is sent to the main radiator TRV?
7. What action is sent to the fixed TRV (towel rail)?
   What temperature is it commanded to?
8. Which notifications fire and why?
9. What switch states change?

Then advance time to 06:00 (schedule turns on):
10. What trigger fires?
11. What does active_schedule evaluate to now?
12. What action is sent to main TRV?
13. What action is sent to fixed TRV?
14. Which notifications fire?
15. sha_schedule_notified turns on — does sha_target_notified
    also turn on? Why or why not? (B1 fix verification)

Then advance time to 06:15 (room reaches 26°C):
16. What does target_reached evaluate to?
17. Is sha_target_notified off at this point? (should be — B1 fix)
18. Which notification fires?
19. What switch state changes?

Then advance time to 07:00 (schedule ends):
20. What trigger fires?
21. What action is sent to main TRV?
22. What action is sent to fixed TRV?
23. Which notification fires?
24. What switch states reset?
    Confirm both sha_schedule_notified and sha_target_notified
    reset — not just one.

---

## Scenario 2 — Window opened and closed BEFORE reaction time

Current time: 05:45
Room temperature: 23.0°C (pre-heat already started)
Pre-heat active: yes
preheat_notified: on
Windows: all closed
Override: inactive

Step A — window opens at 05:46:
1. Which trigger fires?
2. Does window_airing_start fire immediately or after delay?
3. What happens during the delay — does the control loop still run?
4. Window closes at 05:49 (3 minutes later — before 5 min reaction time)
5. Does window_airing_start ever fire? Why or why not?
6. Does window_airing_end fire?
7. Does airing_mode switch turn on? Why or why not?
8. Is any notification sent? Why or why not?
9. Does heating continue normally for both main and fixed TRVs?

---

## Scenario 3 — Window opened and left open past reaction time

Current time: 05:45
Room temperature: 23.0°C
Pre-heat active: yes
preheat_notified: on
Windows: all closed
Override: inactive

Step A — window opens at 05:46 and stays open:
1. window_airing_start fires after 5 minutes at 05:51
2. What is airing_mode state after trigger?
3. What is windows_open variable?
4. What explicit TRV commands fire in window_airing_start?
   Show both main radiator and fixed TRV commands.
   (These must be explicit — not relying on target_mode variable)
5. Is window_timeout_notified_on true or false before notification?
6. Is notification sent? Which one?
7. Is window_timeout_notified turned on as an INDEPENDENT action
   outside the notification block? (Bug 2 fix verification)
8. What switch states change?

Step B — control loop fires at 05:55 (window still open):
9. What does target_mode evaluate to?
10. What action is sent to TRVs?
11. Is another notification sent? Why or why not?
    Check: window=SEND or window=skip in notification gates?

Step C — window closes at 06:10:
12. Which trigger fires?
13. Are all windows closed — how is this checked?
14. What is airing_mode state after?
15. Is window closed notification sent?
    What condition gates it? Is window_timeout_notified_on true?
16. What switch states change?
17. Heating resumes — what mode and temperature for both TRVs?
    Main TRV: what temperature?
    Fixed TRV: what temperature?

---

## Scenario 4 — Manual override during active schedule

Current time: 06:20
Room temperature: 25.5°C
Schedule "Morning Shower 26C": active
target_reached: false (0.5°C below target)
Override: inactive
Windows: closed

Step A — user manually sets TRV to 22°C at 06:20:
1. Which trigger fires?
2. What is trigger.to_state.context.parent_id?
3. What is the current target_temperature?
4. Does the guard condition pass? Show each check:
   - not windows_open_bool
   - not override_active_bool
   - is_state(sha_override_switch, 'off')
   - context.parent_id is none
   - new value differs from target
5. Is sha.start_override called?
6. What is override switch state after?
7. Is notification sent?

Step B — control loop fires at 06:25 (override active):
8. Which choose condition matches first?
9. Does control loop stop? Why?
10. Is TRV commanded? Why or why not?

Step C — schedule changes at 07:00 (turns off):
11. Which trigger fires?
12. Is schedule_changed in the override skip list?
13. Does the override block stop execution?
14. What happens to standby/target notification flags?

Step D — override expires at 08:20:
15. What event fires?
16. Which trigger in the blueprint matches?
17. What is target_mode at 08:20 (no schedule active)?
18. What action is sent to TRVs?
19. Which notification fires?

---

## Scenario 5 — Two consecutive schedules back to back

Schedules:
- "Morning Shower 26C": 06:00 to 07:00
- "Morning Routine 22C": 07:00 to 08:00

Current time: 06:59
Room temperature: 25.8°C
sha_schedule_notified: on
sha_target_notified: on
standby_notified: off

At 07:00 both schedule_changed triggers fire simultaneously:
- "Morning Shower 26C" turns off
- "Morning Routine 22C" turns on

1. For the OFF trigger on "Morning Shower 26C":
   What is in_comfort_bool at this moment?
   What is in_preheat_bool?
   Does the standby notification condition pass?
   Why or why not?
   Are sha_schedule_notified and sha_target_notified both reset?

2. For the ON trigger on "Morning Routine 22C":
   What does active_schedule evaluate to?
   What is comfort_temp?
   What action is sent to main TRV?
   What action is sent to fixed TRV?
   What notification fires?
   Is sha_schedule_notified now off (just reset) allowing the
   Starting Comfort Phase notification to fire?

3. Is a spurious standby notification sent between the two
   schedules? Show exactly which condition prevents or allows it.

4. What is the final main TRV temperature after both triggers?
5. What is the final fixed TRV temperature after both triggers?

---

## Scenario 6 — Heating rate edge case with 0.5°C delta guard

Current time: 05:55
Room temperature: 25.8°C (almost at target already)
Schedule "Morning Shower 26C": starts at 06:00
heating_rate: 0.15

1. Calculate temp_delta: 26 - 25.8 = 0.3°C
2. Does the 0.5°C delta guard block pre-heat?
   (temp_delta > 0.5 → 0.3 > 0.5 → false)
3. Does pre-heat trigger? Why not?
4. Is this the correct behaviour? Explain why.

5. Now same scenario with room at 25.0°C:
   temp_delta = 26 - 25.0 = 1.0°C
   Does 1.0 > 0.5 pass? Yes.
   Calculate mins_needed: (1.0 / 0.15) | int = ?
   Apply max(..., 5) = ?
   mins_to_start = 5 min
   Does pre-heat trigger?

---

## Scenario 7 — Pre-heat suspended then window closes after schedule starts

Current time: 05:55
Room temperature: 20.0°C
Schedule "Morning Shower 26C": starts at 06:00
preheat_notified: off
Window: opens at 05:55 (already past reaction time — airing_mode already on)

1. At 05:55 control_loop fires. Is in_preheat_bool true?
   Apply delta guard: 26 - 20.0 = 6.0 > 0.5 → yes.
   Show mins_needed and mins_to_start calculation.
2. windows_open_bool is true — is pre-heat suspended notification sent?
   Which switch states change?
3. At 06:00 schedule turns on — schedule_changed/on fires.
   What is preheat_notified state at this point?
   Is sha_preheat_notified reset? By which branch?
4. Window closes at 06:05 — window_airing_end fires.
   Is the window closed notification sent? What condition allows it?
5. Heating resumes — what mode and temperature for both TRVs?

---

## Scenario 8 — Room already at target when schedule starts

Current time: 06:00 (schedule_changed/on)
Room temperature: 25.8°C (above comfort_temp - 0.3 = 25.7)
Schedule "Morning Shower 26C": just turned on
sha_schedule_notified: off
sha_target_notified: off

1. schedule_changed/on fires — Starting Comfort Phase notification.
   Does it fire? Which switch turns on?
   Does sha_target_notified turn on here? (Must not — B1 fix)
2. Main TRV commanded to heat at 26°C.
   Fixed TRV commanded to heat at 35°C.
3. Control loop fires at 06:05.
   target_reached = in_comfort_bool AND 25.8 >= 25.7 → true.
   sha_target_notified is off (independent from sha_schedule_notified).
   Target Reached notification fires. sha_target_notified turns on.
4. Are both notifications sent in the same schedule period?
   Confirm B1 fix is effective.
5. slot_end_time available in schedule_changed/on branch?
   Show the .strftime('%H:%M') method call — NOT | strftime filter.

---

## Scenario 9 — Two windows open — one closes, one stays open

Current time: 06:10
Room temperature: 22.0°C
airing_mode: on (both windows triggered reaction time earlier)
window_timeout_notified: on
Two window sensors: sensor_A (open), sensor_B (open)

Step A — sensor_B closes at 06:10:
1. window_airing_end fires. What is the all-closed check result?
   expand(window_sensors) with sensor_A still open → count still > 0
2. Does airing_mode turn off?
3. Is window closed notification sent?
4. Does heating resume for either TRV?

Step B — sensor_A closes at 06:15:
5. window_airing_end fires again. What is the all-closed check now?
6. Does airing_mode turn off?
7. Is window closed notification sent now? Why?
8. Does heating resume for both main and fixed TRVs?

---

## Scenario 10 — heating_rate entity unavailable

Current time: 05:30
Room temperature: 20.5°C
sha_bathroom_heating_rate: state = 'unavailable'
Schedule "Morning Shower 26C": starts at 06:00

1. What does heating_rate variable evaluate to?
   Show: states(sha_heating_rate) | float(0.15)
2. What is the fallback value used?
3. Calculate mins_needed with fallback rate 0.15:
   temp_delta = 26 - 20.5 = 5.5 > 0.5 → guard passes.
   (5.5 / 0.15) | int = ?
   Apply max(..., 5) = ?
4. Does pre-heat trigger?
5. Is this correct behaviour?

---

## Scenario 11 — Schedule name with no C suffix (fallback temp)

Current time: 05:30
Room temperature: 20.0°C
Schedule name: "Morning Routine" (no temperature suffix)
schedule_fallback_temp: 21
Next event: 06:00

1. What does the regex_findall return for "Morning Routine"?
2. What is target_temp?
3. temp_delta = 21 - 20.0 = 1.0 > 0.5 → guard passes.
   Calculate mins_needed: (1.0 / 0.15) | int = ?
   Apply max(..., 5) = ?
4. Does pre-heat trigger at 05:30 (30 min to start)?
5. Is this correct?

---

## Scenario 12 — Override active when window opens

Current time: 06:20
Override: active (sha_override_switch = on)
Schedule "Morning Shower 26C": active
Window: opens — reaction time passes at 06:25

1. window_airing_start fires at 06:25.
   What sequence runs?
2. airing_mode turns on.
   Explicit TRV off commands fire — show both main and fixed TRV.
3. Control loop fires at 06:30.
   override_active_bool = true, trigger.id = control_loop →
   Does the override skip condition match?
4. Is the manual_override trigger ever fired by the TRV off command?
   Show: trigger.to_state.context.parent_id check.
5. State summary: airing_mode=on, override=on. What controls the TRV?

---

## Scenario 13 — Vacation mode becomes active mid-schedule

Current time: 06:30
Schedule "Morning Shower 26C": active, in_comfort_bool = true
vacation_enabled: true
vacation_calendar: calendar.home
vacation_mode: off
Calendar event: starts at 06:30, title "vacation week"

1. Control loop at 06:30 — vacation_active_bool evaluates to?
   Show the regex_search check.
2. What is target_mode?
   windows_open=false, vacation_active=true, vacation_mode='off' → ?
3. TRV action for main and fixed TRVs?
4. Is vacation_notified_on false? Is notification sent?
5. What switch states change?
6. Control loop at 06:35 — vacation still active.
   Is another vacation notification sent? Why or why not?

---

## Scenario 14 — Override expires while window is open

Current time: 08:20
Override timer expires → sha_override_ended event fires
airing_mode: on (window still open)
No schedule active

1. trigger.id = override_ended. Which outer choose condition matches?
2. What is target_mode?
   windows_open_bool = true → ?
3. TRV action for main and fixed TRVs?
4. Override ended notification — does it fire?
5. Does the window open notification re-fire? Why or why not?
   Check: window_timeout_notified_on — is it still on?

---

## Scenario 15 — Room already overheated when schedule starts

Current time: 06:00 (schedule_changed/on)
Room temperature: 27.5°C (above comfort_temp = 26.0)
Schedule "Morning Shower 26C": just turned on
sha_schedule_notified: off
sha_target_notified: off

1. schedule_changed/on: Starting Comfort Phase fires.
   sha_schedule_notified → on. sha_target_notified unchanged.
2. Main TRV commanded to heat at 26°C.
   Fixed TRV commanded to heat at 35°C.
3. Control loop at 06:05:
   target_reached = in_comfort_bool AND 27.5 >= 25.7 → true.
   sha_target_notified is off → Target Reached fires.
4. Are both notifications sent in the same schedule period?
   Confirm B1 fix is effective.
5. Is commanding the TRV to heat when room is already above target
   a problem? What would correct this?

---

## Scenario 16 — slot_end_time template method correctness

Current time: 06:00 (schedule_changed/on)
Schedule "Morning Shower 26C": just turned on
next_event attribute: "2026-04-11T07:00:00+01:00"

1. Show the slot_end_time variable template.
   Confirm it uses .strftime('%H:%M') method syntax NOT
   | strftime filter (which crashes with TemplateRuntimeError).
2. What does slot_end_time evaluate to?
3. Is it included in the Starting Comfort Phase notification body?

---

## Scenario 17 — fixed_radiator_temperature variable shadowing

Current time: 06:00 (comfort phase active)
Blueprint input fixed_radiator_temperature: 35
Variables section: check for fixed_radiator_temperature definition

1. Is fixed_radiator_temperature defined in the variables section?
2. If yes — does it read from input_number.sha_bathroom_fixed_radiator_temperature?
3. What does that entity return? (unknown or 0 if entity missing)
4. Does this overwrite the input value of 35 with 0?
5. What temperature is the fixed TRV commanded to?
6. After the fix — variable definition removed — what does
   fixed_radiator_temperature resolve to in the climate action?
   Show: fixed_radiator_temperature | float(30)

---

## Scenario 18 — Pre-heat notification fires every 5 minutes

Current time: 05:20 (first pre-heat loop)
sha_preheat_notified: off
Pre-heat active: yes

1. Control loop fires at 05:20 — preheat=SEND.
   Notification sent. Is turn_on sha_preheat_notified inside
   the notification if/then block or outside as independent action?
2. If inside — sha_preheat_notified may not turn on if notification
   was skipped for any reason → preheat=SEND again at 05:25.
3. After fix — turn_on is independent block outside notification.
   Control loop at 05:25 — preheat=skip. Why?
   Show: already_sent check = sha_preheat_notified state = on.

---

## Scenario 19 — Notification gates always showing SEND

Current time: any control loop run
No window open, no override active, comfort phase active.

Expected notification gates:
  preheat=skip
  target=skip or SEND (depends on target_reached)
  standby=skip
  window=skip  ← must be skip when no window is open
  override=skip ← must be skip when no override is active

1. Show the window gate condition.
   Does it check windows_open_bool AND not window_timeout_notified_on?
   Or does it only check notify_window_effective (always true)?
2. Show the override gate condition.
   Does it check override_active_bool AND not override_notified_on?
   Or does it only check notify_override_effective (always true)?
3. After fix — what conditions must ALL be true for window=SEND?
   - notify_window_effective (notifications enabled by user)
   - windows_open_bool (window actually open)
   - not window_timeout_notified_on (not already sent)

---

## Scenario 20 — Fixed TRV not commanded in comfort phase

Current time: 06:05 (control loop during comfort phase)
Schedule "Morning Shower 26C": active
in_comfort_bool: true
fixed_radiator_thermostats: climate.bathroom_heated_towel_rail
fixed_radiator_temperature: 35

1. Find the comfort branch in the control_loop default section.
   Is there an explicit climate action for fixed_radiator_thermostats?
2. Show the full fixed TRV block including the count > 0 guard.
3. Show the system_log.write entry that confirms it is executing.
4. What temperature is sent? Show: fixed_radiator_temperature | float(30)
5. After fix — confirm log entry appears:
   SHA [Bathroom] Commanding fixed TRV(s) → heat at 35.0°C.

---

## After all scenarios

Produce a summary table:

| Scenario | Variables correct | Actions correct | Notifications correct | Issues found |
|---|---|---|---|---|

List any bugs or unexpected behaviours found while tracing
through the logic. Be specific about which line or condition
caused the issue.

Flag any scenario that is no longer valid due to blueprint changes
since last run.

Suggest new scenarios for any features or bugs discovered
since this file was last updated.
```