# 🌡️ Smart Heating Advisor — Blueprints

A set of 5 Home Assistant automation blueprints for smart room heating.
Designed to work with the [Smart Heating Advisor](../../README.md) HACS integration
but can also be used standalone without it.

---

## 🎯 What these blueprints do

Heat any room to a target temperature at a defined time, using AI-optimized
pre-heating that starts exactly when needed — not too early, not too late.
Every room in your house can have its own independent heating schedule,
all managed from the same set of blueprints.

---

## 📋 The 5 Blueprints

| File | Purpose | Required |
|---|---|---|
| `sha_room_heating.yaml` | Main heating loop | ✅ Always |
| `sha_override_activated.yaml` | Detects manual TRV changes | ✅ Always |
| `sha_override_resume.yaml` | Resumes after override expires | ✅ Always |
| `sha_daily_reset.yaml` | Resets flags daily | ✅ Always |
| `sha_end_of_day_notification.yaml` | End of day notification | ⚪ Optional |

For each room you want to control, create **one automation from each blueprint**.

---

## 🚀 Quick Start

### Step 1 — Import blueprints

Import each blueprint into Home Assistant via URL:

1. Go to **Settings → Automations & Scenes → Blueprints**
2. Click **Import Blueprint**
3. Paste the raw GitHub URL for each blueprint:
```
https://github.com/bubuplanet/smart-heating-advisor/blob/master/blueprints/sha_daily_reset.yaml
https://github.com/bubuplanet/smart-heating-advisor/blob/master/blueprints/sha_override_activated.yaml
https://github.com/bubuplanet/smart-heating-advisor/blob/master/blueprints/sha_override_resume.yaml
https://github.com/bubuplanet/smart-heating-advisor/blob/master/blueprints/sha_room_heating.yaml
https://github.com/bubuplanet/smart-heating-advisor/blob/master/blueprints/sha_end_of_day_notification.yaml
```

### Step 2 — Create helpers per room

For each room, create these helpers in **Settings → Helpers**.
Replace `ROOM` with your room name (e.g. `bathroom`, `office`, `bedroom_alessio`).

#### Toggle helpers (8 per room)
Go to **+ Create Helper → Toggle** for each:

| Name to type | Entity ID created |
|---|---|
| `ROOM_heating_override` | `input_boolean.ROOM_heating_override` |
| `ROOM_automation_running` | `input_boolean.ROOM_automation_running` |
| `ROOM_room_target_notified` | `input_boolean.ROOM_room_target_notified` |
| `ROOM_towel_target_notified` | `input_boolean.ROOM_towel_target_notified` |
| `ROOM_mode_preheat` | `input_boolean.ROOM_mode_preheat` |
| `ROOM_mode_maintain` | `input_boolean.ROOM_mode_maintain` |
| `ROOM_mode_standby` | `input_boolean.ROOM_mode_standby` |
| `ROOM_window_notified` | `input_boolean.ROOM_window_notified` |

#### Date/Time helper (1 per room)
Go to **+ Create Helper → Date and/or time** → select **Date and time**:

| Name to type | Entity ID created |
|---|---|
| `ROOM_override_until` | `input_datetime.ROOM_override_until` |

#### Number helper (1 per room)
Go to **+ Create Helper → Number**:

| Name | Entity ID | Min | Max | Step | Initial | Mode |
|---|---|---|---|---|---|---|
| `ROOM_heating_rate` | `input_number.ROOM_heating_rate` | 0.05 | 0.30 | 0.01 | 0.15 | Input field |

> 💡 If you have the Smart Heating Advisor integration installed, it will
> automatically update `input_number.ROOM_heating_rate` daily based on
> your room's historical heating data.

### Step 3 — Create automations from blueprints

For each room, create 5 automations — one from each blueprint:

1. Go to **Settings → Automations & Scenes → Blueprints**
2. Find **SHA - Room Heating** and click **Create Automation**
3. Fill in the form with your room's entity IDs and settings
4. Save with a descriptive name like `Bathroom - Smart Heating`
5. Repeat for the other 4 blueprints

---

## 🏠 Example Room Setup

### Bathroom
| Setting | Value |
|---|---|
| Room temperature sensor | `sensor.bathroom_thermostat_temperature` |
| Radiator | `climate.bathroom_radiator` |
| Towel rail | `climate.bathroom_heated_towel_rail` |
| Window sensor 1 | `binary_sensor.bathroom_window` |
| Window sensor 2 | `binary_sensor.bathroom_window_small` |
| Target temperature | `26°C` |
| Towel rail target | `30°C` |
| Target time | `06:00` |
| Active window end | `07:00` |
| Standby temperature | `16°C` |
| Vacation temperature | `12°C` |
| Override duration | `2 hours` |

### Bedroom Alessio
| Setting | Value |
|---|---|
| Room temperature sensor | `sensor.bedroom_alessio_thermostat_temperature` |
| Radiator | `climate.bedroom_alessio_radiator` |
| Towel rail | _(none)_ |
| Window sensor 1 | `binary_sensor.bedroom_alessio_window_left_contact` |
| Window sensor 2 | `binary_sensor.bedroom_alessio_window_right_contact` |
| Target temperature | `20°C` |
| Target time | `07:00` |
| Active window end | `08:00` |
| Standby temperature | `16°C` |
| Vacation temperature | `12°C` |
| Override duration | `2 hours` |

