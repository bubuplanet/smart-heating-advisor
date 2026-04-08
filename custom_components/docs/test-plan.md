
# Smart Heating Advisor — Test Plan v0.0.1

## Environment
- HA Version: 2026.4.1
- SHA Version: dev branch
- Test date: ___________
- Tester: ___________

---

## Phase 1 — Installation

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 1.1 | Install SHA via git clone on production Pi | No errors in HA logs | ☐ Pass ☐ Fail | |
| 1.2 | SHA integration appears in Settings → Devices & Services | SHA card visible | ☐ Pass ☐ Fail | |
| 1.3 | Blueprint auto-installed in /config/blueprints | smart_heating_advisor.yaml present | ☐ Pass ☐ Fail | |
| 1.4 | messages.md present in component folder | File exists | ☐ Pass ☐ Fail | |
| 1.5 | No ERROR in HA logs after restart | Clean startup | ☐ Pass ☐ Fail | |
| 1.6 | Setup persistent notification appears | Notification visible in HA UI | ☐ Pass ☐ Fail | |

---

## Phase 2 — Config Flow

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 2.1 | Open config flow — Step 1 Ollama | Form shows URL + model fields | ☐ Pass ☐ Fail | |
| 2.2 | Enter wrong Ollama URL | Error shown — cannot proceed | ☐ Pass ☐ Fail | |
| 2.3 | Enter correct Ollama URL + model | Proceeds to Step 2 | ☐ Pass ☐ Fail | |
| 2.4 | Enter wrong InfluxDB credentials | Error shown — cannot proceed | ☐ Pass ☐ Fail | |
| 2.5 | Enter correct InfluxDB credentials | Proceeds to Step 3 | ☐ Pass ☐ Fail | |
| 2.6 | Step 3 shows HA Areas as multi-select | All areas listed | ☐ Pass ☐ Fail | |
| 2.7 | Select Bathroom area | Proceeds to entity confirmation | ☐ Pass ☐ Fail | |
| 2.8 | Entity confirmation shows auto-detected temp sensor | Correct sensor pre-filled | ☐ Pass ☐ Fail | |
| 2.9 | Entity confirmation shows auto-detected TRVs | Correct TRVs pre-filled | ☐ Pass ☐ Fail | |
| 2.10 | Complete config flow | SHA configured successfully | ☐ Pass ☐ Fail | |
| 2.11 | SHA cannot be installed twice | Aborts with already configured | ☐ Pass ☐ Fail | |

---

## Phase 3 — Room Management — Add Room

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 3.1 | + Add Room button visible on SHA integration card | Button present | ☐ Pass ☐ Fail | |
| 3.2 | Click + Add Room — Step 1 shows method selection | Select Area / Manual options | ☐ Pass ☐ Fail | |
| 3.3 | Select Area path — already configured areas excluded | Bathroom not shown if already added | ☐ Pass ☐ Fail | |
| 3.4 | Select Area — entity auto-detection works | Temp sensor + TRVs pre-filled | ☐ Pass ☐ Fail | |
| 3.5 | Complete Add Room via area | Room subentry appears on card | ☐ Pass ☐ Fail | |
| 3.6 | Select Manual path — free text room name | Form accepts any name | ☐ Pass ☐ Fail | |
| 3.7 | Manual path — enter duplicate room name | Error: room already exists | ☐ Pass ☐ Fail | |
| 3.8 | Manual path — leave entities empty | Room created without entities | ☐ Pass ☐ Fail | |
| 3.9 | Complete Add Room manually | Room subentry appears on card | ☐ Pass ☐ Fail | |
| 3.10 | Add Room — blueprint automation created | SHA — ROOM automation in HA | ☐ Pass ☐ Fail | |
| 3.11 | Blueprint automation created as disabled | Automation state is disabled | ☐ Pass ☐ Fail | |
| 3.12 | Add Room persistent notification sent | Notification with automation link | ☐ Pass ☐ Fail | |

---

