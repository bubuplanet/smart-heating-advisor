# 🌡️ Smart Heating Advisor

A Home Assistant custom component that uses local AI (Ollama) and historical temperature data to automatically optimize your home heating — room by room — saving energy and reducing your bills.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.6%2B-blue.svg)](https://www.home-assistant.io/)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## 🎯 Goal

Most smart heating systems heat on a fixed schedule regardless of actual conditions. Smart Heating Advisor takes a different approach:

- **Learns** your heating system's actual performance from historical data
- **Adapts** the pre-heat start time based on how cold it is right now
- **Considers** outside temperature and weather forecasts
- **Reports** weekly on heating performance and energy trends
- **Saves energy** by heating only as much as needed, only when needed

---

## ✨ Features

- 🤖 **AI-powered analysis** using local Ollama (phi4, llama3, mistral, etc.)
- 📊 **InfluxDB integration** for historical temperature data analysis
- 🌡️ **Auto-calibrating heating rate** — learns how fast your radiators actually heat
- 📅 **Daily analysis** — adjusts heating rate every morning at 2AM
- 📋 **Weekly report** — persistent HA notification with 30-day performance summary
- 🪟 **Window detection** — stops heating when windows are open
- 🏖️ **Vacation mode** — switches to frost protection via Google Calendar
- 🖐️ **Manual override** — pauses automation for 2 hours when you change thermostat
- ☀️ **Weather-aware** — considers tomorrow's forecast in heating decisions
- 🔔 **Smart notifications** — notified when modes change, targets are reached

---

## 📋 Requirements

- Home Assistant 2024.6+
- [HACS](https://hacs.xyz/) installed
- [Ollama](https://ollama.ai/) running locally on your network
- InfluxDB 2.x with Home Assistant data
- Zigbee TRV devices (tested with Sonoff TRVZB)
- Temperature sensor (tested with Sonoff SNZB-02D)

---

## 🚀 Installation

### Via HACS (recommended)
1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click **⋮ → Custom repositories**
4. Add `https://github.com/yourusername/smart_heating_advisor`
5. Select category **Integration**
6. Click **Download**
7. Restart Home Assistant

### Manual
1. Copy `custom_components/smart_heating_advisor` to your HA config folder
2. Restart Home Assistant

---

## ⚙️ Configuration

1. Go to **Settings → Devices & Services → + Add Integration**
2. Search for **Smart Heating Advisor**
3. Follow the 3-step setup wizard:

### Step 1 — Ollama
| Field | Example |
|---|---|
| Ollama URL | `http://192.168.1.100:11434` |
| Model name | `phi4` |

### Step 2 — InfluxDB
| Field | Example |
|---|---|
| InfluxDB URL | `http://192.168.1.100:8086` |
| API Token | your InfluxDB token |
| Organisation | `home_assistant` |
| Bucket | `home_assistant` |

### Step 3 — Entities
| Field | Example |
|---|---|
| Temperature sensor | `sensor.bathroom_thermostat_temperature` |
| Heating rate helper | `input_number.bathroom_heating_rate` |
| Weather entity | `weather.forecast_home` |
| Target temperature | `26` |
| Target time | `06:00` |

---

## 🔧 Required Helpers

Create these helpers in **Settings → Devices & Services → Helpers**:

| Name | Type | Entity ID | Settings |
|---|---|---|---|
| `bathroom_heating_rate` | Number | `input_number.bathroom_heating_rate` | Min: 0.05, Max: 0.30, Step: 0.01, Initial: 0.15 |
| `bathroom_heating_override` | Toggle | `input_boolean.bathroom_heating_override` | — |
| `bathroom_automation_running` | Toggle | `input_boolean.bathroom_automation_running` | — |
| `bathroom_room_target_notified` | Toggle | `input_boolean.bathroom_room_target_notified` | — |
| `bathroom_towel_target_notified` | Toggle | `input_boolean.bathroom_towel_target_notified` | — |
| `bathroom_mode_preheat` | Toggle | `input_boolean.bathroom_mode_preheat` | — |
| `bathroom_mode_maintain` | Toggle | `input_boolean.bathroom_mode_maintain` | — |
| `bathroom_mode_standby` | Toggle | `input_boolean.bathroom_mode_standby` | — |
| `bathroom_window_notified` | Toggle | `input_boolean.bathroom_window_notified` | — |
| `bathroom_override_until` | Date/Time | `input_datetime.bathroom_override_until` | Date and time |

---

## 📡 Sensors

The component exposes 4 sensors to Home Assistant:

| Sensor | Description |
|---|---|
| `sensor.sha_bathroom_heating_rate` | Current AI-calculated heating rate (°C/min) |
| `sensor.sha_bathroom_last_analysis` | Timestamp of last daily analysis |
| `sensor.sha_bathroom_confidence` | AI confidence level (high/medium/low) |
| `sensor.sha_bathroom_weekly_report` | Latest weekly report summary |

---

## 🛠️ Services

| Service | Description |
|---|---|
| `smart_heating_advisor.run_daily_analysis` | Manually trigger daily analysis |
| `smart_heating_advisor.run_weekly_analysis` | Manually trigger weekly report |

---

## 📅 Schedule

| Time | Action |
|---|---|
| **02:00 AM daily** | Daily analysis — adjusts heating rate |
| **01:00 AM Sunday** | Weekly report — 30-day performance summary |
| **00:00 AM daily** | Reset all flags |
| **03:00 AM daily** | Reset all flags (pre-heat preparation) |

---

## 🏗️ Architecture
```
smart_heating_advisor/
├── __init__.py        # Component setup + service registration
├── manifest.json      # HACS metadata
├── const.py           # Constants and defaults
├── coordinator.py     # Core orchestration logic
├── ollama.py          # Ollama API client
├── analyzer.py        # Heating session detection + prompt building
├── sensor.py          # HA sensor entities
├── config_flow.py     # UI setup wizard
├── services.yaml      # Service descriptions
└── strings.json       # UI translations
```

---

## 🤖 How the AI works

### Daily analysis (7 days data)
```
1. Query InfluxDB for last 7 days of room temperature
2. Detect heating sessions (periods of consistent temperature rise)
3. Calculate: success rate, avg heating rate, avg start time
4. Fetch weather: current outside temp + tomorrow forecast
5. Send to Ollama with structured prompt
6. Parse JSON response → extract new heating_rate
7. Update input_number.bathroom_heating_rate
8. Send mobile notification with reasoning
```

### Weekly report (30 days data)
```
1. Query InfluxDB for last 30 days
2. Deeper pattern analysis
3. Send to Ollama for comprehensive assessment
4. Create persistent HA notification with:
   - 30-day performance summary
   - Suggested rate (not applied automatically)
   - Energy trends
   - AI reasoning
```

---

## 🌡️ Heating Rate Explained

The `heating_rate` (°C/min) controls when pre-heating starts:
```
minutes_needed = (target_temp - current_temp) / heating_rate
pre_heat_start = target_time - minutes_needed - 30min_buffer
```

**Example:** Room at 17°C, target 26°C at 6AM, rate 0.13°C/min:
```
minutes_needed = (26 - 17) / 0.13 = 69 minutes
pre_heat_start = 06:00 - 69min - 30min = 03:51 AM
```

The AI adjusts this rate daily based on whether targets are being reached on time.

---

## 📊 Roadmap

- [ ] Multi-room support with per-room configuration
- [ ] HA local recorder as data source (no InfluxDB required)
- [ ] Multiple AI engine support (Claude, Gemini, Mistral)
- [ ] Scenario-based heating via Lovelace UI card
- [ ] Presence-based heating (skip when nobody home)
- [ ] Solar production integration (heat when solar is free)
- [ ] Energy monitoring and consumption reporting
- [ ] Smart standby optimization (lower temp on mild days)
- [ ] Automation generation from component UI
- [ ] Window/door sensor integration

---

## 🧪 Tested With

| Device | Entity |
|---|---|
| Sonoff TRVZB | `climate.bathroom_radiator` |
| Sonoff TRVZB | `climate.bathroom_heated_towel_rail` |
| Sonoff SNZB-02D | `sensor.bathroom_thermostat_temperature` |
| Aqara SNZB-04P | `binary_sensor.bathroom_window` |
| Ollama phi4 | local AI model |
| InfluxDB 2.x | historical data store |

---

## 🤝 Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👤 Author

Built with ❤️ for Home Assistant by Jeronimo

_Inspired by the idea that smart heating should learn from your home, not the other way around._