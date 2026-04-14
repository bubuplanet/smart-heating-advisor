"""Coordinator for Smart Heating Advisor."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .number import SHAHeatingRateNumber
    from .switch import SHAOverrideSwitch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_OLLAMA_URL,
    CONF_OLLAMA_MODEL,
    CONF_INFLUXDB_URL,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_BUCKET,
    CONF_WEATHER_ENTITY,
    DEFAULT_HEATING_RATE,
    MIN_HEATING_RATE,
    MAX_HEATING_RATE,
    OLLAMA_TIMEOUT,
)
from .ollama import OllamaClient
from .analyzer import (
    analyze_heating_sessions,
    build_schedule_lines,
    build_sessions_table,
    build_sessions_text,
    build_weekly_accuracy_summary,
    detect_all_trvs_active_since,
    detect_standby_temp,
    get_season,
)
from .prompt_loader import load_prompt

_LOGGER = logging.getLogger(__name__)


def _room_name_to_id(room_name: str) -> str:
    """Convert room name to snake_case room ID.

    Examples:
        Bathroom           → bathroom
        Alessio's Bedroom  → alessios_bedroom
        Living Room        → living_room
    """
    room_id = room_name.lower()
    room_id = room_id.replace("'", "")
    room_id = re.sub(r"[\s\-]+", "_", room_id)
    room_id = re.sub(r"[^a-z0-9_]", "", room_id)
    return room_id


def _mask_secret(value: str, visible: int = 4) -> str:
    """Return a masked version of a secret string for safe log output.

    Shows only the last ``visible`` characters; the rest are replaced with ``*``.
    Returns ``'<empty>'`` for falsy or non-string input.

    Example::

        _mask_secret("my-long-api-token-abc1")  # → "********************abc1"
    """
    if not isinstance(value, str) or not value:
        return "<empty>"
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def _do_read_automation_inputs(config_dir: str, room_name: str) -> list[str]:
    """Read schedule entity IDs from automations.yaml for a given room.

    This is a blocking function — call via async_add_executor_job.
    Returns an empty list when automations.yaml is missing, unreadable, or
    when no matching automation is found.
    """
    import os
    import yaml

    automations_file = os.path.join(config_dir, "automations.yaml")
    try:
        with open(automations_file) as fh:
            automations = yaml.safe_load(fh) or []
    except Exception as exc:
        _LOGGER.debug("Could not read automations.yaml: %s", exc)
        return []

    if not isinstance(automations, list):
        return []

    room_id = _room_name_to_id(room_name)

    for automation in automations:
        if not isinstance(automation, dict):
            continue
        blueprint = automation.get("use_blueprint", {})
        if not isinstance(blueprint, dict):
            continue
        inputs = blueprint.get("input", {})
        if not isinstance(inputs, dict):
            continue

        # Match by room_name field inside blueprint inputs
        auto_room = inputs.get("room_name", "")
        if _room_name_to_id(str(auto_room)) != room_id:
            continue

        schedules = inputs.get("schedules", [])
        if isinstance(schedules, str):
            return [schedules] if schedules.strip() else []
        if isinstance(schedules, list):
            return [str(s).strip() for s in schedules if str(s).strip()]
        return []

    return []


class RoomConfig:
    """Holds configuration for a single room discovered from blueprint automations."""

    def __init__(
        self,
        room_name: str,
        temp_sensor: str,
        schedules: list[str],
        daily_report_enabled: bool = True,
        weekly_report_enabled: bool = True,
    ):
        self.room_name = room_name
        self.room_id = _room_name_to_id(room_name)
        self.temp_sensor = temp_sensor
        self.schedule_entities = schedules
        self.daily_report_enabled = daily_report_enabled
        self.weekly_report_enabled = weekly_report_enabled

        # SHA helper entity IDs — custom switch/number entities, derived from room_id
        self.heating_rate_helper = f"number.sha_{self.room_id}_heating_rate"
        self.override_switch = f"switch.sha_{self.room_id}_override"
        self.airing_mode = f"switch.sha_{self.room_id}_airing_mode"
        self.preheat_notified = f"switch.sha_{self.room_id}_preheat_notified"
        self.target_notified = f"switch.sha_{self.room_id}_target_notified"
        self.standby_notified = f"switch.sha_{self.room_id}_standby_notified"
        self.vacation_notified = f"switch.sha_{self.room_id}_vacation_notified"

    def __repr__(self):
        return (
            f"RoomConfig(name={self.room_name!r}, "
            f"sensor={self.temp_sensor!r}, "
            f"schedules={self.schedule_entities}, "
            f"daily_report_enabled={self.daily_report_enabled}, "
            f"weekly_report_enabled={self.weekly_report_enabled})"
        )


class SmartHeatingCoordinator:
    """Coordinates data fetching, analysis and HA state updates for all rooms."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self._entities = []

        # Per-room state — keyed by room_id
        self.room_states: dict[str, dict] = {}

        # Override switch references — keyed by room_id, populated by switch.async_setup_entry
        self._override_switches: dict[str, "SHAOverrideSwitch"] = {}

        # Heating rate entity references — keyed by room_id, populated by number.async_setup_entry
        self.heating_rate_entities: dict[str, "SHAHeatingRateNumber"] = {}

        # Room registry — persisted per config entry via HA storage
        self._room_registry_store: Store = Store(
            hass,
            1,
            f"{self.entry.domain}.{self.entry.entry_id}_rooms",
        )
        self._room_registry: dict[str, dict] = {}

        self.ollama = OllamaClient(
            url=self.config[CONF_OLLAMA_URL],
            model=self.config[CONF_OLLAMA_MODEL],
            timeout=OLLAMA_TIMEOUT,
        )

    # ──────────────────────────────────────────────────────────────────
    # Entity registration
    # ──────────────────────────────────────────────────────────────────

    def register_entities(self, entities: list) -> None:
        """Register sensor entities for push updates."""
        self._entities = entities

    async def async_update_sensors(self) -> None:
        """Push updated state to all registered sensor entities."""
        for entity in self._entities:
            entity.async_write_ha_state()

    def register_heating_rate_entity(self, room_id: str, entity: "SHAHeatingRateNumber") -> None:
        """Store a reference to a room's heating rate entity for direct updates."""
        self.heating_rate_entities[room_id] = entity

    # ──────────────────────────────────────────────────────────────────
    # Room registry / discovery
    # ──────────────────────────────────────────────────────────────────

    async def async_load_room_registry(self) -> None:
        """Load persisted room registry for this config entry."""
        data = await self._room_registry_store.async_load()
        rooms = data.get("rooms", {}) if isinstance(data, dict) else {}
        self._room_registry = rooms if isinstance(rooms, dict) else {}

        _LOGGER.debug(
            "Room registry load: entry_id=%s payload_type=%s room_keys=%s",
            self.entry.entry_id,
            type(data).__name__,
            sorted(self._room_registry.keys()),
        )

        if self._room_registry:
            _LOGGER.info(
                "Loaded %d room(s) from SHA room registry",
                len(self._room_registry),
            )
        else:
            _LOGGER.info("SHA room registry is empty")

    async def async_register_room(
        self,
        room_name: str,
        temp_sensor: str,
        schedules: list[str] | str | None,
        daily_report_enabled: bool = True,
        weekly_report_enabled: bool = True,
    ) -> bool:
        """Register or update one room in the persistent registry.

        Returns True when registry content changed.
        """
        room_name = (room_name or "").strip()
        temp_sensor = (temp_sensor or "").strip()

        _LOGGER.debug(
            "Room registry register request: room_name='%s' temp_sensor='%s' schedules_raw=%s",
            room_name,
            temp_sensor,
            schedules,
        )

        if not room_name:
            _LOGGER.debug("Room registry register skipped: missing room_name")
            return False

        if isinstance(schedules, str):
            normalized_schedules = [schedules]
        elif isinstance(schedules, list):
            normalized_schedules = [str(s).strip() for s in schedules if str(s).strip()]
        else:
            normalized_schedules = []

        room_id = _room_name_to_id(room_name)
        new_data = {
            "room_name": room_name,
            "temp_sensor": temp_sensor,
            "schedules": normalized_schedules,
            "daily_report_enabled": bool(daily_report_enabled),
            "weekly_report_enabled": bool(weekly_report_enabled),
        }

        old_data = self._room_registry.get(room_id)
        if old_data == new_data:
            _LOGGER.debug("Room registry unchanged for room_id='%s'", room_id)
            return False

        self._room_registry[room_id] = new_data
        await self._room_registry_store.async_save({"rooms": self._room_registry})
        _LOGGER.info(
            "Room registry updated: %s (sensor=%s, schedules=%d, daily_report=%s, weekly_report=%s)",
            room_name,
            temp_sensor,
            len(normalized_schedules),
            bool(daily_report_enabled),
            bool(weekly_report_enabled),
        )
        return True

    async def async_unregister_room(self, room_name: str) -> str | None:
        """Remove a room from the persistent registry and in-memory state.

        Returns the room_id when a room was removed, None if not found.
        """
        room_id = _room_name_to_id(room_name.strip())
        if room_id not in self._room_registry:
            _LOGGER.warning(
                "Room registry unregister: room_id='%s' not found — already removed?",
                room_id,
            )
            return None

        del self._room_registry[room_id]
        await self._room_registry_store.async_save({"rooms": self._room_registry})

        # Clean up in-memory state so nothing lingers until the next reload.
        self.room_states.pop(room_id, None)
        self._override_switches.pop(room_id, None)
        self.heating_rate_entities.pop(room_id, None)

        _LOGGER.info("Room registry: removed room '%s' (room_id=%s)", room_name, room_id)
        return room_id

    def discover_rooms(self) -> list[RoomConfig]:
        """Build RoomConfig list from persistent SHA room registry."""
        rooms: list[RoomConfig] = []

        for room_id in sorted(self._room_registry.keys()):
            room_data = self._room_registry.get(room_id, {})
            room_name = str(room_data.get("room_name", "")).strip()
            temp_sensor = str(room_data.get("temp_sensor", "")).strip()
            schedules = room_data.get("schedules", [])
            daily_report_enabled = bool(room_data.get("daily_report_enabled", True))
            weekly_report_enabled = bool(room_data.get("weekly_report_enabled", True))

            if not room_name:
                continue

            if isinstance(schedules, str):
                schedules = [schedules]
            elif not isinstance(schedules, list):
                schedules = []

            rooms.append(
                RoomConfig(
                    room_name,
                    temp_sensor,
                    schedules,
                    daily_report_enabled=daily_report_enabled,
                    weekly_report_enabled=weekly_report_enabled,
                )
            )

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Room discovery (registry): %d room(s): %s",
                len(rooms),
                [r.room_name for r in rooms],
            )
            for room in rooms:
                _LOGGER.debug(
                    "Room discovery (registry) detail: room='%s' room_id='%s' sensor='%s' schedules=%s",
                    room.room_name,
                    room.room_id,
                    room.temp_sensor,
                    room.schedule_entities,
                )

        if not rooms:
            _LOGGER.warning(
                "SHA room registry is empty. "
                "Add rooms via Settings → Integrations → Smart Heating Advisor → Configure."
            )

        return rooms

    # ──────────────────────────────────────────────────────────────────
    # InfluxDB
    # ──────────────────────────────────────────────────────────────────

    async def async_query_influxdb(
        self,
        entity_id: str,
        days: int,
        field: str = "value",
        measurement: str = "°C",
    ) -> list[tuple]:
        """Query InfluxDB for readings for a specific entity.

        Args:
            entity_id: Full HA entity_id (any domain prefix is stripped).
            days:      How many days back to query.
            field:     InfluxDB field name.  Room temp sensors use ``"value"``;
                       climate entity setpoints use ``"temperature"``.
            measurement: InfluxDB measurement filter (default ``"°C"``).
        """
        import aiohttp

        token = self.config[CONF_INFLUXDB_TOKEN]
        url = self.config[CONF_INFLUXDB_URL]
        org = self.config[CONF_INFLUXDB_ORG]
        bucket = self.config[CONF_INFLUXDB_BUCKET]

        # Strip any domain prefix (sensor., climate., binary_sensor., …)
        influx_entity_id = entity_id.split(".", 1)[-1] if "." in entity_id else entity_id

        flux_query = f"""
from(bucket: "{bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["entity_id"] == "{influx_entity_id}")
  |> filter(fn: (r) => r["_field"] == "{field}")
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> sort(columns: ["_time"])
  |> yield(name: "data")
"""

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

        _LOGGER.debug(
            "InfluxDB: querying %s for entity '%s' — last %d day(s)",
            url, entity_id, days,
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{url}/api/v2/query",
                    params={"org": org},
                    headers=headers,
                    data=flux_query,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "InfluxDB query failed for %s: status %s",
                            entity_id,
                            response.status,
                        )
                        return []
                    csv_text = await response.text()
                    readings = self._parse_influxdb_csv(csv_text)
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "InfluxDB: received %d reading(s) for '%s'%s",
                            len(readings),
                            entity_id,
                            f" — first: {readings[0][0]}, last: {readings[-1][0]}" if readings else "",
                        )
                    return readings
        except Exception as e:
            _LOGGER.error("InfluxDB query error for %s: %s", entity_id, e)
            return []

    def _parse_influxdb_csv(self, csv_text: str) -> list[tuple]:
        """Parse InfluxDB CSV response into (datetime, float) tuples."""
        readings = []
        lines = csv_text.strip().split("\n")
        time_idx = None
        value_idx = None

        for line in lines:
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if "_time" in parts and "_value" in parts:
                time_idx = parts.index("_time")
                value_idx = parts.index("_value")
                continue
            if time_idx is None or value_idx is None:
                continue
            try:
                ts = parts[time_idx].strip().replace("Z", "+00:00")
                val = float(parts[value_idx].strip())
                readings.append((datetime.fromisoformat(ts), val))
            except (ValueError, IndexError):
                continue

        return sorted(readings, key=lambda x: x[0])

    # ──────────────────────────────────────────────────────────────────
    # TRV helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_room_trvs(self, room: RoomConfig) -> list[str]:
        """Return the list of TRV entity IDs for this room from subentry data."""
        for subentry in self.entry.subentries.values():
            sub_room = subentry.data.get("room_name", "")
            if _room_name_to_id(str(sub_room)) == room.room_id:
                trvs = subentry.data.get("trvs", [])
                return list(trvs) if isinstance(trvs, list) else []
        return []

    async def async_query_trv_readings(
        self, room: RoomConfig, days: int
    ) -> dict[str, list[tuple]]:
        """Query InfluxDB setpoint history for every TRV in the room.

        Returns a dict mapping entity_id → list of (datetime, setpoint) tuples.
        Climate setpoints are stored in InfluxDB as field ``temperature``
        (the attribute name) in measurement ``°C``.
        """
        trvs = self._get_room_trvs(room)
        if not trvs:
            _LOGGER.debug("[%s] No TRV entities configured — skipping TRV query", room.room_name)
            return {}

        result: dict[str, list[tuple]] = {}
        for trv_entity_id in trvs:
            readings = await self.async_query_influxdb(
                trv_entity_id, days, field="temperature", measurement="°C"
            )
            _LOGGER.debug(
                "[%s] TRV %s: %d setpoint reading(s) over last %d day(s)",
                room.room_name, trv_entity_id, len(readings), days,
            )
            result[trv_entity_id] = readings

        return result

    # ──────────────────────────────────────────────────────────────────
    # Schedule helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_schedule_info(self, room: RoomConfig) -> list[dict]:
        """Read schedule helper states and return schedule info list."""
        schedules = []
        for entity_id in room.schedule_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("[%s] Schedule entity %s not found in HA states", room.room_name, entity_id)
                continue
            fname = state.attributes.get("friendly_name", entity_id)
            next_event = state.attributes.get("next_event")
            schedules.append(
                {
                    "entity_id": entity_id,
                    "name": fname,
                    "state": state.state,
                    "next_event": next_event,
                }
            )
        return schedules

    async def _async_get_schedule_info_with_fallback(
        self, room: RoomConfig
    ) -> list[dict]:
        """Return schedule info for the room, falling back to automations.yaml.

        When the room registry has no schedule entities (common because the
        blueprint service call registers rooms with schedules=[]), this reads
        entity IDs from automations.yaml and resolves their HA states.
        """
        schedules = self._get_schedule_info(room)
        if schedules:
            return schedules

        _LOGGER.debug(
            "[%s] Registry has no schedule entities — reading automations.yaml",
            room.room_name,
        )
        entity_ids = await self.hass.async_add_executor_job(
            _do_read_automation_inputs,
            self.hass.config.config_dir,
            room.room_name,
        )

        if not entity_ids:
            _LOGGER.debug(
                "[%s] No schedule entities found in automations.yaml", room.room_name
            )
            return []

        _LOGGER.info(
            "[%s] Found %d schedule(s) from automations.yaml: %s",
            room.room_name,
            len(entity_ids),
            entity_ids,
        )

        result = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug(
                    "[%s] Schedule entity %s not in HA states", room.room_name, entity_id
                )
                # Entity not yet loaded — use entity_id as name so regex can
                # still extract a temperature if the name encodes it.
                result.append(
                    {
                        "entity_id": entity_id,
                        "name": entity_id,
                        "state": "unknown",
                        "next_event": None,
                    }
                )
                continue
            result.append(
                {
                    "entity_id": entity_id,
                    "name": state.attributes.get("friendly_name", entity_id),
                    "state": state.state,
                    "next_event": state.attributes.get("next_event"),
                }
            )

        return result

    def _primary_schedule_info(self, schedules: list[dict]) -> dict:
        """Return name, target_temp, and schedule_time for the primary schedule.

        The primary schedule is the one with the highest target temperature,
        which is extracted from the schedule entity's friendly name using the
        same regex as the blueprint (e.g. "Morning Shower 26C" → 26°C).
        """
        from .analyzer import extract_temp_from_schedule_name

        if not schedules:
            return {"name": "Default", "target_temp": 21.0, "schedule_time": "n/a"}

        best = max(
            schedules,
            key=lambda s: extract_temp_from_schedule_name(s.get("name", ""), 21.0),
        )
        name = best.get("name", "Default")
        target_temp = extract_temp_from_schedule_name(name, 21.0)

        schedule_time = "n/a"
        next_event = best.get("next_event")
        if next_event:
            try:
                dt = datetime.fromisoformat(str(next_event).replace("Z", "+00:00"))
                schedule_time = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                pass

        return {"name": name, "target_temp": target_temp, "schedule_time": schedule_time}

    # ──────────────────────────────────────────────────────────────────
    # Weather
    # ──────────────────────────────────────────────────────────────────

    def _get_weather_data(self) -> dict:
        """Get current and forecast weather from HA weather entity."""
        weather_entity = self.config.get(CONF_WEATHER_ENTITY, "weather.forecast_home")
        state = self.hass.states.get(weather_entity)

        if not state:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return {"outside_temp": 10.0, "tomorrow_min": 5.0, "tomorrow_max": 15.0}

        outside_temp = state.attributes.get("temperature", 10.0)
        forecast = state.attributes.get("forecast", [])
        tomorrow_min = 5.0
        tomorrow_max = 15.0

        if len(forecast) > 1:
            tomorrow = forecast[1]
            tomorrow_min = tomorrow.get("templow", tomorrow.get("temperature", 5.0))
            tomorrow_max = tomorrow.get("temperature", 15.0)

        return {
            "outside_temp": float(outside_temp),
            "tomorrow_min": float(tomorrow_min),
            "tomorrow_max": float(tomorrow_max),
        }

    # ──────────────────────────────────────────────────────────────────
    # Apply results
    # ──────────────────────────────────────────────────────────────────

    async def _async_apply_heating_rate(
        self, room: RoomConfig, rate: float, reasoning: str
    ) -> None:
        """Update the room's heating rate helper in HA."""
        clamped = max(MIN_HEATING_RATE, min(MAX_HEATING_RATE, rate))
        if clamped != rate:
            _LOGGER.debug(
                "[%s] AI rate %.3f clamped to [%.3f, %.3f] → %.3f",
                room.room_name, rate, MIN_HEATING_RATE, MAX_HEATING_RATE, clamped,
            )
        rate = clamped

        # Preserve the old rate so the weekly prompt can report whether
        # a change was made and what the previous value was.
        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["previous_rate"] = (
            self.room_states[room.room_id].get("heating_rate", rate)
        )

        entity = self.heating_rate_entities.get(room.room_id)
        if entity:
            _LOGGER.debug(
                "[%s] Writing %.3f °C/min directly to entity %s",
                room.room_name, rate, room.heating_rate_helper,
            )
            await entity.async_set_native_value(round(rate, 3))
        else:
            _LOGGER.warning(
                "[%s] No heating rate entity registered — falling back to service call",
                room.room_name,
            )
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": room.heating_rate_helper, "value": round(rate, 3)},
            )

        # Update in-memory state
        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["heating_rate"] = rate
        self.room_states[room.room_id]["last_analysis"] = (
            datetime.now(timezone.utc).isoformat()
        )

        await self.async_update_sensors()
        _LOGGER.info(
            "[%s] Heating rate updated to %.3f°C/min. Reason: %s",
            room.room_name,
            rate,
            reasoning,
        )

    async def _async_notify(self, title: str, message: str) -> None:
        """Send HA mobile notification."""
        await self.hass.services.async_call(
            "notify", "notify", {"title": title, "message": message}
        )

    async def _async_persistent_notification(
        self, title: str, message: str, notification_id: str
    ) -> None:
        """Create or replace a persistent notification in HA UI."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {"title": title, "message": message, "notification_id": notification_id},
        )

    def _format_daily_outcome(self, room_name: str, old_rate: float, new_rate: float) -> str:
        """Return one-line daily outcome summary."""
        delta = new_rate - old_rate
        if abs(delta) < 0.0005:
            return f"No changes needed in {room_name}."
        if delta > 0:
            return f"Heating increased by {delta:.3f} °C/min in {room_name}."
        return f"Heating decreased by {abs(delta):.3f} °C/min in {room_name}."

    def _format_weekly_outcome(self, room_name: str, current_rate: float, suggested_rate: float) -> str:
        """Return one-line weekly outcome summary."""
        delta = suggested_rate - current_rate
        if abs(delta) < 0.0005:
            return f"No changes suggested for {room_name}."
        if delta > 0:
            return f"Weekly suggestion: increase heating by {delta:.3f} °C/min in {room_name}."
        return f"Weekly suggestion: decrease heating by {abs(delta):.3f} °C/min in {room_name}."

    async def _async_notify_daily_room_result(
        self,
        room: RoomConfig,
        run_ts: str,
        old_rate: float | None,
        new_rate: float | None,
        success_rate: int | None,
        outcome: str,
        details: str,
    ) -> None:
        """Create daily per-room persistent notification summary."""
        if not room.daily_report_enabled:
            _LOGGER.debug("[%s] Daily persistent report disabled for this room", room.room_name)
            return

        old_str = f"{old_rate:.3f}" if old_rate is not None else "n/a"
        new_str = f"{new_rate:.3f}" if new_rate is not None else "n/a"
        success_str = f"{success_rate}%" if success_rate is not None else "n/a"

        await self._async_persistent_notification(
            title=f"📅 {room.room_name} — Daily Heating Report",
            message=(
                f"Run time: {run_ts}\n\n"
                f"Outcome: {outcome}\n\n"
                f"Affected sensor: {room.temp_sensor}\n"
                f"Old value: {old_str} °C/min\n"
                f"New value: {new_str} °C/min\n"
                f"Success rate (last 7 days): {success_str}\n"
                f"Details: {details}"
            ),
            notification_id=f"heating_advisor_daily_{room.room_id}",
        )

    async def _async_notify_weekly_room_result(
        self,
        room: RoomConfig,
        run_ts: str,
        current_rate: float | None,
        suggested_rate: float | None,
        success_rate: int | None,
        outcome: str,
        details: str,
        weekly_report: str,
    ) -> None:
        """Create weekly per-room persistent notification summary."""
        if not room.weekly_report_enabled:
            _LOGGER.debug("[%s] Weekly persistent report disabled for this room", room.room_name)
            return

        current_str = f"{current_rate:.3f}" if current_rate is not None else "n/a"
        suggested_str = f"{suggested_rate:.3f}" if suggested_rate is not None else "n/a"
        success_str = f"{success_rate}%" if success_rate is not None else "n/a"
        report_text = weekly_report or "No weekly report generated."

        await self._async_persistent_notification(
            title=f"📊 {room.room_name} — Weekly Heating Report",
            message=(
                f"Run time: {run_ts}\n\n"
                f"Outcome: {outcome}\n\n"
                f"Affected sensor: {room.temp_sensor}\n"
                f"Current value: {current_str} °C/min\n"
                f"Suggested value: {suggested_str} °C/min\n"
                f"Success rate (last 30 days): {success_str}\n"
                f"Details: {details}\n\n"
                f"Weekly summary:\n{report_text}"
            ),
            notification_id=f"heating_advisor_weekly_{room.room_id}",
        )

    # ──────────────────────────────────────────────────────────────────
    # Daily analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_daily_analysis(self) -> None:
        """Run daily heating rate analysis for all discovered rooms."""
        _LOGGER.info("Starting daily heating analysis")

        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ SHA — Daily Analysis Failed",
                "Daily analysis skipped — cannot connect to Ollama.",
            )
            return

        rooms = self.discover_rooms()
        if not rooms:
            _LOGGER.warning("No rooms discovered — skipping daily analysis")
            return

        weather = self._get_weather_data()
        season = get_season(datetime.now().month)

        for room in rooms:
            _LOGGER.info("[%s] Running daily analysis", room.room_name)
            await self._async_run_daily_analysis_for_room(
                room, weather, season
            )

    async def _async_run_daily_analysis_for_room(
        self, room: RoomConfig, weather: dict, season: str
    ) -> None:
        """Run daily analysis for a single room."""
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _LOGGER.debug(
            "[%s] Daily analysis — weather: outside=%.1f°C, tomorrow min=%.1f/max=%.1f°C, season=%s",
            room.room_name,
            weather["outside_temp"], weather["tomorrow_min"], weather["tomorrow_max"], season,
        )
        readings = await self.async_query_influxdb(room.temp_sensor, days=7)
        if len(readings) < 5:
            _LOGGER.warning(
                "[%s] Not enough data (%d readings) — skipping",
                room.room_name,
                len(readings),
            )
            await self._async_notify_daily_room_result(
                room=room,
                run_ts=run_ts,
                old_rate=None,
                new_rate=None,
                success_rate=None,
                outcome=f"No changes needed in {room.room_name}.",
                details=f"Analysis ran but not enough data ({len(readings)} readings).",
            )
            return

        # Query TRV setpoint history — used for accurate session detection
        trv_readings = await self.async_query_trv_readings(room, days=7)
        standby_temp = detect_standby_temp(trv_readings)
        standby_threshold = standby_temp + 5.0
        all_trvs_active_since = detect_all_trvs_active_since(trv_readings, standby_threshold)

        if all_trvs_active_since:
            _LOGGER.info(
                "[%s] All TRVs active since %s — sessions before this date excluded",
                room.room_name,
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
            )

        schedules = await self._async_get_schedule_info_with_fallback(room)
        primary = self._primary_schedule_info(schedules)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "[%s] Schedules found: %d — %s | primary: %s target=%.1f°C time=%s",
                room.room_name, len(schedules), [s["name"] for s in schedules],
                primary["name"], primary["target_temp"], primary["schedule_time"],
            )
        analysis = analyze_heating_sessions(
            readings,
            schedules,
            schedule_time_hhmm=primary["schedule_time"] if primary["schedule_time"] != "n/a" else None,
            trv_readings=trv_readings if trv_readings else None,
            all_trvs_active_since=all_trvs_active_since,
            standby_temp=standby_temp,
            target_temp=primary["target_temp"],
        )
        _LOGGER.debug(
            "[%s] Analysis: %d session(s), success_rate=%s%%, avg_rate=%s",
            room.room_name,
            len(analysis.get("sessions", [])),
            analysis.get("success_rate", "?"),
            analysis.get("avg_rate", "?"),
        )

        if not analysis["sessions"]:
            trvs_early = self._get_room_trvs(room)
            active_since_early = (
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
                if all_trvs_active_since else "n/a"
            )
            days_reliable: int | None = None
            if all_trvs_active_since:
                days_reliable = (datetime.now(timezone.utc) - all_trvs_active_since).days

            if trv_readings and all_trvs_active_since and days_reliable is not None and days_reliable < 3:
                no_session_details = (
                    f"Reliable TRV data only available since {active_since_early} "
                    f"({days_reliable} day(s)). "
                    f"Need at least 3 days before daily analysis can run reliably."
                )
            else:
                no_session_details = (
                    f"No heating sessions detected in the last 7 days. "
                    f"TRVs configured: {len(trvs_early)}. "
                    f"Reliable data since: {active_since_early}."
                )
            _LOGGER.warning(
                "[%s] No heating sessions detected "
                "(TRVs=%d, reliable_since=%s, days_reliable=%s) — skipping",
                room.room_name, len(trvs_early), active_since_early, days_reliable,
            )
            await self._async_notify_daily_room_result(
                room=room,
                run_ts=run_ts,
                old_rate=None,
                new_rate=None,
                success_rate=analysis.get("success_rate"),
                outcome=f"No changes needed in {room.room_name}.",
                details=no_session_details,
            )
            return

        state = self.hass.states.get(room.heating_rate_helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE
        _LOGGER.debug("[%s] Current heating rate: %.3f °C/min", room.room_name, current_rate)

        trvs = self._get_room_trvs(room)
        active_since_str = (
            all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
            if all_trvs_active_since else "n/a"
        )

        prompt = await self.hass.async_add_executor_job(
            load_prompt,
            "daily_analysis.md",
            {
                # Current variable names (new bundled prompts)
                "room_name": room.room_name,
                "heating_rate": round(current_rate, 3),
                "analysis_days": 7,
                "schedule_name": primary["name"],
                "target_temp": primary["target_temp"],
                "schedule_time": primary["schedule_time"],
                "schedule_lines": build_schedule_lines(schedules),
                "sessions_table": build_sessions_table(analysis),
                "sessions_text": build_sessions_text(analysis),
                "sessions_total": analysis.get("sessions_total", 0),
                "sessions_on_target": analysis.get("sessions_on_target", 0),
                "sessions_with_miss": analysis.get("sessions_with_miss", 0),
                "average_miss": analysis.get("average_miss", 0.0),
                "consecutive_misses": analysis.get("consecutive_misses", 0),
                "miss_trend": analysis.get("miss_trend", "stable"),
                "outside_temp": weather["outside_temp"],
                "tomorrow_min": weather["tomorrow_min"],
                "tomorrow_max": weather["tomorrow_max"],
                "season": season,
                # TRV data
                "trv_entities": ", ".join(trvs) if trvs else "none configured",
                "trv_count": len(trvs),
                "standby_temp": standby_temp,
                "all_trvs_active_since": active_since_str,
                "session_count": analysis.get("sessions_total", 0),
                "on_target_count": analysis.get("sessions_on_target", 0),
                "avg_observed_rate": str(analysis.get("avg_rate") or "n/a"),
                # Backward-compat aliases for old /config prompt files
                "current_rate": round(current_rate, 3),
                "days_analyzed": analysis.get("days_analyzed", 0),
                "avg_rate": str(analysis.get("avg_rate") or "n/a"),
                "success_rate": analysis.get("success_rate", 0),
                "avg_start_time": analysis.get("avg_start_time") or "n/a",
            },
            self.hass.config.config_dir,
        )

        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result or "heating_rate" not in result:
            _LOGGER.error(
                "[%s] Invalid Ollama response (first 200 chars): %s",
                room.room_name,
                (response or "")[:200],
            )
            await self._async_notify_daily_room_result(
                room=room,
                run_ts=run_ts,
                old_rate=current_rate,
                new_rate=current_rate,
                success_rate=analysis.get("success_rate"),
                outcome=f"No changes needed in {room.room_name}.",
                details="AI response was invalid, keeping previous heating rate.",
            )
            return

        new_rate = float(result["heating_rate"])
        reasoning = result.get("reasoning", result.get("rate_adjustment_reason", "No reasoning provided"))
        confidence = result.get("confidence", "unknown")
        target_accuracy_percent = result.get("target_accuracy_percent")
        average_miss_celsius = result.get("average_miss_celsius")
        rate_adjustment_reason = result.get("rate_adjustment_reason", reasoning)
        recommendation = result.get("recommendation", "")
        analysis_summary = result.get("analysis_summary", "")

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "[%s] AI result — new_rate=%.3f, confidence=%s, accuracy=%s%%, "
                "avg_miss=%s°C, reasoning=%s",
                room.room_name, new_rate, confidence,
                target_accuracy_percent, average_miss_celsius, reasoning,
            )

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence
        self.room_states[room.room_id]["target_accuracy_percent"] = target_accuracy_percent
        self.room_states[room.room_id]["average_miss_celsius"] = average_miss_celsius
        self.room_states[room.room_id]["rate_adjustment_reason"] = rate_adjustment_reason
        self.room_states[room.room_id]["analysis_summary"] = analysis_summary

        await self._async_apply_heating_rate(room, new_rate, reasoning)

        await self._async_notify_daily_room_result(
            room=room,
            run_ts=run_ts,
            old_rate=current_rate,
            new_rate=new_rate,
            success_rate=analysis.get("success_rate"),
            outcome=self._format_daily_outcome(room.room_name, current_rate, new_rate),
            details=analysis_summary or rate_adjustment_reason or reasoning,
        )

        await self._async_notify(
            f"🌡️ {room.room_name} — Heating Rate Updated",
            f"New rate: {new_rate:.3f}°C/min (was {current_rate:.3f}°C/min)\n"
            f"Accuracy: {target_accuracy_percent}% on target, "
            f"avg miss {average_miss_celsius}°C\n"
            f"Confidence: {confidence}\n"
            f"Reason: {rate_adjustment_reason or reasoning}",
        )

        _LOGGER.info(
            "[%s] Daily analysis complete. New rate: %.3f (accuracy=%s%%, avg_miss=%s°C)",
            room.room_name, new_rate, target_accuracy_percent, average_miss_celsius,
        )

    # ──────────────────────────────────────────────────────────────────
    # Weekly analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_weekly_analysis(self) -> None:
        """Run weekly report analysis for all discovered rooms."""
        _LOGGER.info("Starting weekly heating analysis (report only)")

        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ SHA — Weekly Report Failed",
                "Weekly report skipped — cannot connect to Ollama.",
            )
            return

        rooms = self.discover_rooms()
        if not rooms:
            _LOGGER.warning("No rooms discovered — skipping weekly analysis")
            return

        weather = self._get_weather_data()
        season = get_season(datetime.now().month)

        for room in rooms:
            _LOGGER.info("[%s] Running weekly analysis", room.room_name)
            await self._async_run_weekly_analysis_for_room(room, weather, season)

    async def _async_run_weekly_analysis_for_room(
        self, room: RoomConfig, weather: dict, season: str
    ) -> None:
        """Run weekly analysis for a single room — report only."""
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        readings = await self.async_query_influxdb(room.temp_sensor, days=30)
        if len(readings) < 5:
            _LOGGER.warning(
                "[%s] Not enough data for weekly analysis", room.room_name
            )
            await self._async_notify_weekly_room_result(
                room=room,
                run_ts=run_ts,
                current_rate=None,
                suggested_rate=None,
                success_rate=None,
                outcome=f"No changes suggested for {room.room_name}.",
                details=f"Analysis ran but not enough data ({len(readings)} readings).",
                weekly_report="No weekly report generated due to insufficient data.",
            )
            return

        # Query TRV setpoint history — used for accurate session detection
        trv_readings = await self.async_query_trv_readings(room, days=30)
        standby_temp = detect_standby_temp(trv_readings)
        standby_threshold = standby_temp + 5.0
        all_trvs_active_since = detect_all_trvs_active_since(trv_readings, standby_threshold)

        if all_trvs_active_since:
            _LOGGER.info(
                "[%s] Weekly: all TRVs active since %s — sessions before this date excluded",
                room.room_name,
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
            )

        schedules = await self._async_get_schedule_info_with_fallback(room)
        primary = self._primary_schedule_info(schedules)
        analysis = analyze_heating_sessions(
            readings,
            schedules,
            schedule_time_hhmm=primary["schedule_time"] if primary["schedule_time"] != "n/a" else None,
            trv_readings=trv_readings if trv_readings else None,
            all_trvs_active_since=all_trvs_active_since,
            standby_temp=standby_temp,
            target_temp=primary["target_temp"],
        )

        state = self.hass.states.get(room.heating_rate_helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE

        # Early return when no sessions detected — give context so caller can understand why
        if not analysis["sessions"]:
            trvs_early = self._get_room_trvs(room)
            active_since_early = (
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
                if all_trvs_active_since else "n/a"
            )
            days_reliable: int | None = None
            if all_trvs_active_since:
                days_reliable = (datetime.now(timezone.utc) - all_trvs_active_since).days

            if trv_readings and all_trvs_active_since and days_reliable is not None and days_reliable < 7:
                no_session_details = (
                    f"Reliable TRV data only available since {active_since_early} "
                    f"({days_reliable} day(s) of reliable data). "
                    f"Need at least 7 days before sessions can be analysed reliably."
                )
                no_session_report = (
                    f"SHA is still collecting reliable data for {room.room_name} "
                    f"(reliable since {active_since_early}, {days_reliable} day(s)). "
                    f"A full report will be available after 7 days of data."
                )
            else:
                no_session_details = (
                    f"No heating sessions detected in the last 30 days. "
                    f"TRVs configured: {len(trvs_early)}. "
                    f"Reliable data since: {active_since_early}."
                )
                no_session_report = (
                    f"No heating sessions were detected for {room.room_name} in the last 30 days. "
                    f"Check that the TRV entities are recording setpoint changes in InfluxDB."
                )

            _LOGGER.warning(
                "[%s] Weekly: no heating sessions detected "
                "(TRVs=%d, reliable_since=%s, days_reliable=%s)",
                room.room_name, len(trvs_early), active_since_early, days_reliable,
            )
            await self._async_notify_weekly_room_result(
                room=room,
                run_ts=run_ts,
                current_rate=current_rate,
                suggested_rate=current_rate,
                success_rate=0,
                outcome=f"No changes suggested for {room.room_name}.",
                details=no_session_details,
                weekly_report=no_session_report,
            )
            return

        # Weekly accuracy context
        sessions_7 = analysis.get("sessions", [])
        weekly_on_target = analysis.get("sessions_on_target", 0)
        weekly_sessions_total = analysis.get("sessions_total", 0)

        # avg temp at schedule start — skip sessions with None values (TRV sessions may lack room data)
        valid_temps = [
            s.get("temp_at_schedule_start", s.get("end_temp"))
            for s in sessions_7
            if s.get("temp_at_schedule_start") is not None or s.get("end_temp") is not None
        ]
        weekly_avg_temp = round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else "n/a"

        weekly_average_miss = analysis.get("average_miss", 0.0)
        previous_rate = self.room_states.get(room.room_id, {}).get("previous_rate", current_rate)
        rate_was_adjusted = "Yes" if abs(current_rate - previous_rate) >= 0.005 else "No"

        trvs = self._get_room_trvs(room)
        active_since_str = (
            all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
            if all_trvs_active_since else "n/a"
        )

        prompt = await self.hass.async_add_executor_job(
            load_prompt,
            "weekly_analysis.md",
            {
                # Current variable names (new bundled prompts)
                "room_name": room.room_name,
                "heating_rate": round(current_rate, 3),
                "analysis_days": 30,
                "schedule_name": primary["name"],
                "target_temp": primary["target_temp"],
                "schedule_time": primary["schedule_time"],
                "schedule_lines": build_schedule_lines(schedules),
                "sessions_text": build_sessions_text(analysis, weekly=True),
                "weekly_accuracy_summary": build_weekly_accuracy_summary(analysis),
                "weekly_on_target": weekly_on_target,
                "weekly_sessions_total": weekly_sessions_total,
                "weekly_avg_temp": weekly_avg_temp,
                "weekly_average_miss": weekly_average_miss,
                "miss_trend": analysis.get("miss_trend", "stable"),
                "consecutive_misses": analysis.get("consecutive_misses", 0),
                "rate_was_adjusted": rate_was_adjusted,
                "previous_rate": round(previous_rate, 3),
                "avg_outside_temp": weather["outside_temp"],
                "season": season,
                # TRV data
                "trv_entities": ", ".join(trvs) if trvs else "none configured",
                "trv_count": len(trvs),
                "standby_temp": standby_temp,
                "all_trvs_active_since": active_since_str,
                "session_count": analysis.get("sessions_total", 0),
                "on_target_count": analysis.get("sessions_on_target", 0),
                "avg_observed_rate": str(analysis.get("avg_rate") or "n/a"),
                # Backward-compat aliases for old /config prompt files
                "current_rate": round(current_rate, 3),
                "days_analyzed": analysis.get("days_analyzed", 0),
                "avg_rate": str(analysis.get("avg_rate") or "n/a"),
                "success_rate": analysis.get("success_rate", 0),
                "avg_start_time": analysis.get("avg_start_time") or "n/a",
            },
            self.hass.config.config_dir,
        )

        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result:
            _LOGGER.error(
                "[%s] Invalid weekly Ollama response (first 200 chars): %s",
                room.room_name,
                (response or "")[:200],
            )
            await self._async_notify_weekly_room_result(
                room=room,
                run_ts=run_ts,
                current_rate=current_rate,
                suggested_rate=current_rate,
                success_rate=analysis.get("success_rate"),
                outcome=f"No changes suggested for {room.room_name}.",
                details="AI response was invalid, keeping current value.",
                weekly_report="No weekly report generated due to invalid AI response.",
            )
            return

        confidence = result.get("confidence", "unknown")
        weekly_report = result.get("weekly_report", "No weekly report generated.")
        try:
            suggested_rate = float(result.get("heating_rate", current_rate))
        except (TypeError, ValueError):
            suggested_rate = current_rate
        reasoning = result.get("reasoning", "")

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence
        self.room_states[room.room_id]["weekly_report"] = weekly_report

        await self.async_update_sensors()

        details = (
            f"Confidence: {confidence}. "
            f"Reasoning: {reasoning or 'No reasoning provided.'} "
            f"Avg observed rate: {analysis.get('avg_rate', 'unknown')} °C/min."
        )
        await self._async_notify_weekly_room_result(
            room=room,
            run_ts=run_ts,
            current_rate=current_rate,
            suggested_rate=suggested_rate,
            success_rate=analysis.get("success_rate"),
            outcome=self._format_weekly_outcome(room.room_name, current_rate, suggested_rate),
            details=details,
            weekly_report=weekly_report,
        )

        _LOGGER.info(
            "[%s] Weekly report complete. Suggested rate: %.3f",
            room.room_name,
            suggested_rate,
        )
