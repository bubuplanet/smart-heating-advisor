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
        self.override_notified = f"switch.sha_{self.room_id}_override_notified"

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

        if not room_name or not temp_sensor:
            _LOGGER.debug("Room registry register skipped: missing room_name or temp_sensor")
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

            if not room_name or not temp_sensor:
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
                "Run a SHA blueprint automation once to auto-register the room."
            )

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
        clamped = max(MIN_HEATING_RATE, min(MAX_HEATING_RATE, rate))
        if clamped != rate:
            _LOGGER.debug(
                "[%s] AI rate %.3f clamped to [%.3f, %.3f] → %.3f",
                room.room_name, rate, MIN_HEATING_RATE, MAX_HEATING_RATE, clamped,
            )
        rate = clamped

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

        schedules = self._get_schedule_info(room)
        _LOGGER.debug("[%s] Schedules found: %d — %s", room.room_name, len(schedules), [s["name"] for s in schedules])
        analysis = analyze_heating_sessions(readings, schedules)
        _LOGGER.debug(
            "[%s] Analysis: %d session(s), success_rate=%s%%, avg_rate=%s",
            room.room_name,
            len(analysis.get("sessions", [])),
            analysis.get("success_rate", "?"),
            analysis.get("avg_rate", "?"),
        )

        if not analysis["sessions"]:
            _LOGGER.warning(
                "[%s] No heating sessions detected — skipping", room.room_name
            )
            await self._async_notify_daily_room_result(
                room=room,
                run_ts=run_ts,
                old_rate=None,
                new_rate=None,
                success_rate=analysis.get("success_rate"),
                outcome=f"No changes needed in {room.room_name}.",
                details="Analysis ran but no heating sessions were detected.",
            )
            return

        state = self.hass.states.get(room.heating_rate_helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE
        _LOGGER.debug("[%s] Current heating rate: %.3f °C/min", room.room_name, current_rate)

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
        reasoning = result.get("reasoning", "No reasoning provided")
        confidence = result.get("confidence", "unknown")
        _LOGGER.debug(
            "[%s] AI result — new_rate=%.3f, confidence=%s, reasoning=%s",
            room.room_name, new_rate, confidence, reasoning,
        )

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence

        await self._async_apply_heating_rate(room, new_rate, reasoning)

        await self._async_notify_daily_room_result(
            room=room,
            run_ts=run_ts,
            old_rate=current_rate,
            new_rate=new_rate,
            success_rate=analysis.get("success_rate"),
            outcome=self._format_daily_outcome(room.room_name, current_rate, new_rate),
            details=reasoning,
        )

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
