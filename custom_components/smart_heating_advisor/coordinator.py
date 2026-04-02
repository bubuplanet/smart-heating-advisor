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
    build_daily_prompt,
    build_weekly_prompt,
    get_season,
)

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


class RoomConfig:
    """Holds configuration for a single room discovered from blueprint automations."""

    def __init__(
        self,
        room_name: str,
        temp_sensor: str,
        schedules: list[str],
    ):
        self.room_name = room_name
        self.room_id = _room_name_to_id(room_name)
        self.temp_sensor = temp_sensor
        self.schedule_entities = schedules

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
            f"schedules={self.schedule_entities})"
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
    # Room discovery
    # ──────────────────────────────────────────────────────────────────

    def discover_rooms(self) -> list[RoomConfig]:
        """Discover all SHA rooms from blueprint automation configurations.

        SHA reads the blueprint input values directly from each automation's
        stored configuration. It looks for automations using the SHA blueprint
        and extracts room_name, temperature_sensor and schedules from the
        blueprint inputs.

        This is the most reliable approach — no tag parsing needed, we read
        the actual values the user entered in the blueprint UI.

        Returns a list of RoomConfig objects.
        """
        rooms = []
        seen_room_ids = set()

        # Access HA automation configs via the automations component
        automation_component = self.hass.data.get("automation")
        if automation_component is None:
            _LOGGER.warning("Automation component not available for room discovery")
            return rooms

        try:
            # Iterate over all automation entities
            for entity_id, entity in automation_component.entities.items():
                try:
                    # Check if this automation uses the SHA blueprint
                    config = getattr(entity, "raw_config", None) or {}
                    use_blueprint = config.get("use_blueprint", {})
                    blueprint_path = use_blueprint.get("path", "")

                    if "sha_unified_heating" not in blueprint_path:
                        continue

                    inputs = use_blueprint.get("input", {})
                    room_name = inputs.get("room_name", "")
                    temp_sensor = inputs.get("temperature_sensor", "")
                    schedules = inputs.get("schedules", [])

                    if not room_name or not temp_sensor:
                        continue

                    # Normalise schedules to list
                    if isinstance(schedules, str):
                        schedules = [schedules]
                    elif not isinstance(schedules, list):
                        schedules = []

                    room_id = _room_name_to_id(room_name)
                    if room_id in seen_room_ids:
                        _LOGGER.debug(
                            "Room %s already discovered — skipping duplicate", room_name
                        )
                        continue

                    seen_room_ids.add(room_id)
                    rooms.append(RoomConfig(room_name, temp_sensor, schedules))
                    _LOGGER.debug(
                        "Discovered SHA room: %s (sensor: %s, schedules: %s)",
                        room_name,
                        temp_sensor,
                        schedules,
                    )

                except Exception as e:
                    _LOGGER.debug("Could not read automation config for %s: %s", entity_id, e)
                    continue

        except Exception as e:
            _LOGGER.warning("Room discovery via automation component failed: %s", e)
            # Fallback: try reading from automation states attributes
            rooms = self._discover_rooms_from_states()

        if not rooms:
            _LOGGER.warning(
                "No SHA blueprint automations found. "
                "Create automations from the SHA blueprint to enable multi-room analysis."
            )

        return rooms

    def _discover_rooms_from_states(self) -> list[RoomConfig]:
        """Fallback room discovery via automation state attributes.

        Some HA versions expose blueprint inputs in automation attributes.
        """
        rooms = []
        seen_room_ids = set()

        for auto_state in self.hass.states.async_all("automation"):
            attrs = auto_state.attributes
            blueprint = attrs.get("blueprint_inputs", {})

            if "sha_unified_heating" not in str(attrs.get("id", "")):
                # Also check friendly_name pattern or blueprint path in attributes
                if not blueprint:
                    continue

            room_name = blueprint.get("room_name", "")
            temp_sensor = blueprint.get("temperature_sensor", "")
            schedules = blueprint.get("schedules", [])

            if not room_name or not temp_sensor:
                continue

            if isinstance(schedules, str):
                schedules = [schedules]

            room_id = _room_name_to_id(room_name)
            if room_id not in seen_room_ids:
                seen_room_ids.add(room_id)
                rooms.append(RoomConfig(room_name, temp_sensor, schedules))

        return rooms

    # ──────────────────────────────────────────────────────────────────
    # InfluxDB
    # ──────────────────────────────────────────────────────────────────

    async def async_query_influxdb(
        self, entity_id: str, days: int
    ) -> list[tuple]:
        """Query InfluxDB for temperature readings for a specific entity."""
        import aiohttp

        token = self.config[CONF_INFLUXDB_TOKEN]
        url = self.config[CONF_INFLUXDB_URL]
        org = self.config[CONF_INFLUXDB_ORG]
        bucket = self.config[CONF_INFLUXDB_BUCKET]

        # InfluxDB stores entity_id without domain prefix
        influx_entity_id = entity_id.replace("sensor.", "")

        flux_query = f"""
from(bucket: "{bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["entity_id"] == "{influx_entity_id}")
  |> filter(fn: (r) => r["_field"] == "value")
  |> filter(fn: (r) => r["_measurement"] == "°C")
  |> sort(columns: ["_time"])
  |> yield(name: "data")
"""

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

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
                    return self._parse_influxdb_csv(csv_text)
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
    # Schedule helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_schedule_info(self, room: RoomConfig) -> list[dict]:
        """Read schedule helper states and return schedule info list."""
        schedules = []
        for entity_id in room.schedule_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("Schedule entity %s not found", entity_id)
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
        rate = max(MIN_HEATING_RATE, min(MAX_HEATING_RATE, rate))

        entity = self.heating_rate_entities.get(room.room_id)
        if entity:
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

    # ──────────────────────────────────────────────────────────────────
    # Daily analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_daily_analysis(self) -> None:
        """Run daily heating rate analysis for all discovered rooms."""
        _LOGGER.info("Starting daily heating analysis")

        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
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
        readings = await self.async_query_influxdb(room.temp_sensor, days=7)
        if len(readings) < 5:
            _LOGGER.warning(
                "[%s] Not enough data (%d readings) — skipping",
                room.room_name,
                len(readings),
            )
            return

        schedules = self._get_schedule_info(room)
        analysis = analyze_heating_sessions(readings, schedules)

        if not analysis["sessions"]:
            _LOGGER.warning(
                "[%s] No heating sessions detected — skipping", room.room_name
            )
            return

        state = self.hass.states.get(room.heating_rate_helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE

        prompt = build_daily_prompt(
            room_name=room.room_name,
            current_rate=current_rate,
            analysis=analysis,
            schedules=schedules,
            outside_temp=weather["outside_temp"],
            tomorrow_min=weather["tomorrow_min"],
            tomorrow_max=weather["tomorrow_max"],
            season=season,
        )

        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result or "heating_rate" not in result:
            _LOGGER.error("[%s] Invalid Ollama response: %s", room.room_name, response)
            return

        new_rate = float(result["heating_rate"])
        reasoning = result.get("reasoning", "No reasoning provided")
        confidence = result.get("confidence", "unknown")

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence

        await self._async_apply_heating_rate(room, new_rate, reasoning)

        await self._async_notify(
            f"🌡️ {room.room_name} — Heating Rate Updated",
            f"New rate: {new_rate:.3f}°C/min (was {current_rate:.3f}°C/min)\n"
            f"Confidence: {confidence}\n"
            f"Success rate last 7 days: {analysis['success_rate']}%\n"
            f"Reason: {reasoning}",
        )

        _LOGGER.info(
            "[%s] Daily analysis complete. New rate: %.3f",
            room.room_name,
            new_rate,
        )

    # ──────────────────────────────────────────────────────────────────
    # Weekly analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_weekly_analysis(self) -> None:
        """Run weekly report analysis for all discovered rooms."""
        _LOGGER.info("Starting weekly heating analysis (report only)")

        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
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
        readings = await self.async_query_influxdb(room.temp_sensor, days=30)
        if len(readings) < 5:
            _LOGGER.warning(
                "[%s] Not enough data for weekly analysis", room.room_name
            )
            return

        schedules = self._get_schedule_info(room)
        analysis = analyze_heating_sessions(readings, schedules)

        state = self.hass.states.get(room.heating_rate_helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE

        prompt = build_weekly_prompt(
            room_name=room.room_name,
            current_rate=current_rate,
            analysis=analysis,
            schedules=schedules,
            avg_outside_temp=weather["outside_temp"],
            season=season,
        )

        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result:
            _LOGGER.error(
                "[%s] Invalid weekly Ollama response: %s", room.room_name, response
            )
            return

        confidence = result.get("confidence", "unknown")
        weekly_report = result.get("weekly_report", "No weekly report generated.")
        suggested_rate = result.get("heating_rate", current_rate)
        reasoning = result.get("reasoning", "")

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence
        self.room_states[room.room_id]["weekly_report"] = weekly_report

        await self.async_update_sensors()

        report_date = datetime.now().strftime("%Y-%m-%d")
        await self._async_persistent_notification(
            title=f"📊 {room.room_name} — Weekly Heating Report {report_date}",
            message=(
                f"## Weekly Summary\n"
                f"{weekly_report}\n\n"
                f"## Statistics (last 30 days)\n"
                f"- Sessions analyzed: {analysis['days_analyzed']}\n"
                f"- Success rate: {analysis['success_rate']}%\n"
                f"- Avg heating rate observed: {analysis.get('avg_rate', 'unknown')}°C/min\n"
                f"- Avg pre-heat start time: {analysis.get('avg_start_time', 'unknown')}\n"
                f"- Outside temp: {weather['outside_temp']}°C ({season})\n\n"
                f"## AI Suggestion\n"
                f"Suggested rate: {suggested_rate:.3f}°C/min (current: {current_rate:.3f}°C/min)\n"
                f"Confidence: {confidence}\n"
                f"Reasoning: {reasoning}\n\n"
                f"_Rate is NOT automatically adjusted by the weekly report. "
                f"Apply manually if you agree with the suggestion._"
            ),
            notification_id=f"sha_{room.room_id}_weekly_report",
        )

        _LOGGER.info(
            "[%s] Weekly report complete. Suggested rate: %.3f",
            room.room_name,
            suggested_rate,
        )