### Office
| Setting | Value |
|---|---|
| Room temperature sensor | `sensor.office_thermostat_temperature` |
| Radiator | `climate.office_radiator` |
| Towel rail | _(none)_ |
| Window sensor 1 | `binary_sensor.office_window_left_contact` |
| Window sensor 2 | `binary_sensor.office_window_right_contact` |
| Target temperature | `21°C` |
| Target time | `08:00` |
| Active window end | `18:00` |
| Standby temperature | `16°C` |
| Vacation temperature | `12°C` |
| Override duration | `2 hours` |

---

## 📅 How the Main Heating Blueprint Works
```
00:00 ──────────────────────────────────────────────────────────────────
       Daily Reset — all flags cleared

03:00 ──────────────────────────────────────────────────────────────────
       Pre-heat window opens
       Every 5 min: check if heating needs to start based on:
         - Current room temperature
         - Target temperature
         - Heating rate (from input_number.ROOM_heating_rate)
         - Minutes until target time
         - 30 min safety buffer

06:00 ──────────────────────────────────────────────────────────────────  ← Target time
       Maintain window: keeps room at target temperature
       Cycles radiator on/off with 0.5°C hysteresis

07:00 ──────────────────────────────────────────────────────────────────
       Standby mode: maintains configurable standby temperature
       Towel rail turns off

23:00 ──────────────────────────────────────────────────────────────────
       Automation stops for the night
       End of day notification sent
```

---

## 🪟 Window Detection

When any window sensor is open:
- Both radiator and towel rail turn off immediately
- A notification is sent once (not repeated every 5 min)
- When all windows close → notification sent + heating resumes

The override detection automation ignores TRV changes when
windows are open — so closing a window and having the automation
restart heating won't trigger a false override.

---

## 🖐️ Manual Override

When you manually change a thermostat (physically or via app):
- Smart heating pauses for the configured duration (default 2 hours)
- A notification confirms the pause and shows resume time
- After the duration expires, smart heating automatically resumes
- The override detection ignores changes made by the automation itself

---

## 🏖️ Vacation Mode

Create a Google Calendar event starting with **"Vacation"** (e.g. "Vacation Portugal"):
- Pre-heat and maintain are skipped entirely
- Room is kept at frost protection temperature (default 12°C)
- Towel rail is turned off
- Heating resumes automatically when the calendar event ends

---

## ⚡ Energy Saving Features

| Feature | Energy Impact |
|---|---|
| Smart pre-heat start | Heats only as long as needed — not from a fixed early time |
| Window detection | Stops heating immediately when ventilating |
| Vacation mode | Full heating suspension when away |
| Manual override | Respects your schedule without fighting it |
| AI heating rate | Calibrated to your actual radiators — no over-shooting |
| Standby temperature | Maintains minimum comfort without over-heating |

---

## 🔧 Helpers Reference

Complete list of helpers needed per room:

| Entity ID | Type | Purpose |
|---|---|---|
| `input_boolean.ROOM_heating_override` | Toggle | Is override currently active? |
| `input_boolean.ROOM_automation_running` | Toggle | Is automation currently changing TRVs? |
| `input_boolean.ROOM_room_target_notified` | Toggle | Has room target notification been sent today? |
| `input_boolean.ROOM_towel_target_notified` | Toggle | Has towel target notification been sent today? |
| `input_boolean.ROOM_mode_preheat` | Toggle | Has pre-heat mode notification been sent today? |
| `input_boolean.ROOM_mode_maintain` | Toggle | Has maintain mode notification been sent today? |
| `input_boolean.ROOM_mode_standby` | Toggle | Has standby mode notification been sent today? |
| `input_boolean.ROOM_window_notified` | Toggle | Has window open notification been sent today? |
| `input_datetime.ROOM_override_until` | Date/Time | When does the current override expire? |
| `input_number.ROOM_heating_rate` | Number | Heating rate in °C/min (auto-calibrated by SHA) |

---

## 📬 Notifications

| Notification | When sent |
|---|---|
| 🌅 Pre-heat Mode Started | Once when pre-heat begins |
| ✅ Room Target Reached | Once when room hits target temperature |
| ✅ Towel Rail Target Reached | Once when towel rail hits target temperature |
| 🛁 Heating Window Active | Once at target time |
| 🌡️ Standby Mode | Once when standby begins |
| 🪟 Window Open — Heating Paused | Once when any window opens |
| 🪟 Window Closed — Heating Resumed | Once when all windows close |
| 🏖️ Vacation Mode Active | Once when vacation calendar event detected |
| 🖐️ Heating Override Active | When manual TRV change detected |
| 🔄 Heating Resumed | When override expires |
| 🌙 Heating Off for the Night | At end of day notification time |

---

## 🔗 Related

- [Smart Heating Advisor Integration](../../README.md)
- [Example Automations](../../examples/automations/README.md)
- [Home Assistant Blueprints Documentation](https://www.home-assistant.io/docs/blueprint/)

---

## 📄 License

MIT License — see [LICENSE](../../LICENSE) for details.
```

---

Your complete final structure is now fully documented:
```
blueprints/
└── automation/
    └── smart_heating_advisor/
        ├── README.md                          ✅
        ├── sha_room_heating.yaml              ✅
        ├── sha_override_activated.yaml        ✅
        ├── sha_override_resume.yaml           ✅
        ├── sha_daily_reset.yaml               ✅
        └── sha_end_of_day_notification.yaml   ✅