## Phase 4 — Room Management — Remove Room

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 4.1 | Each room shows ⋮ menu on integration card | Three dot menu visible per room | ☐ Pass ☐ Fail | |
| 4.2 | ⋮ menu shows Delete option | Delete option present | ☐ Pass ☐ Fail | |
| 4.3 | Click Delete — HA confirmation dialog appears | Confirm dialog shown | ☐ Pass ☐ Fail | |
| 4.4 | Confirm delete — all switch entities removed | No switch.sha_ROOM_* in HA | ☐ Pass ☐ Fail | |
| 4.5 | Confirm delete — number entity removed | No number.sha_ROOM_* in HA | ☐ Pass ☐ Fail | |
| 4.6 | Confirm delete — sensor entities removed | No sensor.sha_ROOM_* in HA | ☐ Pass ☐ Fail | |
| 4.7 | Confirm delete — device removed | SHA — ROOM device gone from HA | ☐ Pass ☐ Fail | |
| 4.8 | Confirm delete — automation disabled | SHA — ROOM automation disabled | ☐ Pass ☐ Fail | |
| 4.9 | Confirm delete — automation NOT deleted | SHA — ROOM automation still exists | ☐ Pass ☐ Fail | |
| 4.10 | Confirm delete — room removed from registry | Room not in coordinator registry | ☐ Pass ☐ Fail | |
| 4.11 | Confirm delete — persistent notification sent | Removal notification visible | ☐ Pass ☐ Fail | |
| 4.12 | No orphan devices after delete | Nothing under "Devices that don't belong to a sub-entry" | ☐ Pass ☐ Fail | |
| 4.13 | Delete last room — SHA still loads | No crash with zero rooms | ☐ Pass ☐ Fail | |
| 4.14 | Re-add deleted room — works cleanly | No duplicate or conflict | ☐ Pass ☐ Fail | |

---

## Phase 5 — Entities

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 5.1 | number.sha_ROOM_heating_rate exists | Default 0.15 °C/min | ☐ Pass ☐ Fail | |
| 5.2 | number.sha_ROOM_heating_rate persists after HA restart | Value retained | ☐ Pass ☐ Fail | |
| 5.3 | switch.sha_ROOM_override exists | Default off | ☐ Pass ☐ Fail | |
| 5.4 | switch.sha_ROOM_airing_mode exists | Default off | ☐ Pass ☐ Fail | |
| 5.5 | switch.sha_ROOM_preheat_notified exists | Default off | ☐ Pass ☐ Fail | |
| 5.6 | switch.sha_ROOM_target_notified exists | Default off | ☐ Pass ☐ Fail | |
| 5.7 | switch.sha_ROOM_standby_notified exists | Default off | ☐ Pass ☐ Fail | |
| 5.8 | switch.sha_ROOM_vacation_notified exists | Default off | ☐ Pass ☐ Fail | |
| 5.9 | switch.sha_ROOM_window_timeout_notified exists | Default off | ☐ Pass ☐ Fail | |
| 5.10 | switch.sha_ROOM_preheat_notifications_enabled exists | Default on | ☐ Pass ☐ Fail | |
| 5.11 | switch.sha_ROOM_target_notifications_enabled exists | Default on | ☐ Pass ☐ Fail | |
| 5.12 | switch.sha_ROOM_standby_notifications_enabled exists | Default on | ☐ Pass ☐ Fail | |
| 5.13 | switch.sha_ROOM_window_notifications_enabled exists | Default on | ☐ Pass ☐ Fail | |
| 5.14 | switch.sha_ROOM_override_notifications_enabled exists | Default on | ☐ Pass ☐ Fail | |
| 5.15 | sensor.sha_ROOM_heating_rate exists | Shows current rate | ☐ Pass ☐ Fail | |
| 5.16 | sensor.sha_ROOM_last_analysis exists | Shows unknown initially | ☐ Pass ☐ Fail | |
| 5.17 | sensor.sha_ROOM_confidence exists | Shows unknown initially | ☐ Pass ☐ Fail | |
| 5.18 | sensor.sha_ROOM_weekly_report exists | Shows no report yet | ☐ Pass ☐ Fail | |
| 5.19 | All entities grouped under correct device | SHA — ROOM device in HA | ☐ Pass ☐ Fail | |
| 5.20 | All switch entities persist after HA restart | States retained | ☐ Pass ☐ Fail | |

---

## Phase 6 — Blueprint Automation

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 6.1 | Create schedule helper named "Morning Shower 26C" | Helper created in HA | ☐ Pass ☐ Fail | |
| 6.2 | Open SHA — Bathroom automation | Pre-filled with correct entities | ☐ Pass ☐ Fail | |
| 6.3 | Add schedule helper to automation | Schedule visible in blueprint | ☐ Pass ☐ Fail | |
| 6.4 | Enable automation | Automation state: on | ☐ Pass ☐ Fail | |
| 6.5 | Automation runs on 5 min time_pattern | Logs show control loop | ☐ Pass ☐ Fail | |
| 6.6 | Schedule active — TRV set to heat at correct temp | climate.set_temperature called | ☐ Pass ☐ Fail | |
| 6.7 | Schedule inactive — TRV set to default mode | climate.set_hvac_mode called | ☐ Pass ☐ Fail | |
| 6.8 | Pre-heat starts at correct time before schedule | TRV heating before schedule | ☐ Pass ☐ Fail | |
| 6.9 | Pre-heat notification sent once | One notification in HA | ☐ Pass ☐ Fail | |
| 6.10 | Target reached notification sent once | One notification in HA | ☐ Pass ☐ Fail | |
| 6.11 | Standby notification sent once when schedule ends | One notification in HA | ☐ Pass ☐ Fail | |
| 6.12 | No duplicate notifications on 5 min loop | Flag switches prevent repeats | ☐ Pass ☐ Fail | |

