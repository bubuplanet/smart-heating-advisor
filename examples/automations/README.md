# Bathroom Heating Automations

A complete set of 5 automations for smart bathroom heating.

## Required Helpers

### Toggle helpers
| Entity ID | Purpose |
|---|---|
| `input_boolean.bathroom_heating_override` | Override active? |
| `input_boolean.bathroom_automation_running` | Automation changing TRVs? |
| `input_boolean.bathroom_room_target_notified` | Room notification sent today? |
| `input_boolean.bathroom_towel_target_notified` | Towel notification sent today? |
| `input_boolean.bathroom_mode_preheat` | Pre-heat notification sent today? |
| `input_boolean.bathroom_mode_maintain` | Maintain notification sent today? |
| `input_boolean.bathroom_mode_standby` | Standby notification sent today? |
| `input_boolean.bathroom_window_notified` | Window open notification sent today? |

### Date/Time helper
| Entity ID | Purpose |
|---|---|
| `input_datetime.bathroom_override_until` | Override expiry time |

### Number helper
| Entity ID | Min | Max | Step | Initial |
|---|---|---|---|---|
| `input_number.bathroom_heating_rate` | 0.05 | 0.30 | 0.01 | 0.15 |

## Required Entities
| Entity | Description |
|---|---|
| `sensor.bathroom_thermostat_temperature` | Room temperature sensor |
| `climate.bathroom_radiator` | Radiator TRV |
| `climate.bathroom_heated_towel_rail` | Towel rail TRV |
| `binary_sensor.bathroom_window` | Window sensor 1 |
| `binary_sensor.bathroom_window_small` | Window sensor 2 |
| `calendar.home` | Google Calendar for vacation detection |
| `weather.forecast_home` | Weather entity |

## Automation Files

| File | Purpose |
|---|---|
| `bathroom_smart_heating.yaml` | Main loop — pre-heat, maintain, standby, vacation, window |
| `bathroom_override_activated.yaml` | Detects TRV changes, pauses 2 hours |
| `bathroom_override_resume.yaml` | Resumes after 2 hours |
| `bathroom_daily_reset.yaml` | Resets all flags at midnight and 3AM |
| `bathroom_11pm_notification.yaml` | End of day notification |

## Schedule

| Time | Action |
|---|---|
| 00:00 | Reset all flags |
| 03:00 | Reset flags + pre-heat window opens |
| 03:00–06:00 | Smart pre-heat based on actual temperatures |
| 06:00–07:00 | Maintain target temperatures |
| 07:00–23:00 | Standby at 16°C |
| 23:00 | End of day notification |

## Customization

Edit these variables at the top of `bathroom_smart_heating.yaml`:

| Variable | Default | Description |
|---|---|---|
| `room_target` | `26` | Target room temperature |
| `towel_target` | `30` | Target towel rail temperature |
| `standby_target` | `16` | Standby temperature |
| `vacation_target` | `12` | Frost protection during vacation |
| `heating_rate` | `input_number.bathroom_heating_rate` | Auto-calibrated by Smart Heating Advisor |