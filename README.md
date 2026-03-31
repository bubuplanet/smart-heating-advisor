# 🌡️ Smart Heating Advisor

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1.0+-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> AI-powered smart heating that learns your home. Pre-heat every room to exactly the right temperature at exactly the right time — automatically improving every day.

---

## 📋 Table of Contents

- [How It Works](#-how-it-works)
- [Features](#-features)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Room Setup](#-room-setup)
- [Schedule Naming Convention](#-schedule-naming-convention)
- [Services](#-services)
- [Sensors](#-sensors)
- [Architecture](#-architecture)
- [Troubleshooting](#-troubleshooting)
- [Roadmap](#-roadmap)

---

## ⚙️ How It Works

Smart Heating Advisor (SHA) combines three things:

1. **HA Schedule helpers** — user defines when each room should be warm and at what temperature
2. **InfluxDB history** — SHA reads actual room temperature data to measure real heating performance
3. **Ollama AI** — local AI analyses the data and calibrates the heating rate per room, per day

The SHA blueprint automation controls TRVs directly — pre-heating rooms at exactly the right moment so the target temperature is reached when needed, not before and not after.

```
Your Schedule helpers (e.g. "Morning Shower 26C")
         │
         ▼
SHA Blueprint automation (per room)
  ├── Reads AI-calibrated heating rate from input_number
  ├── Calculates: (target - current) / heating_rate = minutes needed
  ├── Starts pre-heat at exactly the right time
  └── Controls TRVs: heat → maintain → standby → off
         │
         ▼ (records to InfluxDB automatically)
         │
SHA Daily analysis at 02:00 AM
  ├── Discovers rooms from blueprint automations (sha: tag)
  ├── Queries InfluxDB per room (7 days history)
  ├── Reads each room's schedules and target temperatures
  ├── Sends data + weather to Ollama AI
  └── Updates input_number.sha_ROOM_heating_rate per room
         │
         ▼ (feedback loop — system improves daily)
```

---

## ✨ Features

| Feature | Description |
|---|---|
| 🤖 **AI heating rate calibration** | Daily per-room calibration using Ollama + InfluxDB history |
| 📅 **Unlimited schedules** | Any number of HA Schedule helpers per room |
| 🌡️ **Per-schedule temperatures** | Target temp encoded in schedule name (e.g. `Morning Shower 26C`) |
| 🏠 **Multi-room support** | Unlimited rooms — each with independent heating rate |
| 🔍 **Auto room discovery** | Finds rooms from blueprint automations — zero manual config |
| 🛠️ **Auto helper creation** | Creates all HA helpers automatically on first use per room |
| 🔥 **Fixed TRV support** | Optional fixed-temp TRVs (towel rails, floor heating) |
| 🏖️ **Vacation mode** | Calendar-based frost protection |
| 🪟 **Window detection** | Pauses heating when windows open |
| ✋ **Manual override** | Auto-resume via HA Timer |
| 🔔 **Smart notifications** | Each notification fires once per event — no spam |
| 📊 **Weekly reports** | Sunday persistent notification with 30-day analysis |
| 🔄 **Blueprint auto-update** | New SHA versions automatically update the blueprint |

---

## 📦 Requirements

| Requirement | Details |
|---|---|
| Home Assistant | 2024.1.0 or newer |
| [Ollama](https://ollama.ai) | Running locally on your network |
| Ollama model | `phi4` recommended — any model works |
| InfluxDB 2.x | Your HA data must be recorded to InfluxDB |
| HA InfluxDB integration | Configured to record temperature sensors |

---

## 🚀 Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Click **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/bubuplanet/smart-heating-advisor` as **Integration**
4. Search for **Smart Heating Advisor** and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/smart_heating_advisor/` to your HA config directory
2. Restart Home Assistant

---

## ⚙️ Configuration

After installation go to **Settings → Devices & Services → + Add Integration** and search for **Smart Heating Advisor**.

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

---

## 🏠 Room Setup

After installing SHA, set up each room in 3 steps:

### Step 1 — Create Schedule helpers

Go to **Settings → Helpers → + Create Helper → Schedule** and create one schedule per heating period:

| Schedule Name | Days | Time | Meaning |
|---|---|---|---|
| `Morning Shower 26C` | Mon–Fri | 06:00–07:00 | Heat bathroom to 26°C by 6AM |
| `Evening Bath 28C` | Mon–Sun | 19:00–20:30 | Heat bathroom to 28°C by 7PM |
| `Office Morning 20C` | Mon–Fri | 08:00–18:00 | Heat office to 20°C by 8AM |

> ⚠️ The temperature **must** be at the end of the name followed by `C` with no space.
> ✅ `Morning Shower 26C` → reads 26°C
> ❌ `Morning Shower 26 C` → no match, uses fallback temperature

### Step 2 — Import the blueprint

[![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fbubuplanet%2Fsmart-heating-advisor%2Fmain%2Fblueprints%2Fautomation%2Fsmart_heating_advisor%2Fsha_unified_heating.yaml)

Or go to **Settings → Automations → Blueprints** — the blueprint is installed automatically by SHA.

### Step 3 — Create an automation from the blueprint

1. Go to **Settings → Automations → Blueprints → Smart Heating Advisor**
2. Click **Create Automation**
3. Fill in the form:
   - **Room Name**: e.g. `Bathroom` (used for helpers and notifications)
   - **Room Temperature Sensor**: your temperature sensor
   - **Radiator Thermostat**: one or more TRVs
   - **Schedule Helpers**: select the schedules you created in Step 1
4. Save

**That's it.** On the first automation trigger SHA automatically creates all required helpers for this room. Repeat for each additional room.

---

## 📝 Schedule Naming Convention

| Name | Temp extracted | Result |
|---|---|---|
| `Morning Shower 26C` | 26°C | ✅ |
| `Evening Bath 28.5C` | 28.5°C | ✅ |
| `Weekend 20C` | 20°C | ✅ |
| `Morning Shower` | none | ⚠️ Uses fallback temp |
| `Shower 26 C` | none | ⚠️ Uses fallback temp (space before C) |

---

## 🔧 Services

| Service | Description |
|---|---|
| `sha.setup_room` | Creates helpers for a room. Called automatically by blueprint. |
| `sha.run_daily_analysis` | Manually trigger daily AI analysis for all rooms. |
| `sha.run_weekly_analysis` | Manually trigger weekly report for all rooms. |

---

## 📡 Sensors

SHA creates 4 sensors per discovered room:

| Sensor | Description | Unit |
|---|---|---|
| `sensor.sha_ROOM_heating_rate` | AI-calibrated heating rate | °C/min |
| `sensor.sha_ROOM_last_analysis` | Timestamp of last analysis | datetime |
| `sensor.sha_ROOM_confidence` | AI confidence level | high/medium/low |
| `sensor.sha_ROOM_weekly_report` | Last weekly report summary | text |

---

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
SHA is ready — no helpers created yet
```

### 2 — Room setup (per room)

```
User creates Schedule helpers ("Morning Shower 26C")
        │
        ▼
User creates automation from SHA blueprint
Blueprint stores: room_name, temperature_sensor, schedules
in the automation's blueprint_inputs config
        │
        ▼
Automation first trigger (5-min loop)
        │
        ▼
Blueprint calls sha.setup_room("Bathroom")
        │
        ▼
SHA creates 6 helpers (skips existing):
  input_number.sha_bathroom_heating_rate
  timer.sha_bathroom_override
  input_boolean.sha_bathroom_automation_running
  input_boolean.sha_bathroom_airing_mode
  input_boolean.sha_bathroom_preheat_notified
  input_boolean.sha_bathroom_target_notified
  input_boolean.sha_bathroom_standby_notified
  input_boolean.sha_bathroom_vacation_notified
        │
        ▼
Automation runs normally ✅
```

### 3 — Daily AI analysis (02:00 AM)

```
SHA coordinator wakes up
        │
        ▼
discover_rooms() — reads all SHA blueprint automation configs
        │
        ├── Bathroom  → sensor.bathroom_temp + [schedule.morning_shower, schedule.evening_bath]
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
        └── Update input_number.sha_ROOM_heating_rate
        │
        ▼
Mobile notification per room with new rate + reasoning
```

### 4 — Blueprint heating loop (every 5 min)

```
Read heating rate from input_number.sha_ROOM_heating_rate
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
Send notifications (once per event via flag helpers)
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

## 🔍 Troubleshooting

### SHA not finding my rooms

SHA discovers rooms by reading the blueprint input values from all automations using the `sha_unified_heating` blueprint. It looks for `room_name`, `temperature_sensor` and `schedules` inputs.

Check:
1. Is the automation created from the **Smart Heating Advisor** blueprint?
2. Does the automation have a **Room Name** and **Temperature Sensor** set?
3. Try triggering a manual analysis via **Developer Tools → Actions → `smart_heating_advisor.run_daily_analysis`** and check the HA logs for `Discovered SHA room`.

### Pre-heat starts too late or too early

The heating rate `input_number.sha_ROOM_heating_rate` controls timing. SHA calibrates this daily but you can also adjust manually:
- **Too late** (room not warm enough) → increase the value (e.g. `0.10` → `0.13`)
- **Too early** (room overheating before schedule) → decrease the value

### Sensors not updating

Trigger a manual analysis via **Developer Tools → Actions → `smart_heating_advisor.run_daily_analysis`**.

### InfluxDB query returning no data

SHA queries InfluxDB with:
- `_field == "value"`
- `_measurement == "°C"`
- `entity_id == "your_sensor_id"` (without `sensor.` prefix)

Verify your data in the InfluxDB Data Explorer using this Flux query:
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

---

## 🗺️ Roadmap

| # | Feature |
|---|---|
| 1 | HA local recorder support (remove InfluxDB dependency) |
| 2 | Presence-based heating (heat only when someone is home) |
| 3 | Solar integration (shift heating load to free solar energy) |
| 4 | Energy consumption monitoring (prove the savings) |
| 5 | Multiple AI engine support (Claude, Gemini, Mistral) |
| 6 | Smart standby optimization (lower standby on mild days) |
| 7 | Scenario-based Lovelace card |
| 8 | AI-suggested schedule fallback temperatures |

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

Issues and pull requests welcome at [github.com/bubuplanet/smart-heating-advisor](https://github.com/bubuplanet/smart-heating-advisor).