---

## Phase 7 — Window Detection

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 7.1 | Open window sensor — wait reaction time | airing_mode switch turns on | ☐ Pass ☐ Fail | |
| 7.2 | Window open — TRV set to off | Heating stops | ☐ Pass ☐ Fail | |
| 7.3 | Window open notification sent once | One notification | ☐ Pass ☐ Fail | |
| 7.4 | Window open notification not repeated | Flag prevents duplicate | ☐ Pass ☐ Fail | |
| 7.5 | Close window — airing_mode turns off | Switch state off | ☐ Pass ☐ Fail | |
| 7.6 | Close window — heating resumes | TRV back to correct mode | ☐ Pass ☐ Fail | |
| 7.7 | Window closed notification sent | One notification | ☐ Pass ☐ Fail | |
| 7.8 | Close window before reaction time — no notification | No spurious notification | ☐ Pass ☐ Fail | |

---

## Phase 8 — Manual Override

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 8.1 | Manually change TRV temperature | override switch turns on | ☐ Pass ☐ Fail | |
| 8.2 | Override active — control loop skipped | No SHA commands to TRV | ☐ Pass ☐ Fail | |
| 8.3 | Override notification sent | One notification with resume time | ☐ Pass ☐ Fail | |
| 8.4 | Override expires after configured duration | override switch turns off | ☐ Pass ☐ Fail | |
| 8.5 | sha_override_ended event fired on expiry | Event in HA logbook | ☐ Pass ☐ Fail | |
| 8.6 | Override ended — control loop resumes | TRV back under SHA control | ☐ Pass ☐ Fail | |
| 8.7 | Override ended notification sent | One notification | ☐ Pass ☐ Fail | |
| 8.8 | SHA-commanded TRV change does not trigger override | No false override | ☐ Pass ☐ Fail | |
| 8.9 | Schedule changes during override do not affect TRV | Override protected | ☐ Pass ☐ Fail | |

---

## Phase 9 — Vacation Mode

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 9.1 | Create calendar event starting with vacation keyword | Event visible in HA | ☐ Pass ☐ Fail | |
| 9.2 | Calendar event active — vacation_active is true | Template evaluates true | ☐ Pass ☐ Fail | |
| 9.3 | Vacation mode off — TRV set to off | Heating stops | ☐ Pass ☐ Fail | |
| 9.4 | Vacation mode frost — TRV maintains frost temp | TRV set to vacation_temperature | ☐ Pass ☐ Fail | |
| 9.5 | Vacation notification sent once | One notification | ☐ Pass ☐ Fail | |
| 9.6 | Calendar event ends — vacation_active false | Normal heating resumes | ☐ Pass ☐ Fail | |
| 9.7 | Vacation notification flag reset on vacation end | vacation_notified off | ☐ Pass ☐ Fail | |

---

## Phase 10 — AI Analysis

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 10.1 | Trigger sha.run_daily_analysis manually | Runs without error | ☐ Pass ☐ Fail | |
| 10.2 | Ollama unreachable — graceful failure | Notification sent, no crash | ☐ Pass ☐ Fail | |
| 10.3 | InfluxDB no data — graceful skip | Warning logged, notification sent | ☐ Pass ☐ Fail | |
| 10.4 | Valid analysis — heating rate updated | number.sha_ROOM_heating_rate changes | ☐ Pass ☐ Fail | |
| 10.5 | Valid analysis — sensor.sha_ROOM_confidence updated | Shows high/medium/low | ☐ Pass ☐ Fail | |
| 10.6 | Valid analysis — sensor.sha_ROOM_last_analysis updated | Shows current timestamp | ☐ Pass ☐ Fail | |
| 10.7 | Valid analysis — mobile notification sent | Notification with new rate | ☐ Pass ☐ Fail | |
| 10.8 | Valid analysis — persistent notification created | Notification in HA UI | ☐ Pass ☐ Fail | |
| 10.9 | Trigger sha.run_weekly_analysis manually | Runs without error | ☐ Pass ☐ Fail | |
| 10.10 | Weekly analysis — persistent notification created | Weekly report in HA UI | ☐ Pass ☐ Fail | |
| 10.11 | Weekly analysis — heating rate NOT changed | Rate unchanged after weekly | ☐ Pass ☐ Fail | |
| 10.12 | Weekly analysis — sensor.sha_ROOM_weekly_report updated | Report text visible | ☐ Pass ☐ Fail | |
| 10.13 | Daily analysis runs automatically at 02:00 | Log entry at 02:00 | ☐ Pass ☐ Fail | |
| 10.14 | Weekly analysis runs automatically Sunday 01:00 | Log entry Sunday 01:00 | ☐ Pass ☐ Fail | |

