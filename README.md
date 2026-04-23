# Smart Heating Advisor

[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.4+-blue.svg)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/MIT)

Smart Heating Advisor (SHA) is a Home Assistant custom integration that uses AI to learn how your home heats and automatically adjusts radiator settings to reach the right temperature at the right time.

SHA analyses your heating sessions every day using a local Ollama AI and InfluxDB history. It adjusts each room's heating rate and TRV setpoints so rooms reach their target temperature exactly when your schedules start — improving automatically over time.

---

## Features

- **AI daily analysis** — SHA analyses heating sessions every day and adjusts radiator setpoints and pre-heat timing automatically
- **Weekly report** — plain language performance report every Sunday flagging issues per room
- **Per-room configuration** — each room has its own temperature sensor, radiator thermostats and schedules
- **Per-schedule target temperatures** — set in the room wizard, no naming convention needed
- **Vacation mode** — manual toggle or automatic date-range activation with frost / eco / off modes
- **Window detection** — optional window sensors pause heating automatically
- **Manual override** — temporarily pause SHA for a configured duration
- **Humidity monitoring** — optional humidity sensor detects shower or cooking activity and adjusts heating

---

## Requirements

- Home Assistant 2026.4 or later
- [Ollama](https://ollama.ai) instance with a model (e.g. `phi4`)
- [InfluxDB v2](https://www.influxdata.com/) instance with Home Assistant data
- TRV thermostats integrated in Home Assistant (Zigbee, Z-Wave, etc.)

---

## Installation

### Via HACS

1. Add this repository as a custom repository in HACS
2. Install **Smart Heating Advisor**
3. Restart Home Assistant

### Manual

Copy the `custom_components/smart_heating_advisor` directory to your `/config/custom_components/` directory and restart Home Assistant.

### Add the integration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Smart Heating Advisor**
3. Complete the 3-step setup wizard:
   - **Step 1** — Ollama URL, model name, optional debug logging toggle
   - **Step 2** — InfluxDB URL, token, organisation and bucket
   - **Step 3** — Weather entity and optional dedicated outside temperature sensor

---

## Adding Rooms

1. On the integration card click **+ Add Room**
2. Complete the 4-step room wizard:
   - **Step 1** — Room name, temperature sensor, radiator thermostats, optional fixed TRVs, manual override settings, humidity monitoring
   - **Step 2** — Schedule helpers and comfort temperature
   - **Step 3** — Window sensors and airing mode
   - **Step 4** — Review and confirm
3. SHA creates and enables the automation **SHA — {room_name}** automatically — no further setup needed

To edit a room later: integration card → room name → **Reconfigure**

---

## Schedules

1. Create schedule helpers in **Settings → Helpers → Add Helper → Schedule**
2. Define the time windows when SHA should heat each room
3. Add schedules to your room via the room wizard or room reconfigure
4. Set a target temperature per schedule directly in the wizard — no naming convention needed

---

## Vacation Mode

1. On the integration card click **+ Vacation**
2. Enable the manual toggle **or** set a start and end date
3. When a date range is set SHA activates vacation automatically when today falls within the range
4. Outside the date range the manual toggle controls vacation

Vacation modes: **Frost protection (7°C)**, **Eco (15°C)**, **Off (no heating)**

---

## Entities

Each room gets the following entities (replace `{room}` with the room name in snake_case):

| Entity | Type | Description |
|---|---|---|
| `number.sha_{room}_heating_rate` | Number | AI-calibrated heating rate (°C/min) |
| `number.sha_{room}_trv_setpoint` | Number | TRV temperature setpoint when heating |
| `number.sha_{room}_comfort_temp` | Number | Temperature maintained when no schedule is active |
| `switch.sha_{room}_airing_mode` | Switch | Manually trigger an airing pause |
| `switch.sha_{room}_override` | Switch | Temporarily pause SHA for this room |
| `binary_sensor.sha_{room}_window_open` | Binary sensor | Window open state (only created when sensors configured) |

Global:

| Entity | Description |
|---|---|
| `binary_sensor.sha_vacation` | Vacation mode active state |

---

## Services

| Service | Description |
|---|---|
| `smart_heating_advisor.run_daily_analysis` | Trigger daily AI analysis immediately |
| `smart_heating_advisor.run_weekly_analysis` | Trigger weekly report immediately |
| `smart_heating_advisor.start_override` | Start override for a room with a duration |

---

## Architecture

- **Integration type:** service, single config entry
- **Rooms** stored as config subentries (Settings → Integrations → SHA → room card)
- **Vacation** stored as a config subentry
- **Automations** written inline to `automations.yaml` — one automation per room named `SHA — {room_name}`, never visible in the Blueprints UI
- **Blueprint file** stays inside `custom_components/smart_heating_advisor/blueprints/` and is not copied to `/config/blueprints/`
- **Daily analysis** runs at 00:01
- **Weekly report** runs Sunday at 01:00

---

## Troubleshooting

**Enable debug logging**

Go to the integration card → **Configure** → enable **Enable debug logging**. SHA will write detailed logs for entity creation, InfluxDB queries, Ollama prompts and responses, and heating rate updates.

**Check HA logs**

Filter by `custom_components.smart_heating_advisor` in **Settings → System → Logs**.

**Run manual analysis**

**Developer Tools → Actions → `smart_heating_advisor.run_daily_analysis`**

**SHA automation not created**

Check HA logs for errors writing to `automations.yaml`. Ensure the file exists and is writable. SHA writes the automation on the next reload after a room is added.

**Pre-heat starts too early or too late**

SHA adjusts the heating rate daily based on InfluxDB history. Allow a few days for the AI to calibrate. Check `number.sha_{room}_heating_rate` — a higher value means SHA thinks the room heats faster.

**InfluxDB returning no data**

Verify your InfluxDB bucket, org and token in the integration settings. Confirm the temperature sensor entity ID is being recorded to InfluxDB. SHA queries the last 7 days of data.

---

## License

[MIT](LICENSE)
