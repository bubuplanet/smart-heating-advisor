# SHA Example Automations

A complete set of 5 generic room heating automations for use
with Smart Heating Advisor. These can be used for any room —
bathroom, bedroom, office, etc.

## Setup per room

For each room you want to control:
1. Create the required helpers with a room-specific prefix
2. Copy and adapt each automation replacing `ROOM` with your room name
3. Update entity IDs to match your devices

## Required Helpers per room

Replace `ROOM` with your room name (e.g. `bathroom`, `office`, `bedroom_alessio`)

### Toggle helpers
| Entity ID | Purpose |
|---|---|
| `input_boolean.ROOM_heating_override` | Override active? |
| `input_boolean.ROOM_automation_running` | Automation changing TRVs? |
| `input_boolean.ROOM_room_target_notified` | Room target notification sent today? |
| `input_boolean.ROOM_towel_target_notified` | Towel target notification sent today? |
| `input_boolean.ROOM_mode_preheat` | Pre-heat notification sent today? |
| `input_boolean.ROOM_mode_maintain` | Maintain notification sent today? |
| `input_boolean.ROOM_mode_standby` | Standby notification sent today? |
| `input_boolean.ROOM_window_notified` | Window open notification sent today? |

### Date/Time helper
| Entity ID | Purpose |
|---|---|
| `input_datetime.ROOM_override_until` | Override expiry time |

### Number helper
| Entity ID | Min | Max | Step | Initial |
|---|---|---|---|---|
| `input_number.ROOM_heating_rate` | 0.05 | 0.30 | 0.01 | 0.15 |

## Example room prefixes

| Room | Prefix |
|---|---|
| Bathroom | `bathroom` |
| Parent bedroom | `bedroom_parent` |
| Alessio's bedroom | `bedroom_alessio` |
| Office | `office` |

## Required Entities per room
| Entity | Description |
|---|---|
| `sensor.ROOM_thermostat_temperature` | Room temperature sensor |
| `climate.ROOM_radiator` | Radiator TRV |
| `climate.ROOM_heated_towel_rail` | Towel rail TRV (optional) |
| `binary_sensor.ROOM_window` | Window sensor 1 (optional) |
| `binary_sensor.ROOM_window_2` | Window sensor 2 (optional) |
| `calendar.home` | Shared calendar for vacation detection |
| `weather.forecast_home` | Shared weather entity |

## Automation Files

| File | Purpose |
|---|---|
| `sha_room_heating.yaml` | Main loop — pre-heat, maintain, standby, vacation, window |
| `sha_ov