---

## Phase 11 — Options Flow

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 11.1 | Open gear icon on SHA integration card | Options flow opens | ☐ Pass ☐ Fail | |
| 11.2 | Options shows Ollama settings | URL and model editable | ☐ Pass ☐ Fail | |
| 11.3 | Options shows InfluxDB settings | All fields editable | ☐ Pass ☐ Fail | |
| 11.4 | Options shows weather entity | Entity editable | ☐ Pass ☐ Fail | |
| 11.5 | Options shows debug toggle | Toggle present | ☐ Pass ☐ Fail | |
| 11.6 | Enable debug — SHA debug logs appear | DEBUG level logs visible | ☐ Pass ☐ Fail | |
| 11.7 | Disable debug — DEBUG logs stop | INFO level only | ☐ Pass ☐ Fail | |
| 11.8 | Options does NOT show room management | No add/remove room in options | ☐ Pass ☐ Fail | |

---

## Phase 12 — HA Restart resilience

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 12.1 | Restart HA — all entities reload | All sha_* entities available | ☐ Pass ☐ Fail | |
| 12.2 | Restart HA — heating rate value retained | number.sha_ROOM_heating_rate same value | ☐ Pass ☐ Fail | |
| 12.3 | Restart HA — switch states retained | All switches same state | ☐ Pass ☐ Fail | |
| 12.4 | Restart HA — room registry intact | All rooms still configured | ☐ Pass ☐ Fail | |
| 12.5 | Restart HA — blueprint still installed | Blueprint file present | ☐ Pass ☐ Fail | |
| 12.6 | Restart HA — automations still enabled | SHA — ROOM automations on | ☐ Pass ☐ Fail | |
| 12.7 | Restart HA — weekly overdue catch-up runs | Weekly analysis if >7 days | ☐ Pass ☐ Fail | |
| 12.8 | Update SHA via git pull — HA reloads cleanly | No errors after update | ☐ Pass ☐ Fail | |

---

## Phase 13 — Multi-room

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 13.1 | Add second room | Both rooms show as subentries | ☐ Pass ☐ Fail | |
| 13.2 | Both rooms have independent entities | No entity ID conflicts | ☐ Pass ☐ Fail | |
| 13.3 | Both room automations run independently | No interference between rooms | ☐ Pass ☐ Fail | |
| 13.4 | Daily analysis runs for all rooms | Log shows each room analysed | ☐ Pass ☐ Fail | |
| 13.5 | Override in Room A does not affect Room B | Room B heating unaffected | ☐ Pass ☐ Fail | |
| 13.6 | sha_override_ended event only resumes correct room | Wrong room ignores event | ☐ Pass ☐ Fail | |
| 13.7 | Remove one room — other room unaffected | Room B still working | ☐ Pass ☐ Fail | |

---

## Test summary

| Phase | Total | Pass | Fail | Blocked |
|---|---|---|---|---|
| 1 — Installation | 6 | | | |
| 2 — Config flow | 11 | | | |
| 3 — Add room | 12 | | | |
| 4 — Remove room | 14 | | | |
| 5 — Entities | 20 | | | |
| 6 — Blueprint | 12 | | | |
| 7 — Window detection | 8 | | | |
| 8 — Manual override | 9 | | | |
| 9 — Vacation mode | 7 | | | |
| 10 — AI analysis | 14 | | | |
| 11 — Options flow | 8 | | | |
| 12 — HA restart | 8 | | | |
| 13 — Multi-room | 7 | | | |
| **Total** | **136** | | | |

---

## Blocking issues found

| # | Phase | Description | Severity |
|---|---|---|---|
| | | | |

---

## Sign-off

Ready for v0.0.1 release when all Phase 1–6 tests pass with zero failures
and Phases 7–13 have no critical failures.