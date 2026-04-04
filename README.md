# 🌡️ Smart Heating Advisor

[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1.0+-blue.svg)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/MIT)

> AI-powered smart heating that learns your home. Pre-heat every room to exactly the right temperature at exactly the right time — automatically improving every day.

---

## 📋 Table of Contents

- [How It Works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Room Setup](#room-setup)
- [Schedule Naming Convention](#schedule-naming-convention)
- [Blueprint Configuration](#blueprint-configuration)
- [Notification Reference](#notification-reference)
- [Services](#services)
- [Entities](#entities)
- [Architecture](#architecture)
- [Example: Bathroom Setup](#example-bathroom-setup)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)

---

<a id="how-it-works"></a>

## ⚙️ How It Works

Smart Heating Advisor (SHA) combines three things:

1. **HA Schedule helpers** — you define when each room should be warm and at what temperature
2. **InfluxDB history** — SHA reads actual room temperature data to measure real heating performance
3. **Ollama AI** — local AI analyses the data and calibrates the heating rate per room, per day

The SHA blueprint automation controls TRVs directly — pre-heating rooms at exactly the right moment so the target temperature is reached when needed.

```
Your Schedule helpers (e.g. "Morning Shower 26C")
         │
         ▼
SHA Blueprint automation (per room)
  ├── Reads AI-calibrated heating rate from number.sha_ROOM_heating_rate
  ├── Calculates: (target - current) / heating_rate = minutes needed
  ├── Starts pre-heat at exactly the right time
  └── Controls TRVs: heat → maintain → standby → off
         │
         ▼ (records to InfluxDB automatically)
         │
SHA Daily analysis at 02:00 AM
        ├── Reads rooms from SHA internal room registry
  ├── Queries InfluxDB per room (7 days history)
  ├── Reads each room's schedules and target temperatures
  ├── Sends data + weather to Ollama AI
  └── Updates number.sha_ROOM_heating_rate per room
         │
         ▼ (feedback loop — system improves daily)
```

---

<a id="features"></a>

## ✨ Features

| Feature | Description |
|---|---|
| 🤖 **AI heating rate calibration** | Daily per-room calibration using Ollama + InfluxDB history |
| 📅 **Unlimited schedules** | Any number of HA Schedule helpers per room |
| 🌡️ **Per-schedule temperatures** | Target temp encoded in schedule name (e.g. `Morning Shower 26C`) |
| 🏠 **Multi-room support** | Unlimited rooms — each with independent heating rate |
| 🔍 **Registry-based room discovery** | Rooms are persisted in SHA internal registry (no automation file scanning) |
| 🛠️ **Auto helper creation** | Helper entities are created from registered rooms on integration reload |
| 🔥 **Fixed TRV support** | Optional fixed-temp TRVs (towel rails, floor heating) |
| 🏖️ **Vacation mode** | Calendar-based frost protection |
| 🪟 **Window detection** | Pauses heating when windows open |
| ✋ **Manual override** | Auto-resume after configurable duration |
| 🔔 **Smart notifications** | Mobile lifecycle notifications with per-room enable switches |
| 📊 **Weekly reports** | Sunday persistent notification with 30-day analysis |
| 🔄 **Blueprint auto-update** | New SHA versions automatically update the blueprint |

---

<a id="requirements"></a>

## 📦 Requirements

| Requirement | Details |
|---|---|
| Home Assistant | 2024.1.0 or newer |
| [Ollama](https://ollama.ai) | Running locally on your network |
| Ollama model | `phi4` recommended — any model works |
| InfluxDB 2.x | Your HA data must be recorded to InfluxDB |
| HA InfluxDB integration | Configured to record temperature sensors |

---

<a id="installation"></a>

## 🚀 Installation

### Via HACS (recommended)

[![Install via HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bubuplanet&repository=smart-heating-advisor&category=integration)

Click the button above to open HACS directly on the Smart Heating Advisor repository.

Or add manually:
1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/bubuplanet/smart-heating-advisor` as **Integration**
4. Search for **Smart Heating Advisor** and install
5. Restart Home Assistant

### Add the integration

After restarting Home Assistant:

1. Go to **Settings → Devices & Services**
2. Click **+ Add Integration** in the bottom-right corner
3. Search for **Smart Heating Advisor** and select it
4. Complete the 3-step setup wizard — see [Configuration](#configuration) for details

> 💡 SHA only creates a persistent setup notification if there is a setup error.

### Manual

1. Copy `custom_components/smart_heating_advisor/` to your HA config directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → + Add Integration** and search for **Smart Heating Advisor**

---

<a id="configuration"></a>

## ⚙️ Configuration

The setup wizard has 3 steps:

### Step 1 — Ollama

| Field | Example |
|---|---|
| Ollama URL | `http://192.168.1.100:11434` |
| Ollama Model | `phi4` |

SHA tests the connection and verifies the model exists before proceeding.

### Step 2 — InfluxDB

| Field | Example |
|---|---|
| InfluxDB URL | `http://192.168.1.100:8086` |
| InfluxDB API Token | `your-token-here` |
| InfluxDB Organisation | `home_assistant` |
| InfluxDB Bucket | `home_assistant` |

SHA tests the connection before proceeding.

### Step 3 — HA Entities

| Field | Example |
|---|---|
| Weather Entity | `weather.forecast_home` |

### Optional: Customize setup notification text (no Python edits)

SHA notification text is loaded from JSON templates:

- Bundled defaults: `custom_components/smart_heating_advisor/messages.json`
- User override file: `/config/smart_heating_advisor_messages.json`

If `/config/smart_heating_advisor_messages.json` exists, SHA merges it over defaults at startup.
This lets you customize wording without touching Python files.

---

<a id="room-setup"></a>

## 🏠 Room Setup

### Step 1 — Create Schedule helpers

Go to **Settings → Helpers → + Create Helper → Schedule** and create one schedule per heating period:

| Schedule Name | Days | Time | Meaning |
|---|---|---|---|
| `Morning Shower 26C` | Mon–Fri | 06:00–07:00 | Heat bathroom to 26°C by 6AM |
| `Evening Bath 28C` | Mon–Sun | 19:00–20:30 | Heat bathroom to 28°C by 7PM |
| `Office Morning 20C` | Mon–Fri | 08:00–18:00 | Heat office to 20°C by 8AM |

> ⚠️ The temperature **must** be at the end of the name followed by `C` with no space.

### Step 2 — Create an automation from the blueprint

The blueprint is installed automatically when you install SHA. To create a room automation:

1. Go to **Settings → Automations → Blueprints → Smart Heating Advisor**
2. Click **Create Automation**
3. Fill in the form:
   - **Room Name**: e.g. `Bathroom` (used for entities and notifications)
   - **Room Temperature Sensor**: your temperature sensor
   - **Radiator Thermostat**: one or more TRVs
   - **Schedule Helpers**: select the schedules you created in Step 1
4. Save

### Step 3 — Run the automation once (required)

After saving the automation, run it once manually so it can call `smart_heating_advisor.register_room` and store room data in SHA's internal registry.

1. Open the created automation
2. Click **Run**

### Step 4 — Reload SHA integration

After the first room registration run, reload SHA so entities are created from the registry.

1. Go to **Settings → Devices & Services**
2. Open **Smart Heating Advisor**
3. Click **⋮ → Reload**

**That's it.** Repeat for each additional room: create automation, run once, then reload SHA.

> 💡 This registry-first flow is intentional and avoids scanning `automations.yaml` or HA storage files at runtime.

### Changing schedules later (after automation already exists)

If you need to change room timing or targets after initial setup:

1. Go to **Settings → Helpers** and edit existing schedule helpers, or create new ones
2. Go to **Settings → Automations →** open your room automation created from SHA blueprint
3. Click **Edit in UI** and update the **Schedule Helpers** selection
4. Save the automation
5. Run the automation once manually
6. Reload SHA integration from **Settings → Devices & Services → Smart Heating Advisor → ⋮ → Reload**

Why this is needed:
- The manual run re-sends room data through `smart_heating_advisor.register_room`
- Reload makes SHA re-create/use entities from the latest registry data

---

<a id="schedule-naming-convention"></a>

## 📝 Schedule Naming Convention

Name each **HA Schedule helper** with the target temperature at the end:

| Schedule Name | Target Temp | Result |
|---|---|---|
| `Morning Shower 26C` | 26°C | ✅ |
| `Evening Bath 28.5C` | 28.5°C | ✅ |
| `Weekend 20C` | 20°C | ✅ |
| `Morning Shower` | _(no temp)_ | ⚠️ Uses fallback temp |
| `Shower 26 C` | _(no match)_ | ⚠️ Uses fallback temp (space before C) |

> The temperature must be at the **end** of the name, immediately followed by `C` with no space.

---

<a id="blueprint-configuration"></a>

## 🎛️ Blueprint Configuration

### 🏠 Room Section

| Field | Description | Example |
|---|---|---|
| **Room Name** | Friendly name — used in notifications and to derive entity IDs | `Bathroom` |
| **Room Temperature Sensor** | Sensor measuring actual room temperature | `sensor.bathroom_thermostat_temperature` |
| **Radiator Thermostat** | One or more TRVs following the schedule temp | `climate.bathroom_radiator` |
| **Fixed Radiator Thermostat** | Optional TRVs always heating to a fixed temp | `climate.bathroom_heated_towel_rail` |
| **Fixed Radiator Temperature** | Temperature for fixed TRVs when active | `30°C` |

### 📅 Schedules Section

| Field | Description | Example |
|---|---|---|
| **Schedule Helpers** | Select one or more HA Schedule helpers | `Morning Shower 26C`, `Evening Bath 28C` |
| **Schedule Fallback Temperature** | Used when no temp found in schedule name | `21°C` |

### 🌡️ Default Section _(collapsed)_

| Field | Description | Default |
|---|---|---|
| **Default Heating Mode** | Off or Heat when no schedule is active | `Off` |
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
| **Notify target reached / schedule start** | When comfort schedule starts | `true` |
| **Notify standby starts** | Once when no schedule active | `true` |
| **Notify window open/close** | On window state change | `true` |
| **Notify override active/resumed** | On manual TRV change + resume | `true` |
| **Notify daily report** | Daily analysis summary notification | `true` |
| **Notify weekly report** | Weekly analysis summary notification | `true` |
| **Window Open Header / Body Template** | Custom text for window-open notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Window Closed Header / Body Template** | Custom text for window-closed notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Schedule Started Header / Body Template** | Custom text for schedule-started notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Schedule Finished Header / Body Template** | Custom text for schedule-finished notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Pre-heat Started Header / Body Template** | Custom text for pre-heat notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Target Reached Header / Body Template** | Custom text for target-reached notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Standby Header / Body Template** | Custom text for standby notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Override Active Header / Body Template** | Custom text for override-active notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |
| **Override Ended Header / Body Template** | Custom text for override-ended notification | `{{ room_name }} - {{ type }}` / `{{ room_name }} - {{ type }} : {{ status }}` |

Supported template variables:
- Common (all templates): `{{ room_name }}`, `{{ type }}`, `{{ status }}`
- Window open/closed body: `{{ room_temp }}`
- Schedule started body: `{{ room_temp }}`, `{{ schedule_name }}`
- Schedule finished body: `{{ room_temp }}`, `{{ default_hvac_mode }}`, `{{ default_temp }}`
- Pre-heat started body: `{{ room_temp }}`, `{{ target_temp }}`, `{{ schedule_name }}`, `{{ eta_minutes }}`
- Target reached body: `{{ room_temp }}`, `{{ schedule_name }}`, `{{ time }}`
- Standby body: `{{ room_temp }}`, `{{ default_hvac_mode }}`, `{{ default_temp }}`
- Override active body: `{{ device_name }}`, `{{ override_minutes }}`, `{{ resume_time }}`, `{{ room_temp }}`
- Override ended body: `{{ room_temp }}`

---

<a id="notification-reference"></a>

## 🔔 Notification Reference

| Notification | Title | When | Fires |
|---|---|---|---|
| Pre-heat | 🌅 Room — Pre-heat Started | Pre-heat begins | Once per schedule event |
| Schedule started | ▶️ Room — Schedule Started | Comfort schedule turns ON | Once per schedule start |
| Schedule finished | ⏹️ Room — Schedule Finished | Comfort schedule turns OFF | Once per schedule end |
| Window open | 🪟 Room — Window Open | Window opens (after delay) | Once per opening |
| Window closed | 🪟 Room — Window Closed | All windows close | Once per closing |
| Override active | ✋ Room — Override Active | Manual TRV change detected | On each manual change |
| Override ended | 🔄 Room — Heating Resumed | Override expires | On each resume |
| Daily report | 📅 Room — Daily Heating Report | Daily analysis completes | Once per room per run |
| Weekly report | 📊 Room — Weekly Heating Report | Weekly analysis completes | Once per room per run |

---

<a id="services"></a>

## 🔧 Services

| Service | Description |
|---|---|
| `smart_heating_advisor.run_daily_analysis` | Manually trigger daily AI analysis for all rooms |
| `smart_heating_advisor.run_weekly_analysis` | Manually trigger weekly report for all rooms |
| `smart_heating_advisor.start_override` | Start a timed manual override for a room |
| `smart_heating_advisor.register_room` | Register or update a room in SHA internal room registry (normally called by blueprint) |

---

<a id="entities"></a>

## 📡 Entities

SHA creates entities per discovered room, all grouped under a device named **SHA — {Room Name}**.

### Sensors (read-only)

| Entity | Description | Unit |
|---|---|---|
| `sensor.sha_ROOM_heating_rate` | Current AI-calibrated heating rate | °C/min |
| `sensor.sha_ROOM_last_analysis` | Timestamp of last AI analysis | datetime |
| `sensor.sha_ROOM_confidence` | AI confidence level | high/medium/low |
| `sensor.sha_ROOM_weekly_report` | Last weekly report summary | text |

### Number (writable)

| Entity | Description | Range |
|---|---|---|
| `number.sha_ROOM_heating_rate` | Heating rate — updated daily by AI, adjustable manually | 0.05–0.30 °C/min |

### Switches (state flags)

| Entity | Description |
|---|---|
| `switch.sha_ROOM_airing_mode` | Window open — heating paused |
| `switch.sha_ROOM_preheat_notifications_enabled` | Enable/disable pre-heat notifications for this room |
| `switch.sha_ROOM_target_notifications_enabled` | Enable/disable schedule start notifications for this room |
| `switch.sha_ROOM_standby_notifications_enabled` | Enable/disable schedule finish/standby notifications for this room |
| `switch.sha_ROOM_window_notifications_enabled` | Enable/disable window open/close notifications for this room |
| `switch.sha_ROOM_override_notifications_enabled` | Enable/disable override active/resumed notifications for this room |
| `switch.sha_ROOM_preheat_notified` | Pre-heat notification sent this cycle |
| `switch.sha_ROOM_target_notified` | Schedule start notification sent this cycle |
| `switch.sha_ROOM_standby_notified` | Schedule finish/standby notification sent this cycle |
| `switch.sha_ROOM_vacation_notified` | Vacation notification sent this cycle |
| `switch.sha_ROOM_override` | Manual override active |

Replace `ROOM` with your room ID — room name in lowercase with underscores:

| Room Name | Room ID |
|---|---|
| `Bathroom` | `bathroom` |
| `Living Room` | `living_room` |
| `Alessio's Bedroom` | `alessios_bedroom` |

---

<a id="architecture"></a>

## 🏗️ Architecture

### 1 — Installation

```
HACS installs SHA integration
        │
        ▼
SHA copies blueprint to /config/blueprints/automation/smart_heating_advisor/
        │
        ▼
User runs config flow (Ollama + InfluxDB + Weather)
        │
        ▼
SHA is ready
```

### 2 — Room setup (per room)

```
User creates Schedule helpers ("Morning Shower 26C")
        │
        ▼
User creates automation from SHA blueprint
Blueprint run calls: smart_heating_advisor.register_room
SHA stores: room_name, temperature_sensor, schedules in internal registry
        │
        ▼
SHA reloads → discovers rooms from internal registry
        │
        ▼
SHA creates helper entities per room:
  number.sha_ROOM_heating_rate      (AI-calibrated, default 0.15°C/min)
  switch.sha_ROOM_override          (manual override state)
  switch.sha_ROOM_airing_mode       (window open tracking)
  switch.sha_ROOM_preheat_notified  (notification flags)
  switch.sha_ROOM_target_notified
  switch.sha_ROOM_standby_notified
  switch.sha_ROOM_vacation_notified
        │
        ▼
Automation runs normally ✅
```

### 3 — Daily AI analysis (02:00 AM)

```
SHA coordinator wakes up
        │
        ▼
discover_rooms() — reads all registered rooms from SHA internal registry
        │
        ├── Bathroom  → sensor.bathroom_temp + [schedule.morning_shower, ...]
        ├── Office    → sensor.office_temp   + [schedule.office_morning]
        └── Bedroom   → sensor.bedroom_temp  + [schedule.bedroom_evening]
        │
        ▼ (for each room)
        │
        ├── Query InfluxDB (7 days temp history)
        ├── Read schedule names → extract target temperatures
        ├── Read weather (outside temp + tomorrow forecast)
        ├── Analyse heating sessions (was target reached? at what rate?)
        ├── Build AI prompt with room data + weather + session stats
        ├── Call Ollama → get new heating_rate + reasoning
        └── Update number.sha_ROOM_heating_rate directly
        │
        ▼
Mobile notification per room with new rate + reasoning
```

### 4 — Blueprint heating loop (every 5 min)

```
Read heating rate from number.sha_ROOM_heating_rate
        │
        ▼
Check active schedule → extract target temp from name
        │
        ▼
Calculate pre-heat start:
  minutes_needed = (target_temp - room_temp) / heating_rate
  if minutes_to_schedule_start <= minutes_needed → start heating
        │
        ▼
Set TRV temperature + mode (heat / off)
        │
        ▼
Send notifications (once per event via flag switches)
```

### 5 — Self-improving feedback loop

```
Room heats up → data recorded in InfluxDB
        │
        ▼
Next day 02:00 AM → SHA analyses session
  Was target reached? Was pre-heat too early / too late?
        │
        ▼
Heating rate updated → pre-heat timing improves
        │
        ▼
Repeats daily — system gets smarter over time
```

---

<a id="example-bathroom-setup"></a>

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
| Window Sensors | `binary_sensor.bathroom_window` |
| Vacation Calendar | `calendar.home` |
| Vacation Keyword | `vacation` |
| Override Duration | `120 min` |

### Typical day timeline

```
05:10  🌅 Pre-heat starts for "Morning Shower 26C"
       │   Room: 18°C → target 26°C, Est. 53 min
       │   (notification fires once)
       │
06:00  Schedule "Morning Shower 26C" turns ON
       │   Notification flags reset for next cycle
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
20:30  Schedule turns OFF — 🌡️ Standby
```

---

<a id="troubleshooting"></a>

## 🔍 Troubleshooting

### SHA not finding my rooms

SHA discovers rooms from its internal room registry.

Check:
1. Is the automation created from the **Smart Heating Advisor** blueprint?
2. Does the automation have a **Room Name** and **Temperature Sensor** set?
3. Run the automation once manually after creation (this registers the room).
4. Reload SHA via **Settings → Devices & Services → Smart Heating Advisor → ⋮ → Reload**.
5. Check logs for registry lines:
        - `sha.register_room payload`
        - `Room registry updated`
        - `Room discovery (registry)`

### How to enable verbose debug mode

1. Go to **Settings → Devices & Services → Smart Heating Advisor → Configure**
2. Enable **Debug logging** in options
3. Reload SHA integration
4. Re-run one room automation
5. Review logs for detailed traces (registry load/register/discovery, platform entity preparation, service payloads)

### Understanding HA automation trace lines

Example log line:

`[homeassistant.components.automation.testroom_smart_heating_advisor] Testroom - Smart Heating Advisor: Choose at step 2: default: Choose at step 1: choice 1: Executing step call service`

This is Home Assistant's built-in automation trace path, not an error. It means:

1. `Choose at step 2: default`: The outer `choose` block did not match earlier branches, so default branch is running
2. `Choose at step 1: choice 1`: Inside that default branch, a nested `choose` selected its first matching option
3. `Executing step call service`: The selected branch is currently calling a service (for example `climate.set_temperature` or `smart_heating_advisor.register_room`)

How to inspect it clearly:

1. Open **Settings → Automations & Scenes →** your room automation
2. Click **Traces**
3. Open the latest run and expand the `choose` nodes to see exactly which conditions were true/false

### Debug: verify created states in Developer Tools

If setup appears correct but behavior is wrong, verify entity states directly:

1. Go to **Developer Tools → States**
2. Check room helper entities exist:
        - `number.sha_ROOM_heating_rate`
        - `switch.sha_ROOM_override`
        - `switch.sha_ROOM_airing_mode`
        - `switch.sha_ROOM_preheat_notified`
        - `switch.sha_ROOM_target_notified`
        - `switch.sha_ROOM_standby_notified`
        - `switch.sha_ROOM_vacation_notified`
3. Check sensor entities exist:
        - `sensor.sha_ROOM_heating_rate`
        - `sensor.sha_ROOM_last_analysis`
        - `sensor.sha_ROOM_confidence`
        - `sensor.sha_ROOM_weekly_report`
4. If entities are missing:
        - Run room automation once
        - Reload SHA integration
        - Re-check states

### Pre-heat starts too late or too early

`number.sha_ROOM_heating_rate` controls timing. SHA calibrates this daily but you can also adjust manually:
- **Too late** (room not warm enough at schedule time) → increase the value (e.g. `0.10` → `0.13`)
- **Too early** (room overheating before schedule) → decrease the value

### Sensors not updating

Trigger a manual analysis via **Developer Tools → Actions → `smart_heating_advisor.run_daily_analysis`**.

### InfluxDB query returning no data

SHA queries InfluxDB with `_field == "value"`, `_measurement == "°C"`, and `entity_id` without the `sensor.` prefix.

Verify in the InfluxDB Data Explorer:
```flux
from(bucket: "home_assistant")
  |> range(start: -7d)
  |> filter(fn: (r) => r["_measurement"] == "°C")
  |> filter(fn: (r) => r["_field"] == "value")
  |> filter(fn: (r) => r["entity_id"] =~ /your_room/)
  |> limit(n: 10)
```

### Blueprint not showing in HA

The blueprint is auto-installed by SHA on setup. If it's missing:
1. Check `/config/blueprints/automation/smart_heating_advisor/sha_unified_heating.yaml` exists
2. Go to **Settings → Automations → Blueprints** and click **Reload blueprints**

### Notifications firing every 5 minutes

Check these first:
- Notification-enable switches are on for the room (for example `switch.sha_ROOM_preheat_notifications_enabled`)
- Notification flag switches (`switch.sha_ROOM_preheat_notified`, etc.) are visible and changing state
- The `schedule_changed` trigger is firing when schedules turn on/off

### Override not working

- Verify `switch.sha_ROOM_override` exists in HA
- Check the HA logs for `sha.start_override` calls

---

<a id="roadmap"></a>

## 🗺️ Roadmap

| # | Feature |
|---|---|
| 1 | HA local recorder support (remove InfluxDB dependency) |
| 2 | Presence-based heating (heat only when someone is home) |
| 3 | Solar integration (shift heating load to free solar energy) |
| 4 | Energy consumption monitoring (prove the savings) |
| 5 | Multiple AI engine support (Claude, Gemini, Mistral) |
| 6 | Smart standby optimization (lower standby on mild days) |
| 7 | Lovelace card |
| 8 | AI-suggested schedule fallback temperatures |

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

Issues and pull requests welcome at [github.com/bubuplanet/smart-heating-advisor](https://github.com/bubuplanet/smart-heating-advisor).
