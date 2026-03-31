# 🌡️ Smart Heating Advisor - Blueprint

[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1.0+-blue.svg)](https://www.home-assistant.io/)
[![Blueprint](https://img.shields.io/badge/Blueprint-Automation-green.svg)](https://www.home-assistant.io/docs/blueprint/)
[![SHA Integration](https://img.shields.io/badge/SHA-Integration-orange.svg)](https://github.com/bubuplanet/smart-heating-advisor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 🔘 Import Blueprint

[![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fbubuplanet%2Fsmart-heating-advisor%2Fmain%2Fblueprints%2Fautomation%2Fsmart_heating_advisor%2Fsha_unified_heating.yaml)

> Click the button above to import this blueprint directly into your Home Assistant.  
> Or copy the URL manually:  
> `https://raw.githubusercontent.com/bubuplanet/smart-heating-advisor/main/blueprints/automation/smart_heating_advisor/sha_unified_heating.yaml`

---

> **This blueprint is designed to work with the [Smart Heating Advisor (SHA)](../../../README.md) HACS integration.**  
> It can also be used standalone without SHA, but without AI calibration the heating experience will be limited to a fixed heating rate with no automatic adaptation.

---

## 📋 Table of Contents

- [How It Works](#-how-it-works)
- [Features](#-features)
- [Quick Start](#-quick-start)
- [Usage Modes](#-usage-modes)
- [Schedule Naming Convention](#-schedule-naming-convention)
- [Required Helpers](#-required-helpers)
- [Blueprint Configuration](#-blueprint-configuration)
- [Notification Reference](#-notification-reference)
- [Example: Bathroom Setup](#-example-bathroom-setup)
- [Troubleshooting](#-troubleshooting)

---

## ⚙️ How It Works

```
Room temp sensor
      │
      ▼
┌─────────────────────────────────────────────────┐
│  Every 5 min + on schedule change               │
│                                                 │
│  1. Check active schedule → target temp         │
│  2. Check upcoming schedule → pre-heat needed?  │
│     mins_needed = (target - current) / rate     │
│  3. Apply heating or standby                    │
│  4. Send notifications (once per event)         │
└─────────────────────────────────────────────────┘
      │
      ▼
 SHA Integration (optional)
 Updates heating rate daily
 using AI + InfluxDB history
```

---

## ✨ Features

| Feature | Description |
|---|---|
| ⏱️ **Smart pre-heat** | Calculates exact start time from current temp + heating rate |
| 📅 **Unlimited schedules** | Add as many HA Schedule helpers as needed |
| 🌡️ **Per-schedule temperature** | Encoded in schedule name (e.g. `Morning Shower 26C`) |
| 🔥 **Fixed radiator thermostat** | Optional — for towel rails, floor heating etc. |
| 🏖️ **Vacation mode** | Via calendar keyword, configurable behavior |
| 🪟 **Window detection** | Configurable open/close reaction delays |
| ✋ **Manual override** | Auto-resume via HA Timer |
| 🔔 **Smart notifications** | Each notification fires only **once** per event |
| 🤖 **AI calibration** | Optional — heating rate auto-adjusted daily by SHA |

---

## 🚀 Quick Start

### Option A — With SHA Integration (Recommended)

[![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fbubuplanet%2Fsmart-heating-advisor%2Fmain%2Fblueprints%2Fautomation%2Fsmart_heating_advisor%2Fsha_unified_heating.yaml)

1. Install **Smart Heating Advisor** via HACS
2. Configure your rooms in SHA — helpers are created automatically
3. Import this blueprint:
   ```
   https://raw.githubusercontent.com/bubuplanet/smart-heating-advisor/main/blueprints/automation/smart_heating_advisor/sha_unified_heating.yaml
   ```
4. Go to **Settings → Automations → Blueprints → Smart Heating Advisor → Create Automation**
5. Fill in the form and save

### Option B — Standalone (No SHA Required)

1. Create the required helpers manually (see [Required Helpers](#-required-helpers))
2. Import this blueprint
3. Create an automation from the blueprint
4. Fill in the form and save

---

## 🔧 Usage Modes

| | ⭐ With SHA | ⚙️ Standalone |
|---|---|---|
| Smart pre-heat | ✅ | ✅ |
| Unlimited schedules | ✅ | ✅ |
| Window detection | ✅ | ✅ |
| Vacation mode | ✅ | ✅ |
| Manual override | ✅ | ✅ |
| Notifications | ✅ | ✅ |
| AI heating rate calibration | ✅ Auto-calibrated daily | ❌ Fixed at 0.15°C/min |
| Seasonal adaptation | ✅ | ❌ |
| Weather-aware | ✅ | ❌ |
| Weekly performance report | ✅ | ❌ |
| Helper creation | ✅ Automatic | ⚠️ Manual |

---

## 📝 Schedule Naming Convention

Name each **HA Schedule helper** with the target temperature at the end:

| Schedule Name | Target Temp | Result |
|---|---|---|
| `Morning Shower 26C` | 26°C | ✅ Correct |
| `Evening Bath 28C` | 28°C | ✅ Correct |
| `Night Standby 16C` | 16°C | ✅ Correct |
| `Weekend 20C` | 20°C | ✅ Correct |
| `Morning Shower` | _(no temp)_ | ⚠️ Uses fallback temp |

> 💡 **Tip:** The temperature must be at the **end** of the name, immediately followed by `C` with no space.  
> ✅ `Shower 26C` → reads `26°C`  
> ❌ `Shower 26 C` → no match, uses fallback  
> ❌ `26C Shower` → no match, uses fallback

**Create Schedule helpers in:** Settings → Helpers → + Create Helper → **Schedule**

---

## 🛠️ Required Helpers

All helpers follow the naming convention: `sha_ROOMID_suffix`  
where `ROOMID` is your room name in **lowercase with underscores**.

### Room ID Examples

| Room Name | Room ID |
|---|---|
| `Bathroom` | `bathroom` |
| `Alessio's Bedroom` | `alessios_bedroom` |
| `Living Room` | `living_room` |
| `Office` | `office` |
| `Parent Bedroom` | `parent_bedroom` |

---

### 🟢 Toggle Helpers (×6)

**Settings → Helpers → + Create Helper → Toggle**

| Helper Name to Type | Entity ID Created | Purpose |
|---|---|---|
| `sha_ROOMID_automation_running` | `input_boolean.sha_ROOMID_automation_running` | Prevents false override triggers |
| `sha_ROOMID_airing_mode` | `input_boolean.sha_ROOMID_airing_mode` | Tracks window open/close state |
| `sha_ROOMID_preheat_notified` | `input_boolean.sha_ROOMID_preheat_notified` | Pre-heat notification sent today? |
| `sha_ROOMID_target_notified` | `input_boolean.sha_ROOMID_target_notified` | Target reached notification sent? |
| `sha_ROOMID_standby_notified` | `input_boolean.sha_ROOMID_standby_notified` | Standby notification sent today? |
| `sha_ROOMID_vacation_notified` | `input_boolean.sha_ROOMID_vacation_notified` | Vacation notification sent? |

---

### 🔢 Number Helper (×1)

**Settings → Helpers → + Create Helper → Number**

| Field | Value |
|---|---|
| **Name** | `sha_ROOMID_heating_rate` |
| **Entity ID** | `input_number.sha_ROOMID_heating_rate` |
| **Min** | `0.05` |
| **Max** | `0.30` |
| **Step** | `0.01` |
| **Initial value** | `0.15` |
| **Display mode** | Input field |

> 💡 This is the heating rate in **°C per minute**.  
> `0.15` means the room heats at 1°C every ~6.5 minutes.  
> When SHA is installed it updates this value daily based on your room's actual performance.

---

### ⏱️ Timer Helper (×1)

**Settings → Helpers → + Create Helper → Timer**

| Helper Name to Type | Entity ID Created | Purpose |
|---|---|---|
| `sha_ROOMID_override` | `timer.sha_ROOMID_override` | Tracks manual override duration |

---

### 📋 Complete Example — Room: Bathroom

| Type | Name to Type | Entity ID Created |
|---|---|---|
| Toggle | `sha_bathroom_automation_running` | `input_boolean.sha_bathroom_automation_running` |
| Toggle | `sha_bathroom_airing_mode` | `input_boolean.sha_bathroom_airing_mode` |
| Toggle | `sha_bathroom_preheat_notified` | `input_boolean.sha_bathroom_preheat_notified` |
| Toggle | `sha_bathroom_target_notified` | `input_boolean.sha_bathroom_target_notified` |
| Toggle | `sha_bathroom_standby_notified` | `input_boolean.sha_bathroom_standby_notified` |
| Toggle | `sha_bathroom_vacation_notified` | `input_boolean.sha_bathroom_vacation_notified` |
| Number | `sha_bathroom_heating_rate` | `input_number.sha_bathroom_heating_rate` |
| Timer | `sha_bathroom_override` | `timer.sha_bathroom_override` |

---

## 🎛️ Blueprint Configuration

### 🏠 Room Section

| Field | Description | Example |
|---|---|---|
| **Room Name** | Friendly name — used in notifications and to derive helper IDs | `Bathroom` |
| **Room Temperature Sensor** | Sensor measuring actual room temperature | `sensor.bathroom_thermostat_temperature` |
| **Radiator Thermostat** | One or more TRVs following the schedule temp | `climate.bathroom_radiator` |
| **Fixed Radiator Thermostat** | Optional TRVs always heating to fixed temp | `climate.bathroom_heated_towel_rail` |
| **Fixed Radiator Temperature** | Temperature for fixed TRVs when active | `30°C` |

### 📅 Schedules Section

| Field | Description | Example |
|---|---|---|
| **Schedule Helpers** | Select one or more HA Schedule helpers | `Morning Shower 26C`, `Evening Bath 28C` |
| **Schedule Fallback Temperature** | Used when no temp found in schedule name | `21°C` |

### 🌡️ Default Section _(collapsed)_

| Field | Description | Default |
|---|---|---|
| **Default Heating Mode** | Off or Heat when no schedule active | `Off` |
| **Default Temperature** | Standby temp when Default Mode = Heat | `16°C` |

### 🪟 Window Detection Section _(collapsed)_

| Field | Description | Default |
|---|---|---|
| **Window & Door Sensors** | Binary sensors for windows/doors | _(empty)_ |
| **Open Reaction Time** | Delay before pausing heating | `5 min` |
| **Close Reaction Time** | Delay before resuming heating | `30 sec` |

### 🏖️ Vacation Section _(collapsed)_

| Field | Description | Default |
|---|---|---|
| **Enable Vacation Mode** | Toggle vacation detection on/off | `false` |
| **Vacation Calendar** | Calendar with vacation events | `calendar.home` |
| **Vacation Keyword** | Event title prefix to detect | `vacation` |
| **Vacation Behavior** | Off or Frost protection | `Off` |
| **Vacation Frost Temperature** | Temp during frost protection | `12°C` |

### ✋ Override Section _(collapsed)_

| Field | Description | Default |
|---|---|---|
| **Override Duration** | Minutes to pause after manual TRV change | `120 min` |

### 🔔 Notifications Section _(collapsed)_

| Notification | When | Default |
|---|---|---|
| **Notify pre-heat starts** | Once when pre-heat begins | `true` |
| **Notify target reached** | Once when room hits target | `true` |
| **Notify standby starts** | Once when no schedule active | `true` |
| **Notify window open/close** | On window state change | `true` |
| **Notify override active/resumed** | On manual TRV change + resume | `true` |

---

## 🔔 Notification Reference

| Notification | Title | When | Fires |
|---|---|---|---|
| Pre-heat | 🌅 Room — Pre-heat Started | Pre-heat begins | Once per schedule event |
| Target reached | ✅ Room — Target Reached | Room hits target temp | Once per schedule event |
| Standby | 🌡️ Room — Standby | No schedule active | Once per transition |
| Window open | 🪟 Room — Window Open | Window opens (after delay) | Once per opening |
| Window closed | 🪟 Room — Window Closed | All windows close | Once per closing |
| Override active | ✋ Room — Override Active | Manual TRV change detected | On each manual change |
| Override ended | 🔄 Room — Heating Resumed | Override timer expires | On each resume |
| Vacation active | 🏖️ Room — Vacation Mode | Vacation calendar event | Once per vacation event |

---

## 🛁 Example: Bathroom Setup

### Schedules to create

| Schedule Name | Days | Time | Target |
|---|---|---|---|
| `Morning Shower 26C` | Mon–Fri | 06:00–07:00 | 26°C |
| `Evening Bath 28C` | Mon–Sun | 19:00–20:30 | 28°C |

### Blueprint settings

| Field | Value |
|---|---|
| Room Name | `Bathroom` |
| Temperature Sensor | `sensor.bathroom_thermostat_temperature` |
| Radiator Thermostat | `climate.bathroom_radiator` |
| Fixed Radiator Thermostat | `climate.bathroom_heated_towel_rail` |
| Fixed Radiator Temperature | `30°C` |
| Schedules | `Morning Shower 26C`, `Evening Bath 28C` |
| Schedule Fallback Temp | `21°C` |
| Default Heating Mode | `Off` |
| Window Sensors | `binary_sensor.bathroom_window`, `binary_sensor.bathroom_window_small` |
| Vacation Calendar | `calendar.home` |
| Vacation Keyword | `vacation` |
| Override Duration | `120 min` |

### Typical day timeline

```
00:00  Daily flags reset (managed by SHA or manually via automation)
       │
05:10  🌅 Pre-heat starts for "Morning Shower 26C"
       │   Room: 18°C → target 26°C, Est. 53 min
       │   (notification fires once)
       │
06:00  Schedule "Morning Shower 26C" turns ON
       │   Flags reset for next cycle
       │
06:12  ✅ Room reached 26°C
       │   (notification fires once)
       │
07:00  Schedule turns OFF
       │   🌡️ Standby — heating off
       │   (notification fires once)
       │
18:10  🌅 Pre-heat starts for "Evening Bath 28C"
       │   Room: 19°C → target 28°C, Est. 60 min
       │
19:00  Schedule "Evening Bath 28C" turns ON
       │
19:15  ✅ Room reached 28°C
       │
20:30  Schedule turns OFF
       │   🌡️ Standby — heating off
```

---

## 🔍 Troubleshooting

### Pre-heat doesn't start early enough

The heating rate `0.15°C/min` may be too optimistic for your radiator.
- Increase `input_number.sha_ROOMID_heating_rate` to a lower value (e.g. `0.10`)
- Or install SHA which calibrates this automatically

### Pre-heat starts too early

The heating rate may be too conservative.
- Decrease `input_number.sha_ROOMID_heating_rate` to a higher value (e.g. `0.20`)

### Notifications firing every 5 minutes

The notification flag helpers are missing or not being reset.
- Verify all 6 toggle helpers exist with the correct entity IDs
- Check the `schedule_changed` trigger is firing correctly

### Override not working

- Verify `timer.sha_ROOMID_override` exists
- Check `input_boolean.sha_ROOMID_automation_running` exists

### Window detection not working

- Verify window sensors are `binary_sensor` domain
- Check the `open reaction time` — default is 5 min so heating won't pause immediately
- Verify `input_boolean.sha_ROOMID_airing_mode` exists

---

## 📄 License

MIT License — see [LICENSE](../../../LICENSE) for details.

---

## 🔗 Related

- [Smart Heating Advisor Integration](../../../README.md)
- [Example Automations](../../examples/automations/README.md)
- [Home Assistant Blueprints Documentation](https://www.home-assistant.io/docs/blueprint/)