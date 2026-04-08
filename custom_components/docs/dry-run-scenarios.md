I want you to simulate several heating scenarios by tracing through
the blueprint logic step by step. Do not modify any files.

Read blueprints/smart_heating_advisor.yaml completely before starting.

For each scenario below trace through every variable calculation,
every condition check and every action that would fire. Show your
working at each step. Use realistic values I provide.

---

## Room configuration for all scenarios

- room_name: Bathroom
- room_id: bathroom
- temperature_sensor: sensor.bathroom_thermostat_temperature
- heating_rate: 0.13 (from number.sha_bathroom_heating_rate)
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
3. What is in_preheat_bool?
4. What is target_temperature?
5. What is target_mode?
6. What action is sent to the TRV?
7. Which notifications fire and why?
8. What switch states change?

Then advance time to 06:00 (schedule turns on):
9. What trigger fires?
10. What does active_schedule evaluate to now?
11. What action is sent to the TRV?
12. Which notifications fire?

Then advance time to 06:15 (room reaches 26°C):
13. What does target_reached evaluate to?
14. Which notification fires?
15. What switch state changes?

Then advance time to 07:00 (schedule ends):
16. What trigger fires?
17. What action is sent to the TRV?
18. Which notification fires?
19. What switch states reset?

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
9. Does heating continue normally?

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
4. What is target_mode now?
5. What action is sent to TRV?
6. Is window_timeout_notified_on true or false before notification?
7. Is notification sent? Which one?
8. What switch states change?

Step B — control loop fires at 05:55 (window still open):
9. What does target_mode evaluate to?
10. What action is sent to TRV?
11. Is another notification sent? Why or why not?

Step C — window closes at 06:10:
12. Which trigger fires?
13. Are all windows closed — how is this checked?
14. What is airing_mode state after?
15. Is window closed notification sent? What condition allows it?
16. What switch states change?
17. Heating resumes — what mode and temperature?

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
18. What action is sent to TRV?
19. Which notification fires?

---

## Scenario 5 — Two consecutive schedules back to back

Schedules:
- "Morning Shower 26C": 06:00 to 07:00
- "Morning Routine 22C": 07:00 to 08:00

Current time: 06:59
Room temperature: 25.8°C
target_reached: true
target_notified: on
standby_notified: off

At 07:00 both schedule_changed triggers fire simultaneously:
- "Morning Shower 26C" turns off
- "Morning Routine 22C" turns on

1. For the OFF trigger on "Morning Shower 26C":
   What is in_comfort_bool at this moment?
   What is in_preheat_bool?
   Does the standby notification condition pass?
   Why or why not?

2. For the ON trigger on "Morning Routine 22C":
   What does active_schedule evaluate to?
   What is comfort_temp?
   What action is sent to TRV?
   What notification fires?

3. Is a spurious standby notification sent between the two schedules?
   Show exactly which condition prevents or allows it.

4. What is the final TRV temperature after both triggers process?

---

## Scenario 6 — Heating rate edge case

Current time: 05:55
Room temperature: 25.8°C (almost at target already)
Schedule "Morning Shower 26C": starts at 06:00
heating_rate: 0.13

1. Calculate mins_needed:
   (26 - 25.8) / 0.13 = ?
   max(result, 5) = ?

2. Calculate mins_to_start:
   06:00 - 05:55 = 5 minutes

3. Is 0 <= mins_to_start <= mins_needed?

4. Does pre-heat trigger?

5. If yes — is this correct behaviour or a spurious pre-heat?

6. Now same scenario with room at 25.95°C:
   (26 - 25.95) / 0.13 = ?
   max(result, 5) = ?
   Does pre-heat trigger?
   Is sending the TRV a heat command here correct or wasteful?

---

## After all scenarios

Produce a summary table:

| Scenario | Variables calculated correctly | Actions correct | Notifications correct | Issues found |
|---|---|---|---|---|

List any bugs or unexpected behaviours you found while tracing
through the logic. Be specific about which line or condition
caused the issue.