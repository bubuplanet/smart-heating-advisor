"""Coordinator for Smart Heating Advisor."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .number import SHAHeatingRateNumber, SHATRVSetpointNumber
    from .switch import SHAOverrideSwitch
    from .binary_sensor import SHAWindowOpenBinarySensor, SHAVacationBinarySensor

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    CONF_OLLAMA_URL,
    CONF_OLLAMA_MODEL,
    CONF_INFLUXDB_URL,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_BUCKET,
    CONF_WEATHER_ENTITY,
    CONF_VACATION_ENABLED,
    CONF_VACATION_CALENDAR,
    CONF_VACATION_MODE,
    DEFAULT_HEATING_RATE,
    DEFAULT_VACATION_MODE,
    DEFAULT_DEFAULT_TEMP,
    MIN_HEATING_RATE,
    MAX_HEATING_RATE,
    DEFAULT_TRV_SETPOINT,
    MIN_TRV_SETPOINT,
    MAX_TRV_SETPOINT,
    OLLAMA_TIMEOUT,
)
from .ollama import OllamaClient
from .analyzer import (
    analyze_heating_sessions,
    analyze_sessions_per_schedule,
    build_humidity_analysis_text,
    build_schedule_lines,
    build_schedule_on_periods,
    build_schedules_analysis_text,
    build_sessions_table,
    build_sessions_text,
    build_weekly_accuracy_summary,
    detect_all_trvs_active_since,
    detect_heating_sessions_from_hvac,
    detect_standby_temp,
    extract_temp_from_schedule_name,
    get_season,
    match_sessions_to_schedules,
)
from .prompt_loader import load_prompt

_LOGGER = logging.getLogger(__name__)


def _room_name_to_id(room_name: str) -> str:
    """Convert room name to snake_case room ID.

    Examples:
        Bathroom           → bathroom
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


# Plain-language descriptions of weekly root-cause codes
_ROOT_CAUSE_PLAIN: dict[str, str] = {
    "HARDWARE_INSUFFICIENT": "heater may be underpowered for this room",
    "PREHEAT_TOO_SHORT": "pre-heat starting too late",
    "TRV_SETPOINT_TOO_LOW": "radiator setpoint needs to be higher",
    "HEAT_LOSS_HIGH": "possible heat loss — check windows and insulation",
    "RECENT_DEGRADATION": "recent drop in performance — something may have changed",
}


def _get_measurement(entity_id: str) -> str:
    """Auto-derive InfluxDB measurement name from entity_id.

    Matches the confirmed production schema exactly:
    - ``sensor.*_temperature`` → ``"°C"``
    - ``sensor.*_humidity``    → ``"%"``
    - ``climate.*``            → full entity_id (e.g. ``"climate.bathroom_radiator"``)
    - ``schedule.*``           → full entity_id (e.g. ``"schedule.weekday_morning_shower_26c"``)
    - anything else            → ``"°C"`` (safe fallback)
    """
    if entity_id.startswith("sensor.") and entity_id.endswith("_temperature"):
        return "°C"
    if entity_id.startswith("sensor.") and entity_id.endswith("_humidity"):
        return "%"
    if entity_id.startswith("climate."):
        return entity_id
    if entity_id.startswith("schedule."):
        return entity_id
    return "°C"


def _strip_domain(entity_id: str) -> str:
    """Strip the HA domain prefix from an entity_id for use in InfluxDB filters.

    Examples:
        sensor.bathroom_thermostat_temperature → bathroom_thermostat_temperature
        climate.bathroom_radiator              → bathroom_radiator
        schedule.weekday_morning_shower_26c    → weekday_morning_shower_26c
    """
    return entity_id.split(".", 1)[1] if "." in entity_id else entity_id


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


def _do_read_automation_trvs(config_dir: str, room_name: str) -> list[str]:
    """Read TRV entity IDs from automations.yaml for a given room.

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
        _LOGGER.debug("Could not read automations.yaml for TRV lookup: %s", exc)
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

        auto_room = inputs.get("room_name", "")
        if _room_name_to_id(str(auto_room)) != room_id:
            continue

        trvs = inputs.get("radiator_thermostats", [])
        if isinstance(trvs, str):
            return [trvs] if trvs.strip() else []
        if isinstance(trvs, list):
            return [str(t).strip() for t in trvs if str(t).strip()]
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
        self.trv_setpoint_helper = f"number.sha_{self.room_id}_trv_setpoint"
        self.override_switch = f"switch.sha_{self.room_id}_override"
        self.airing_mode = f"switch.sha_{self.room_id}_airing_mode"

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

        # TRV setpoint entity references — keyed by room_id, populated by number.async_setup_entry
        self.trv_setpoint_entities: dict[str, "SHATRVSetpointNumber"] = {}

        # Binary sensor entity references — keyed by room_id, populated by binary_sensor.async_setup_entry
        self.window_open_entities: dict[str, "SHAWindowOpenBinarySensor"] = {}
        # Global vacation entity — one for all rooms
        self._vacation_entity: "SHAVacationBinarySensor | None" = None

        # State change listener unsubscribe callbacks — cancelled on unload
        self._unsub_callbacks: list = []

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

    def register_trv_setpoint_entity(self, room_id: str, entity: "SHATRVSetpointNumber") -> None:
        """Store a reference to a room's TRV setpoint entity for direct updates."""
        self.trv_setpoint_entities[room_id] = entity

    def register_window_open_entity(self, room_id: str, entity: "SHAWindowOpenBinarySensor") -> None:
        """Store a reference to a room's window open binary sensor for direct updates."""
        self.window_open_entities[room_id] = entity

    def register_vacation_entity(self, entity: "SHAVacationBinarySensor") -> None:
        """Store the global vacation binary sensor reference."""
        self._vacation_entity = entity

    # ──────────────────────────────────────────────────────────────────
    # Runtime state listeners
    # ──────────────────────────────────────────────────────────────────

    async def async_start_listeners(self) -> None:
        """Subscribe to window sensor and vacation state changes.

        Called once from async_setup_entry in __init__.py after all platforms
        have finished loading (i.e. after async_forward_entry_setups returns),
        so all binary sensor entity references are guaranteed to be populated.
        """
        # ── Window sensor listeners (per room) ────────────────────────
        for subentry in self.entry.subentries.values():
            room_name = subentry.data.get("room_name", "").strip()
            if not room_name:
                continue
            room_id = _room_name_to_id(room_name)
            window_sensors: list[str] = list(subentry.data.get("window_sensors", []))

            if not window_sensors:
                _LOGGER.debug(
                    "[%s] No window sensors configured — window_open sensor will always be off",
                    room_name,
                )
                continue

            # Push initial state from current HA states
            any_open = any(
                (s := self.hass.states.get(sid)) is not None
                and s.state in ("on", "open")
                for sid in window_sensors
            )
            entity = self.window_open_entities.get(room_id)
            if entity:
                entity.set_window_open(any_open)

            # Closure captures room_id, room_name, and the sensor list
            def _make_window_handler(r_id: str, r_name: str, sensors: list[str]):
                async def _handle(event: Event) -> None:
                    triggered = event.data.get("entity_id", "")
                    new_st = event.data.get("new_state")
                    new_st_str = new_st.state if new_st else "unavailable"
                    recalc = any(
                        (s := self.hass.states.get(sid)) is not None
                        and s.state in ("on", "open")
                        for sid in sensors
                    )
                    ent = self.window_open_entities.get(r_id)
                    if ent:
                        ent.set_window_open(recalc)
                    _LOGGER.info(
                        "[%s] Window state changed: open=%s triggered by %s → %s",
                        r_name, recalc, triggered, new_st_str,
                    )
                return _handle

            unsub = async_track_state_change_event(
                self.hass, window_sensors, _make_window_handler(room_id, room_name, window_sensors)
            )
            self._unsub_callbacks.append(unsub)
            _LOGGER.debug(
                "[%s] Subscribed to %d window sensor(s): %s",
                room_name, len(window_sensors), window_sensors,
            )

        # ── Vacation state (global) ───────────────────────────────────
        await self._async_update_vacation_state(source="init")

        vacation_calendar = self._get_vacation_config().get(CONF_VACATION_CALENDAR, "")
        if vacation_calendar:
            async def _handle_vacation_calendar(event: Event) -> None:
                new_st = event.data.get("new_state")
                new_st_str = new_st.state if new_st else "unavailable"
                await self._async_update_vacation_state(
                    source=f"calendar → {new_st_str}"
                )

            unsub = async_track_state_change_event(
                self.hass, [vacation_calendar], _handle_vacation_calendar
            )
            self._unsub_callbacks.append(unsub)
            _LOGGER.debug("[SHA] Subscribed to vacation calendar: %s", vacation_calendar)

    def async_stop_listeners(self) -> None:
        """Cancel all state change subscriptions. Safe to call from the event loop."""
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()
        _LOGGER.debug("[SHA] All state change listeners cancelled")

    def _get_vacation_config(self) -> dict:
        """Return vacation config from the vacation subentry, falling back to options.

        Subentry (created in Phase 6) takes priority.  Options fallback keeps
        backwards compatibility for installations that stored vacation in options.
        """
        for s in self.entry.subentries.values():
            if s.subentry_type == "vacation":
                return dict(s.data)
        return {
            CONF_VACATION_ENABLED: self.entry.options.get(CONF_VACATION_ENABLED, False),
            CONF_VACATION_MODE: self.entry.options.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
            CONF_VACATION_CALENDAR: self.entry.options.get(CONF_VACATION_CALENDAR, ""),
        }

    async def _async_update_vacation_state(self, source: str = "init") -> None:
        """Compute vacation active flag and push to every room vacation sensor."""
        vac = self._get_vacation_config()
        vacation_enabled = vac.get(CONF_VACATION_ENABLED, False)
        vacation_calendar = vac.get(CONF_VACATION_CALENDAR, "")

        if vacation_calendar:
            cal_state = self.hass.states.get(vacation_calendar)
            vacation_active = cal_state is not None and cal_state.state == "on"
        elif vacation_enabled:
            vacation_active = True
        else:
            vacation_active = False

        if self._vacation_entity is not None:
            self._vacation_entity.set_vacation(vacation_active)

        if source == "init":
            _LOGGER.debug("[SHA] Initial vacation state: active=%s", vacation_active)
        else:
            _LOGGER.info(
                "[SHA] Vacation state changed: active=%s source=%s",
                vacation_active, source,
            )

    async def async_get_room_config(self, room_id: str) -> dict:
        """Return full room configuration for blueprint service call.

        Data sources (in priority order):
        - SubEntry data   → TRVs, sensors, window/humidity config
        - Room registry   → schedules, temp_sensor (written by sha.register_room)
        - Entry options   → vacation mode/active
        - Vacation entity → live vacation active state
        """
        subentry_data: dict = {}
        for s in self.entry.subentries.values():
            if _room_name_to_id(s.data.get("room_name", "")) == room_id:
                subentry_data = dict(s.data)
                break

        room_data = self._room_registry.get(room_id, {})
        room_name = (subentry_data.get("room_name") or room_data.get("room_name", "")).strip()
        temp_sensor = str(
            subentry_data.get("thermostat_sensor") or room_data.get("temp_sensor", "")
        ).strip()
        schedules = room_data.get("schedules", [])
        if isinstance(schedules, str):
            schedules = [schedules] if schedules.strip() else []
        elif not isinstance(schedules, list):
            schedules = []

        trvs = list(subentry_data.get("trvs", []))
        fixed_trvs = list(subentry_data.get("fixed_trvs", []))
        fixed_trv_temp = float(subentry_data.get("fixed_trv_temp", DEFAULT_DEFAULT_TEMP))
        default_temp_enabled = bool(subentry_data.get("default_temp_enabled", True))
        default_temp = float(subentry_data.get("default_temp", DEFAULT_DEFAULT_TEMP))

        vac_cfg = self._get_vacation_config()
        vacation_mode = vac_cfg.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE)
        vacation_active = (
            self._vacation_entity.is_on if self._vacation_entity is not None else False
        )
        vacation_temp_map = {"frost": 7.0, "eco": 15.0, "off": 18.0}
        vacation_temp = vacation_temp_map.get(vacation_mode, 7.0)

        _LOGGER.debug(
            "[%s] get_room_config: trvs=%s schedules=%s vacation_active=%s vacation_mode=%s",
            room_id, trvs, schedules, vacation_active, vacation_mode,
        )

        return {
            "room_id": room_id,
            "room_name": room_name,
            "temp_sensor": temp_sensor,
            "schedules": schedules,
            "trvs": trvs,
            "fixed_trvs": fixed_trvs,
            "fixed_trv_temp": fixed_trv_temp,
            "default_hvac_mode": "heat" if default_temp_enabled else "off",
            "default_temp": default_temp,
            "vacation_active": vacation_active,
            "vacation_mode": vacation_mode,
            "vacation_temp": vacation_temp,
        }

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

        entity_id_bare = _strip_domain(entity_id)

        flux_query = (
            f'from(bucket: "{bucket}")\n'
            f"  |> range(start: -{days}d)\n"
            f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
            f'  |> filter(fn: (r) => r["entity_id"] == "{entity_id_bare}")\n'
            f'  |> filter(fn: (r) => r["_field"] == "{field}")\n'
            f'  |> sort(columns: ["_time"])\n'
            f'  |> yield(name: "data")\n'
        )

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

        _LOGGER.debug(
            "InfluxDB: entity='%s' measurement='%s' field='%s' days=%d",
            entity_id, measurement, field, days,
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
                            "InfluxDB query failed for %s: HTTP %s",
                            entity_id, response.status,
                        )
                        return []
                    csv_text = await response.text()
                    readings = self._parse_influxdb_csv(csv_text)
                    _LOGGER.debug(
                        "InfluxDB: entity='%s' → %d reading(s)",
                        entity_id, len(readings),
                    )
                    return readings
        except Exception as exc:
            _LOGGER.error("InfluxDB query error for %s: %s", entity_id, exc)
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

    async def _async_get_room_trvs_with_fallback(self, room: RoomConfig) -> list[str]:
        """Return TRV entity IDs, falling back to automations.yaml when subentry is empty."""
        trvs = self._get_room_trvs(room)
        if trvs:
            return trvs
        config_dir = self.hass.config.config_dir
        trvs = await self.hass.async_add_executor_job(
            _do_read_automation_trvs, config_dir, room.room_name
        )
        if trvs:
            _LOGGER.debug(
                "[%s] TRVs not in subentry — loaded %d TRV(s) from automations.yaml: %s",
                room.room_name, len(trvs), trvs,
            )
        else:
            _LOGGER.debug("[%s] No TRVs found in subentry or automations.yaml", room.room_name)
        return trvs

    async def async_query_trv_readings(
        self, room: RoomConfig, days: int
    ) -> dict[str, list[tuple]]:
        """Query InfluxDB setpoint history for every TRV in the room.

        Returns a dict mapping entity_id → list of (datetime, setpoint) tuples.
        Climate setpoints are stored in InfluxDB as field ``temperature``
        (the attribute name) in measurement ``°C``.
        """
        trvs = await self._async_get_room_trvs_with_fallback(room)
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
    # InfluxDB v2 — multi-field, correct-measurement queries
    # ──────────────────────────────────────────────────────────────────

    def _parse_influxdb_csv_multi(self, csv_text: str) -> dict:
        """Parse InfluxDB CSV response into dict[field_name → list[(datetime, value)]].

        Handles both numeric and string values (e.g. ``hvac_action_str``).
        Routes rows by the ``_field`` column in the CSV header.
        """
        result: dict = {}
        lines = csv_text.strip().split("\n")
        time_idx: int | None = None
        value_idx: int | None = None
        field_idx: int | None = None

        for line in lines:
            if not line or line.startswith("#"):
                # Each table in InfluxDB CSV is preceded by annotations (#datatype …)
                # followed by a fresh header row — reset indices on blank lines so
                # the next header row is picked up correctly.
                if not line:
                    time_idx = value_idx = field_idx = None
                continue

            parts = line.split(",")

            if "_time" in parts and "_value" in parts:
                time_idx = parts.index("_time")
                value_idx = parts.index("_value")
                field_idx = parts.index("_field") if "_field" in parts else None
                continue

            if time_idx is None or value_idx is None:
                continue

            try:
                ts_str = parts[time_idx].strip().replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
                raw_val = parts[value_idx].strip()
                try:
                    val: object = float(raw_val)
                except ValueError:
                    val = raw_val
                fname = parts[field_idx].strip() if field_idx is not None else "value"
                result.setdefault(fname, []).append((ts, val))
            except (ValueError, IndexError):
                continue

        for fname in result:
            result[fname].sort(key=lambda x: x[0])

        return result

    async def async_query_influxdb_v2(
        self,
        entity_id: str,
        days: int,
        fields: list[str],
        measurement: str | None = None,
    ) -> dict:
        """Query InfluxDB for multiple fields using the confirmed schema.

        Auto-derives measurement from entity_id unless overridden.
        Includes the entity_id tag filter for all entity types.
        For ``climate.*`` and ``schedule.*`` the measurement filter already
        disambiguates uniquely but the entity_id filter is included for
        consistency and correctness.

        Returns dict[field_name → list[(datetime, value)]].
        """
        import aiohttp

        token = self.config[CONF_INFLUXDB_TOKEN]
        url = self.config[CONF_INFLUXDB_URL]
        org = self.config[CONF_INFLUXDB_ORG]
        bucket = self.config[CONF_INFLUXDB_BUCKET]

        if measurement is None:
            measurement = _get_measurement(entity_id)

        entity_id_bare = _strip_domain(entity_id)
        field_filter = " or ".join(f'r["_field"] == "{f}"' for f in fields)

        flux_query = (
            f'from(bucket: "{bucket}")\n'
            f"  |> range(start: -{days}d)\n"
            f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
            f'  |> filter(fn: (r) => r["entity_id"] == "{entity_id_bare}")\n'
            f'  |> filter(fn: (r) => {field_filter})\n'
            f'  |> sort(columns: ["_time"])\n'
            f'  |> yield(name: "data")\n'
        )

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

        _LOGGER.debug(
            "InfluxDB v2: entity='%s' measurement='%s' entity_bare='%s' fields=%s days=%d",
            entity_id, measurement, entity_id_bare, fields, days,
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
                            "InfluxDB v2 query failed for %s: status %s",
                            entity_id, response.status,
                        )
                        return {}
                    csv_text = await response.text()
                    result = self._parse_influxdb_csv_multi(csv_text)
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "InfluxDB v2: '%s' → %s",
                            entity_id, {k: len(v) for k, v in result.items()},
                        )
                    return result
        except Exception as exc:
            _LOGGER.error("InfluxDB v2 query error for %s: %s", entity_id, exc)
            return {}

    async def async_query_trv_data_full(
        self, room: "RoomConfig", days: int
    ) -> dict:
        """Query temperature, current_temperature, and hvac_action_str for all TRVs.

        Uses the confirmed schema: measurement = full entity_id (e.g.
        ``climate.bathroom_radiator``), no entity_id tag filter.

        Returns dict[entity_id → dict[field → list[(datetime, value)]]].
        """
        trvs = await self._async_get_room_trvs_with_fallback(room)
        if not trvs:
            _LOGGER.debug("[%s] No TRV entities — skipping TRV data query", room.room_name)
            return {}

        result: dict = {}
        for trv_entity_id in trvs:
            fields_data = await self.async_query_influxdb_v2(
                trv_entity_id,
                days,
                fields=["temperature", "current_temperature", "hvac_action_str"],
            )
            _LOGGER.debug(
                "[%s] TRV %s: %s",
                room.room_name, trv_entity_id,
                {k: len(v) for k, v in fields_data.items()},
            )
            result[trv_entity_id] = fields_data

        return result

    async def async_query_schedule_data(
        self, schedule_entity_ids: list[str], days: int
    ) -> dict:
        """Query ON periods for each schedule from InfluxDB.

        Uses confirmed schema: measurement = full schedule entity_id.

        Returns dict[entity_id → list[{start, end}]].
        """
        result: dict = {}
        for entity_id in schedule_entity_ids:
            fields_data = await self.async_query_influxdb_v2(
                entity_id, days, fields=["state"],
            )
            state_readings = fields_data.get("state", [])
            on_periods = build_schedule_on_periods(state_readings)
            _LOGGER.debug(
                "Schedule %s: %d state readings → %d ON period(s) over %d days",
                entity_id, len(state_readings), len(on_periods), days,
            )
            result[entity_id] = on_periods
        return result

    def _get_room_humidity_sensor(self, room: "RoomConfig") -> str | None:
        """Return humidity sensor entity_id for the room, if configured.

        Checks subentry data first.  Falls back to auto-deriving by replacing
        ``_temperature`` with ``_humidity`` in the room temp sensor entity_id
        (only if the derived entity is present in HA states).
        """
        for subentry in self.entry.subentries.values():
            sub_room = subentry.data.get("room_name", "")
            if _room_name_to_id(str(sub_room)) == room.room_id:
                humidity = subentry.data.get("humidity_sensor")
                if humidity:
                    return str(humidity)

        sensor = room.temp_sensor
        if "_temperature" in sensor:
            candidate = sensor.replace("_temperature", "_humidity")
            if self.hass.states.get(candidate):
                return candidate

        return None

    async def async_query_humidity_data(self, room: "RoomConfig", days: int) -> list[tuple]:
        """Query humidity readings for the room (measurement ``%``, field ``value``)."""
        humidity_sensor = self._get_room_humidity_sensor(room)
        if not humidity_sensor:
            return []
        return await self.async_query_influxdb(
            humidity_sensor, days, field="value", measurement="%"
        )

    @staticmethod
    def _detect_all_trvs_active_since_from_hvac(trv_data: dict) -> "datetime | None":
        """Return the latest 'first heating reading' timestamp across all TRVs.

        "First heating" means the earliest timestamp where hvac_action_str == "heating".
        Returns None if any TRV has no hvac_action_str data OR never reported "heating"
        (treats the whole dataset as unreliable until all TRVs confirm active heating).
        """
        if not trv_data:
            return None
        latest: "datetime | None" = None
        for entity_id, fields in trv_data.items():
            hvac_readings = fields.get("hvac_action_str", [])
            if not hvac_readings:
                _LOGGER.debug(
                    "TRV %s has no hvac_action_str data — treating as unreliable",
                    entity_id,
                )
                return None
            heating_ts = [ts for ts, val in hvac_readings if str(val).lower() == "heating"]
            if not heating_ts:
                _LOGGER.debug(
                    "TRV %s never reported 'heating' in queried window — treating as unreliable",
                    entity_id,
                )
                return None
            first_ts = min(heating_ts)
            if latest is None or first_ts > latest:
                latest = first_ts
        return latest

    def _enrich_schedules_info(self, schedules: list[dict]) -> list[dict]:
        """Add ``target_temp`` and ``schedule_time`` to each schedule info dict."""
        result = []
        for s in schedules:
            name = s.get("name", "")
            target_temp = extract_temp_from_schedule_name(name, 21.0)
            schedule_time = "n/a"
            next_event = s.get("next_event")
            if next_event:
                try:
                    dt = datetime.fromisoformat(str(next_event).replace("Z", "+00:00"))
                    schedule_time = dt.strftime("%H:%M")
                except (ValueError, TypeError):
                    pass
            result.append({**s, "target_temp": target_temp, "schedule_time": schedule_time})
        return result

    # ──────────────────────────────────────────────────────────────────
    # Schedule helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_schedule_info(self, room: "RoomConfig") -> list[dict]:
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

    def _get_trv_max_temp(self, room: "RoomConfig", trvs: list[str]) -> float:
        """Return the minimum max_temp reported across all TRVs for this room.

        Reads live from HA climate entity attributes.  Uses MAX_TRV_SETPOINT
        (35°C) as a safe default when no TRV state is available.  The minimum
        across all TRVs is used so the recommendation never exceeds any single
        TRV's hardware limit.
        """
        max_temps: list[float] = []
        for trv_entity_id in trvs:
            state = self.hass.states.get(trv_entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                val = state.attributes.get("max_temp")
                if isinstance(val, (int, float)):
                    max_temps.append(float(val))
        result = min(max_temps) if max_temps else MAX_TRV_SETPOINT
        _LOGGER.debug("[%s] TRV max_temp: %.1f°C (from %d TRV(s))", room.room_name, result, len(max_temps))
        return result

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

    async def _async_apply_trv_setpoint(
        self,
        room: "RoomConfig",
        setpoint: float,
        avg_gradient: float | None,
        target_comfort: float,
    ) -> None:
        """Write the recommended TRV setpoint to the SHA entity."""
        clamped = max(MIN_TRV_SETPOINT, min(MAX_TRV_SETPOINT, setpoint))
        entity = self.trv_setpoint_entities.get(room.room_id)
        if entity:
            await entity.async_set_native_value(round(clamped, 1))
        else:
            _LOGGER.warning(
                "[%s] No TRV setpoint entity registered — falling back to service call",
                room.room_name,
            )
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": room.trv_setpoint_helper, "value": round(clamped, 1)},
            )
        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["trv_setpoint"] = clamped
        gradient_str = f"{avg_gradient:.1f}" if avg_gradient is not None else "unknown"
        _LOGGER.info(
            "[%s] TRV setpoint updated to %.1f°C (gradient %s°C, target %.1f°C)",
            room.room_name, clamped, gradient_str, target_comfort,
        )

    async def _async_check_stale_data(
        self, room: "RoomConfig", readings: list
    ) -> bool:
        """Check whether InfluxDB data for this room is stale (> 48 h old).

        Returns True when analysis should be skipped (no data or data is stale).
        Returns False when data is fresh.
        """
        if not readings:
            _LOGGER.warning(
                "[%s] InfluxDB returned no data — is InfluxDB running? "
                "Check that InfluxDB is recording %s correctly. Analysis skipped.",
                room.room_name, room.temp_sensor,
            )
            return True

        last_ts = readings[-1][0]
        now_utc = datetime.now(timezone.utc)
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        hours_since = (now_utc - last_ts).total_seconds() / 3600

        if hours_since > 48:
            last_reading_time = last_ts.strftime("%Y-%m-%d %H:%M UTC")
            _LOGGER.warning(
                "[%s] InfluxDB data is stale — last reading: %s (%d hours ago). "
                "SHA cannot run accurate analysis. "
                "Check that InfluxDB is recording %s correctly. Analysis skipped.",
                room.room_name, last_reading_time, int(hours_since), room.temp_sensor,
            )
            return True

        return False

    async def _async_check_automation_enabled(self, room: "RoomConfig") -> bool:
        """Check whether the SHA blueprint automation for this room is enabled.

        Returns True when analysis should be skipped (automation disabled).
        Returns False when enabled — or when no automation is found (room
        may be newly configured; analysis still runs in that case).
        """
        alias = f"SHA — {room.room_name}"

        automation_state = None
        for state in self.hass.states.async_all("automation"):
            if state.attributes.get("friendly_name") == alias:
                automation_state = state
                break

        if automation_state is None:
            _LOGGER.warning(
                "[%s] No SHA automation found (alias: '%s') — "
                "analysis will still run (automation may not yet be configured).",
                room.room_name, alias,
            )
            return False

        if automation_state.state == "off":
            _LOGGER.warning(
                "[%s] SHA automation is disabled — skipping analysis. "
                "Pre-heat will not run until the automation is re-enabled.",
                room.room_name,
            )
            return True

        return False

    async def _async_check_radiator_capacity(
        self,
        room: "RoomConfig",
        per_schedule: dict,
        current_rate: float,
        avg_rate: float | None,
    ) -> None:
        """Check per-schedule recommended_preheat_min and warn if > 180 min."""
        for eid, sched_stats in per_schedule.items():
            preheat_min = sched_stats.get("recommended_preheat_min")
            sched_name = sched_stats.get("name", eid)
            target_temp = sched_stats.get("target_temp", 21.0)

            if preheat_min is None:
                continue

            if preheat_min > 180:
                observed_str = f"{avg_rate:.3f}" if avg_rate is not None else "unknown"
                _LOGGER.warning(
                    "[%s] Radiator may be underpowered — recommended pre-heat is "
                    "%d minutes for schedule '%s'. "
                    "Target %.0f°C may not be achievable. "
                    "Current rate: %.2f°C/min, observed: %s°C/min.",
                    room.room_name, int(preheat_min), sched_name,
                    target_temp, current_rate, observed_str,
                )

    def _format_daily_outcome(self, room_name: str, old_rate: float, new_rate: float) -> str:
        """Return one-line daily outcome summary."""
        delta = new_rate - old_rate
        if abs(delta) < 0.0005:
            return f"No changes needed in {room_name}."
        if delta > 0:
            return f"Heating increased by {delta:.3f} °C/min in {room_name}."
        return f"Heating decreased by {abs(delta):.3f} °C/min in {room_name}."

    def _async_log_weekly_summary(self, room_results: list, date_str: str) -> None:
        """Log overall SHA weekly summary after all rooms are processed."""
        if not room_results:
            return

        good_rooms = [(r, s) for r, s in room_results if not s.get("consistent_miss")]
        bad_rooms = [(r, s) for r, s in room_results if s.get("consistent_miss")]
        total_rooms = len(room_results)
        good_count = len(good_rooms)

        lines = [f"[SHA] Weekly summary {date_str}: {good_count}/{total_rooms} rooms on target."]
        for room, stats in bad_rooms:
            rc = stats.get("root_cause") or ""
            rc_plain = _ROOT_CAUSE_PLAIN.get(rc, rc or "unknown")
            lines.append(f"  ⚠ {room.room_name}: {rc_plain}")
        for room, stats in good_rooms:
            sc = stats.get("session_count", 0)
            ot = stats.get("on_target", 0)
            lines.append(f"  ✓ {room.room_name}: {ot}/{sc} sessions on target")

        _LOGGER.info("\n".join(lines))

    # ──────────────────────────────────────────────────────────────────
    # Daily analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_daily_analysis(self) -> None:
        """Run daily heating rate analysis for all discovered rooms."""
        if not await self.ollama.async_test_connection():
            _LOGGER.error("[SHA] Daily analysis skipped — cannot connect to Ollama.")
            return

        rooms = self.discover_rooms()
        if not rooms:
            _LOGGER.warning("No rooms discovered — skipping daily analysis")
            return

        total = len(rooms)
        _LOGGER.info("[SHA] Daily analysis starting — %d room(s)", total)
        weather = self._get_weather_data()
        season = get_season(datetime.now().month)

        success_count = 0
        for idx, room in enumerate(rooms, start=1):
            _LOGGER.info("[%s] Analysis starting (%d of %d)", room.room_name, idx, total)
            try:
                stats = await self._async_run_daily_analysis_for_room(room, weather, season)
                new_rate = stats.get("new_rate") if stats else None
                session_count = stats.get("session_count", 0) if stats else 0
                on_target = stats.get("on_target", 0) if stats else 0
                rate_str = f"{new_rate:.3f}" if new_rate is not None else "n/a"
                _LOGGER.info(
                    "[%s] Analysis complete — rate: %s°C/min, sessions: %d, target accuracy: %d of %d",
                    room.room_name, rate_str, session_count, on_target, session_count,
                )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "[%s] Analysis failed — %s. Continuing with next room.",
                    room.room_name, exc,
                )

        _LOGGER.info("[SHA] Daily analysis complete — %d of %d rooms processed", success_count, total)

    async def _async_run_daily_analysis_for_room(
        self, room: RoomConfig, weather: dict, season: str
    ) -> None:
        """Run daily analysis for a single room (hvac_action_str pipeline)."""
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _LOGGER.debug(
            "[%s] Daily analysis — weather: outside=%.1f°C, tomorrow min=%.1f/max=%.1f°C, season=%s",
            room.room_name,
            weather["outside_temp"], weather["tomorrow_min"], weather["tomorrow_max"], season,
        )

        # ── 0. Pre-flight checks ─────────────────────────────────────
        if await self._async_check_automation_enabled(room):
            return {"new_rate": None, "session_count": 0, "on_target": 0}

        # ── 1. Room temperature ──────────────────────────────────────
        readings = await self.async_query_influxdb(room.temp_sensor, days=30)
        if await self._async_check_stale_data(room, readings):
            return {"new_rate": None, "session_count": 0, "on_target": 0}
        if len(readings) < 5:
            _LOGGER.warning(
                "[%s] Not enough room-temp data (%d readings) — skipping",
                room.room_name, len(readings),
            )
            return {"new_rate": None, "session_count": 0, "on_target": 0}

        # ── 2. TRV data (hvac_action_str + setpoints + current_temp) ─
        room_trvs = await self._async_get_room_trvs_with_fallback(room)
        trv_data = await self.async_query_trv_data_full(room, days=30)

        # Standby temp from setpoint readings (backward compat helper)
        trv_setpoint_readings = {
            eid: fields.get("temperature", []) for eid, fields in trv_data.items()
        }
        standby_temp_val = detect_standby_temp(trv_setpoint_readings) if trv_setpoint_readings else 7.0

        # ── 3. all_trvs_active_since ─────────────────────────────────
        all_trvs_active_since = self._detect_all_trvs_active_since_from_hvac(trv_data)
        if all_trvs_active_since is None and trv_setpoint_readings:
            # Fallback: use setpoint-based detection when hvac_action_str missing
            standby_threshold = standby_temp_val + 5.0
            all_trvs_active_since = detect_all_trvs_active_since(
                trv_setpoint_readings, standby_threshold
            )

        if all_trvs_active_since:
            _LOGGER.info(
                "[%s] All TRVs active since %s — sessions before flagged as partial_setup",
                room.room_name, all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
            )

        # ── 4. Schedules ─────────────────────────────────────────────
        schedules = await self._async_get_schedule_info_with_fallback(room)
        schedules_info = self._enrich_schedules_info(schedules)
        primary = self._primary_schedule_info(schedules)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "[%s] Schedules: %d — %s | primary: %s target=%.1f°C time=%s",
                room.room_name, len(schedules), [s["name"] for s in schedules],
                primary["name"], primary["target_temp"], primary["schedule_time"],
            )

        # ── 5. Schedule ON periods from InfluxDB ─────────────────────
        schedule_entity_ids = [s["entity_id"] for s in schedules_info]
        schedule_on_periods = await self.async_query_schedule_data(schedule_entity_ids, days=30)

        # ── 6. Humidity ──────────────────────────────────────────────
        humidity_readings = await self.async_query_humidity_data(room, days=30)

        # ── 7. Session detection ─────────────────────────────────────
        has_hvac_data = any(
            fields.get("hvac_action_str") for fields in trv_data.values()
        )

        if has_hvac_data:
            raw_sessions = detect_heating_sessions_from_hvac(trv_data, all_trvs_active_since)
            matched_sessions = match_sessions_to_schedules(
                raw_sessions, schedule_on_periods, schedules_info, readings, trv_data
            )
            per_schedule = analyze_sessions_per_schedule(matched_sessions, schedules_info)

            # Aggregate overall stats
            # sessions_total = all sessions (for display); on_target from full-setup only
            sessions_total = sum(s["sessions_total"] for s in per_schedule.values())
            full_setup_count = sum(s.get("full_setup_count", s["sessions_total"]) for s in per_schedule.values())
            partial_setup_count = sum(s.get("partial_setup_count", 0) for s in per_schedule.values())
            sessions_on_target = sum(s["sessions_on_target"] for s in per_schedule.values())
            sessions_with_miss = sum(s["sessions_with_miss"] for s in per_schedule.values())
            all_avg_misses = [
                s["avg_miss"] for s in per_schedule.values() if s.get("avg_miss") is not None
            ]
            average_miss = round(sum(all_avg_misses) / len(all_avg_misses), 1) if all_avg_misses else 0.0
            all_rates = [s["avg_rate"] for s in per_schedule.values() if s.get("avg_rate") is not None]
            avg_rate = round(sum(all_rates) / len(all_rates), 3) if all_rates else None
            success_rate_pct = round(sessions_on_target / full_setup_count * 100, 1) if full_setup_count else 0.0

            # Consecutive misses and trend — full-setup sessions only (accuracy metric)
            full_sorted = sorted(
                [s for s in matched_sessions if s.get("full_setup", True)],
                key=lambda s: s["date"],
            )
            all_sorted = sorted(matched_sessions, key=lambda s: s["date"])
            consecutive_misses = 0
            for s in reversed(full_sorted):
                miss = s.get("target_miss")
                if miss is not None and miss > 0.5:
                    consecutive_misses += 1
                else:
                    break
            half = len(full_sorted) // 2
            miss_trend = "stable"
            if half >= 2:
                first_m = [s["target_miss"] for s in full_sorted[:half] if s.get("target_miss") is not None]
                second_m = [s["target_miss"] for s in full_sorted[half:] if s.get("target_miss") is not None]
                if first_m and second_m:
                    fa = sum(first_m) / len(first_m)
                    sa = sum(second_m) / len(second_m)
                    if sa < fa - 0.5:
                        miss_trend = "improving"
                    elif sa > fa + 0.5:
                        miss_trend = "worsening"

            display_sessions = all_sorted[-7:]
            schedules_analysis_text = build_schedules_analysis_text(per_schedule, all_trvs_active_since)
        else:
            # Fallback: old setpoint/room-sensor pipeline
            _LOGGER.debug("[%s] No hvac_action_str data — using setpoint-based detection", room.room_name)
            analysis = analyze_heating_sessions(
                readings,
                schedules,
                schedule_time_hhmm=primary["schedule_time"] if primary["schedule_time"] != "n/a" else None,
                trv_readings=trv_setpoint_readings if trv_setpoint_readings else None,
                all_trvs_active_since=all_trvs_active_since,
                standby_temp=standby_temp_val,
                target_temp=primary["target_temp"],
            )
            matched_sessions = analysis.get("sessions", [])
            per_schedule = {}
            sessions_total = analysis.get("sessions_total", 0)
            full_setup_count = sum(1 for s in matched_sessions if s.get("full_setup", True))
            partial_setup_count = sum(1 for s in matched_sessions if not s.get("full_setup", True))
            sessions_on_target = analysis.get("sessions_on_target", 0)
            sessions_with_miss = analysis.get("sessions_with_miss", 0)
            average_miss = analysis.get("average_miss", 0.0)
            avg_rate = analysis.get("avg_rate")
            success_rate_pct = analysis.get("success_rate", 0.0)
            consecutive_misses = analysis.get("consecutive_misses", 0)
            miss_trend = analysis.get("miss_trend", "stable")
            display_sessions = matched_sessions
            schedules_analysis_text = (
                f"  (Setpoint-based detection — {sessions_on_target}/{sessions_total} sessions on target, "
                f"avg miss {average_miss:+.1f}°C)\n"
            )

        _LOGGER.debug(
            "[%s] Sessions: total=%d full_setup=%d partial=%d on_target=%d avg_rate=%s avg_miss=%s",
            room.room_name, sessions_total, full_setup_count, partial_setup_count,
            sessions_on_target, avg_rate, average_miss,
        )

        # ── 7b. TRV gradient and recommended setpoint ────────────────
        # gradient = TRV setpoint commanded during session
        #            − comfort sensor reading at schedule ON time
        # avg_gradient gives the average offset needed to compensate for
        # heat loss between the radiator and the comfort sensor location.
        target_comfort_temp = primary["target_temp"]
        trv_max_temp = self._get_trv_max_temp(room, room_trvs)

        gradients: list[float] = []
        for s in matched_sessions:
            if s.get("observed_rate") is None:
                _LOGGER.debug(
                    "[%s] Session %s excluded from rate/gradient calculation"
                    " — no valid sensor readings",
                    room.room_name, s.get("date"),
                )
                continue
            trv_t = s.get("trv_target")
            room_at_on = s.get("room_temp_at_schedule_start") or s.get("temp_at_schedule_start")
            if trv_t is not None and room_at_on is not None and trv_t > room_at_on:
                gradients.append(trv_t - room_at_on)
        avg_gradient: float | None = round(sum(gradients) / len(gradients), 1) if gradients else None

        recommended_trv_setpoint: float | None = None
        if avg_gradient is not None:
            raw = target_comfort_temp + avg_gradient
            rounded = round(raw * 2) / 2  # nearest 0.5°C (TRV step)
            recommended_trv_setpoint = min(rounded, trv_max_temp)
            _LOGGER.debug(
                "[%s] TRV gradient: avg=%.1f°C → raw %.1f°C → rounded %.1f°C → capped %.1f°C",
                room.room_name, avg_gradient, raw, rounded, recommended_trv_setpoint,
            )

        if sessions_total == 0:
            trvs_early = room_trvs
            active_since_early = (
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
                if all_trvs_active_since else "n/a"
            )
            days_reliable: int | None = None
            if all_trvs_active_since:
                days_reliable = (datetime.now(timezone.utc) - all_trvs_active_since).days

            state = self.hass.states.get(room.heating_rate_helper)
            if state and state.state not in ("unavailable", "unknown"):
                current_rate = float(state.state)
            else:
                if state:
                    _LOGGER.debug(
                        "[%s] Heating rate entity unavailable "
                        "— using default %s°C/min",
                        room.room_name,
                        DEFAULT_HEATING_RATE,
                    )
                current_rate = DEFAULT_HEATING_RATE

            _LOGGER.warning(
                "[%s] No sessions detected — %s days of data available. TRVs: %s",
                room.room_name,
                days_reliable if days_reliable is not None else "unknown",
                ", ".join(trvs_early) if trvs_early else "none",
            )

            if days_reliable is not None and days_reliable < 7:
                phase_instruction = (
                    f"SHA is still in the learning phase — only {days_reliable} day(s) of "
                    f"reliable TRV data available. Explain to the homeowner that the system "
                    f"will improve automatically as more data is collected."
                )
            else:
                phase_instruction = (
                    f"Reliable TRV data has been available for "
                    f"{days_reliable if days_reliable is not None else 'an unknown number of'} "
                    f"days, but no heating sessions were detected. This is unexpected. "
                    f"Flag to the homeowner that TRVs may not be reporting hvac_action_str "
                    f"to InfluxDB correctly, and suggest checking the InfluxDB integration."
                )

            no_session_prompt = (
                f"No heating sessions detected yet for {room.room_name}.\n"
                f"TRVs configured: {', '.join(trvs_early) if trvs_early else 'none'}\n"
                f"Reliable data since: {active_since_early}\n"
                f"Days of reliable data: {days_reliable if days_reliable is not None else 'unknown'}\n\n"
                f"{phase_instruction}\n\n"
                f"Current heating rate: {current_rate:.3f}°C/min — keep unchanged, no session data to adjust from.\n\n"
                f"Respond ONLY with a valid JSON object:\n"
                f"{{\n"
                f'  "heating_rate": {current_rate:.3f},\n'
                f'  "rate_adjustment_reason": "No sessions detected — heating rate unchanged",\n'
                f'  "target_accuracy_percent": null,\n'
                f'  "average_miss_celsius": null,\n'
                f'  "confidence": "low",\n'
                f'  "recommendation": "Wait for more data before making adjustments",\n'
                f'  "analysis_summary": "2-3 sentence plain-language message to the homeowner"\n'
                f"}}"
            )

            response = await self.ollama.async_generate(no_session_prompt)
            no_session_result = await self.ollama.async_parse_json_response(response)

            if no_session_result:
                details_text = (
                    no_session_result.get("analysis_summary")
                    or no_session_result.get("recommendation")
                    or phase_instruction
                )
            else:
                details_text = phase_instruction

            _LOGGER.info(
                "[%s] Daily result: no heating sessions detected — rate unchanged at %.3f°C/min. %s",
                room.room_name, current_rate, details_text,
            )
            return {"new_rate": current_rate, "session_count": 0, "on_target": 0}

        state = self.hass.states.get(room.heating_rate_helper)
        if state and state.state not in ("unavailable", "unknown"):
            current_rate = float(state.state)
        else:
            if state:
                _LOGGER.debug(
                    "[%s] Heating rate entity unavailable "
                    "— using default %s°C/min",
                    room.room_name,
                    DEFAULT_HEATING_RATE,
                )
            current_rate = DEFAULT_HEATING_RATE
        _LOGGER.debug("[%s] Current heating rate: %.3f °C/min", room.room_name, current_rate)

        # Read current TRV setpoint entity value
        trv_setpoint_state = self.hass.states.get(room.trv_setpoint_helper)
        current_trv_setpoint = DEFAULT_TRV_SETPOINT
        if trv_setpoint_state and trv_setpoint_state.state not in ("unavailable", "unknown"):
            try:
                current_trv_setpoint = float(trv_setpoint_state.state)
            except (ValueError, TypeError):
                pass

        trvs = room_trvs
        active_since_str = (
            all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
            if all_trvs_active_since else "n/a"
        )
        humidity_sensor_entity = self._get_room_humidity_sensor(room)
        humidity_analysis_text = build_humidity_analysis_text(humidity_readings, matched_sessions)
        learning_phase = full_setup_count < 7
        sessions_so_far = sessions_total

        prompt = await self.hass.async_add_executor_job(
            load_prompt,
            "daily_analysis.md",
            {
                "room_name": room.room_name,
                "heating_rate": round(current_rate, 3),
                "analysis_days": 30,
                "schedule_count": len(schedules),
                "schedule_lines": build_schedule_lines(schedules),
                "schedules_analysis_text": schedules_analysis_text,
                "humidity_analysis_text": humidity_analysis_text,
                "outside_temp": weather["outside_temp"],
                "tomorrow_min": weather["tomorrow_min"],
                "tomorrow_max": weather["tomorrow_max"],
                "season": season,
                "trv_entities": ", ".join(trvs) if trvs else "none configured",
                "trv_count": len(trvs),
                "standby_temp": standby_temp_val,
                "all_trvs_active_since": active_since_str,
                "full_setup_count": full_setup_count,
                "partial_setup_count": partial_setup_count,
                "session_count": sessions_total,
                "on_target_count": sessions_on_target,
                "avg_observed_rate": str(avg_rate or "n/a"),
                "humidity_sensor": humidity_sensor_entity or "not configured",
                "learning_phase": learning_phase,
                "sessions_so_far": sessions_so_far,
                # TRV setpoint and gradient
                "target_comfort_temp": target_comfort_temp,
                "trv_max_temp": round(trv_max_temp, 1),
                "current_trv_setpoint": round(current_trv_setpoint, 1),
                "avg_gradient": str(round(avg_gradient, 1)) if avg_gradient is not None else "n/a",
                "recommended_trv_setpoint": str(round(recommended_trv_setpoint, 1)) if recommended_trv_setpoint is not None else "n/a",
                # Backward-compat aliases for old /config prompt files
                "schedule_name": primary["name"],
                "target_temp": primary["target_temp"],
                "schedule_time": primary["schedule_time"],
                "sessions_table": build_sessions_table({"sessions": display_sessions}),
                "sessions_text": build_sessions_text({"sessions": display_sessions}),
                "sessions_total": sessions_total,
                "sessions_on_target": sessions_on_target,
                "sessions_with_miss": sessions_with_miss,
                "average_miss": average_miss,
                "consecutive_misses": consecutive_misses,
                "miss_trend": miss_trend,
                "current_rate": round(current_rate, 3),
                "days_analyzed": 30,
                "avg_rate": str(avg_rate or "n/a"),
                "success_rate": success_rate_pct,
                "avg_start_time": "n/a",
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
            _LOGGER.error(
                "[%s] Daily result: invalid AI response — rate unchanged at %.3f°C/min.",
                room.room_name, current_rate,
            )
            return {"new_rate": current_rate, "session_count": sessions_total, "on_target": sessions_on_target}

        new_rate = float(result["heating_rate"])
        reasoning = result.get("reasoning", result.get("rate_adjustment_reason", "No reasoning provided"))
        confidence = result.get("confidence", "unknown")
        target_accuracy_percent = result.get("target_accuracy_percent")
        average_miss_celsius = result.get("average_miss_celsius") or result.get("average_miss")
        rate_adjustment_reason = result.get("rate_adjustment_reason", reasoning)
        recommendation = result.get("recommendation", "")
        analysis_summary = result.get("analysis_summary") or result.get("daily_summary", "")

        # Fallback: if observed rate differs significantly from Ollama's
        # recommendation, apply the observed rate directly.
        if (
            avg_rate is not None
            and abs(avg_rate - new_rate) > 0.005
            and 0.005 <= avg_rate <= 0.30
        ):
            _LOGGER.info(
                "[%s] Ollama recommended %.3f°C/min but observed rate %.3f°C/min "
                "differs significantly — applying observed rate.",
                room.room_name,
                new_rate,
                avg_rate,
            )
            new_rate = avg_rate

        # Extract TRV setpoint from Ollama response; fall back to calculated recommendation
        ollama_trv_setpoint = result.get("trv_setpoint")
        if ollama_trv_setpoint is not None:
            try:
                new_trv_setpoint: float | None = min(float(ollama_trv_setpoint), trv_max_temp)
            except (TypeError, ValueError):
                _LOGGER.warning("[%s] Invalid trv_setpoint in Ollama response: %s", room.room_name, ollama_trv_setpoint)
                new_trv_setpoint = recommended_trv_setpoint
        else:
            new_trv_setpoint = recommended_trv_setpoint

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "[%s] AI result — new_rate=%.3f, trv_setpoint=%s, confidence=%s, "
                "accuracy=%s%%, avg_miss=%s°C, reasoning=%s",
                room.room_name, new_rate,
                f"{new_trv_setpoint:.1f}" if new_trv_setpoint is not None else "n/a",
                confidence, target_accuracy_percent, average_miss_celsius, reasoning,
            )

        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence
        self.room_states[room.room_id]["target_accuracy_percent"] = target_accuracy_percent
        self.room_states[room.room_id]["average_miss_celsius"] = average_miss_celsius
        self.room_states[room.room_id]["rate_adjustment_reason"] = rate_adjustment_reason
        self.room_states[room.room_id]["analysis_summary"] = analysis_summary

        await self._async_apply_heating_rate(room, new_rate, reasoning)

        if new_trv_setpoint is not None:
            await self._async_apply_trv_setpoint(room, new_trv_setpoint, avg_gradient, target_comfort_temp)

        await self._async_check_radiator_capacity(
            room, per_schedule, current_rate, avg_rate
        )

        _LOGGER.info(
            "[%s] Daily result: %s | accuracy=%s%% avg_miss=%s°C confidence=%s",
            room.room_name,
            self._format_daily_outcome(room.room_name, current_rate, new_rate),
            target_accuracy_percent, average_miss_celsius, confidence,
        )

        return {"new_rate": new_rate, "session_count": sessions_total, "on_target": sessions_on_target}

    # ──────────────────────────────────────────────────────────────────
    # Weekly analysis
    # ──────────────────────────────────────────────────────────────────

    async def async_run_weekly_analysis(self) -> None:
        """Run weekly report analysis for all discovered rooms."""
        if not await self.ollama.async_test_connection():
            _LOGGER.error("[SHA] Weekly report skipped — cannot connect to Ollama.")
            return

        rooms = self.discover_rooms()
        if not rooms:
            _LOGGER.warning("No rooms discovered — skipping weekly analysis")
            return

        total = len(rooms)
        _LOGGER.info("[SHA] Weekly analysis starting — %d room(s)", total)
        weather = self._get_weather_data()
        season = get_season(datetime.now().month)
        date_str = datetime.now().strftime("%Y-%m-%d")

        room_results: list[tuple[RoomConfig, dict]] = []
        success_count = 0
        for idx, room in enumerate(rooms, start=1):
            _LOGGER.info("[%s] Weekly analysis starting (%d of %d)", room.room_name, idx, total)
            try:
                stats = await self._async_run_weekly_analysis_for_room(room, weather, season)
                if stats:
                    room_results.append((room, stats))
                    _LOGGER.info(
                        "[%s] Weekly analysis complete — sessions: %d, on_target: %d, "
                        "consistent_miss: %s",
                        room.room_name,
                        stats.get("session_count", 0),
                        stats.get("on_target", 0),
                        stats.get("consistent_miss", False),
                    )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "[%s] Weekly analysis failed — %s. Continuing with next room.",
                    room.room_name, exc,
                )

        self._async_log_weekly_summary(room_results, date_str)
        _LOGGER.info("[SHA] Weekly analysis complete — %d of %d rooms processed", success_count, total)

    async def _async_run_weekly_analysis_for_room(
        self, room: RoomConfig, weather: dict, season: str
    ) -> dict:
        """Run weekly performance report for a single room.

        Report only — no entity updates, no rate or setpoint changes.
        Queries InfluxDB, calls Ollama for a plain-language homeowner report,
        sends a persistent notification only when the room is consistently
        missing target, and returns per-room stats for the overall summary.
        """
        _EMPTY: dict = {
            "session_count": 0, "on_target": 0,
            "consistent_miss": False, "root_cause": None,
            "report": "", "performance": "unknown",
        }
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # ── 0. Pre-flight checks ─────────────────────────────────────
        if await self._async_check_automation_enabled(room):
            return _EMPTY

        # ── 1. Room temperature ──────────────────────────────────────
        readings = await self.async_query_influxdb(room.temp_sensor, days=30)
        if await self._async_check_stale_data(room, readings):
            return _EMPTY
        if len(readings) < 5:
            _LOGGER.warning("[%s] Not enough room-temp data for weekly analysis", room.room_name)
            return _EMPTY

        # ── 2. TRV data ──────────────────────────────────────────────
        room_trvs = await self._async_get_room_trvs_with_fallback(room)
        trv_data = await self.async_query_trv_data_full(room, days=30)

        trv_setpoint_readings = {
            eid: fields.get("temperature", []) for eid, fields in trv_data.items()
        }
        standby_temp_val = detect_standby_temp(trv_setpoint_readings) if trv_setpoint_readings else 7.0

        # ── 3. all_trvs_active_since ─────────────────────────────────
        all_trvs_active_since = self._detect_all_trvs_active_since_from_hvac(trv_data)
        if all_trvs_active_since is None and trv_setpoint_readings:
            standby_threshold = standby_temp_val + 5.0
            all_trvs_active_since = detect_all_trvs_active_since(
                trv_setpoint_readings, standby_threshold
            )

        if all_trvs_active_since:
            _LOGGER.info(
                "[%s] Weekly: all TRVs active since %s — sessions before flagged as partial_setup",
                room.room_name, all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
            )

        # ── 4. Schedules ─────────────────────────────────────────────
        schedules = await self._async_get_schedule_info_with_fallback(room)
        schedules_info = self._enrich_schedules_info(schedules)
        primary = self._primary_schedule_info(schedules)

        # ── 5. Schedule ON periods ───────────────────────────────────
        schedule_entity_ids = [s["entity_id"] for s in schedules_info]
        schedule_on_periods = await self.async_query_schedule_data(schedule_entity_ids, days=30)

        # ── 6. Humidity ──────────────────────────────────────────────
        humidity_readings = await self.async_query_humidity_data(room, days=30)

        # ── 7. Session detection ─────────────────────────────────────
        has_hvac_data = any(fields.get("hvac_action_str") for fields in trv_data.values())

        if has_hvac_data:
            raw_sessions = detect_heating_sessions_from_hvac(trv_data, all_trvs_active_since)
            matched_sessions = match_sessions_to_schedules(
                raw_sessions, schedule_on_periods, schedules_info, readings, trv_data
            )
            per_schedule = analyze_sessions_per_schedule(matched_sessions, schedules_info)

            sessions_total = sum(s["sessions_total"] for s in per_schedule.values())
            full_setup_count = sum(s.get("full_setup_count", s["sessions_total"]) for s in per_schedule.values())
            partial_setup_count = sum(s.get("partial_setup_count", 0) for s in per_schedule.values())
            sessions_on_target = sum(s["sessions_on_target"] for s in per_schedule.values())
            all_avg_misses = [
                s["avg_miss"] for s in per_schedule.values() if s.get("avg_miss") is not None
            ]
            average_miss = round(sum(all_avg_misses) / len(all_avg_misses), 1) if all_avg_misses else 0.0
            all_rates = [s["avg_rate"] for s in per_schedule.values() if s.get("avg_rate") is not None]
            avg_rate = round(sum(all_rates) / len(all_rates), 3) if all_rates else None
            success_rate_pct = round(sessions_on_target / full_setup_count * 100, 1) if full_setup_count else 0.0

            # Consecutive misses and trend — full-setup sessions only
            full_sorted = sorted(
                [s for s in matched_sessions if s.get("full_setup", True)],
                key=lambda s: s["date"],
            )
            all_sorted = sorted(matched_sessions, key=lambda s: s["date"])
            consecutive_misses = 0
            for s in reversed(full_sorted):
                miss = s.get("target_miss")
                if miss is not None and miss > 0.5:
                    consecutive_misses += 1
                else:
                    break
            half = len(full_sorted) // 2
            miss_trend = "stable"
            if half >= 2:
                first_m = [s["target_miss"] for s in full_sorted[:half] if s.get("target_miss") is not None]
                second_m = [s["target_miss"] for s in full_sorted[half:] if s.get("target_miss") is not None]
                if first_m and second_m:
                    fa = sum(first_m) / len(first_m)
                    sa = sum(second_m) / len(second_m)
                    if sa < fa - 0.5:
                        miss_trend = "improving"
                    elif sa > fa + 0.5:
                        miss_trend = "worsening"

            display_sessions = all_sorted[-7:]
            schedules_analysis_text = build_schedules_analysis_text(per_schedule, all_trvs_active_since)
        else:
            _LOGGER.debug("[%s] Weekly: no hvac_action_str — using setpoint-based detection", room.room_name)
            analysis = analyze_heating_sessions(
                readings,
                schedules,
                schedule_time_hhmm=primary["schedule_time"] if primary["schedule_time"] != "n/a" else None,
                trv_readings=trv_setpoint_readings if trv_setpoint_readings else None,
                all_trvs_active_since=all_trvs_active_since,
                standby_temp=standby_temp_val,
                target_temp=primary["target_temp"],
            )
            matched_sessions = analysis.get("sessions", [])
            per_schedule = {}
            sessions_total = analysis.get("sessions_total", 0)
            full_setup_count = sum(1 for s in matched_sessions if s.get("full_setup", True))
            partial_setup_count = sum(1 for s in matched_sessions if not s.get("full_setup", True))
            sessions_on_target = analysis.get("sessions_on_target", 0)
            average_miss = analysis.get("average_miss", 0.0)
            avg_rate = analysis.get("avg_rate")
            success_rate_pct = analysis.get("success_rate", 0.0)
            consecutive_misses = analysis.get("consecutive_misses", 0)
            miss_trend = analysis.get("miss_trend", "stable")
            display_sessions = matched_sessions
            schedules_analysis_text = build_weekly_accuracy_summary(analysis)

        state = self.hass.states.get(room.heating_rate_helper)
        if state and state.state not in ("unavailable", "unknown"):
            current_rate = float(state.state)
        else:
            if state:
                _LOGGER.debug(
                    "[%s] Heating rate entity unavailable "
                    "— using default %s°C/min",
                    room.room_name,
                    DEFAULT_HEATING_RATE,
                )
            current_rate = DEFAULT_HEATING_RATE

        if sessions_total == 0:
            trvs_early = room_trvs
            active_since_early = (
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M")
                if all_trvs_active_since else "n/a"
            )
            days_reliable: int | None = None
            if all_trvs_active_since:
                days_reliable = (datetime.now(timezone.utc) - all_trvs_active_since).days

            _LOGGER.warning(
                "[%s] No sessions detected — %s days of data available.",
                room.room_name,
                days_reliable if days_reliable is not None else "unknown",
            )

            if days_reliable is not None and days_reliable < 7:
                phase_instruction = (
                    f"SHA is still in the learning phase — only {days_reliable} day(s) of "
                    f"reliable TRV data available. Explain to the homeowner that the system "
                    f"will improve automatically as more data is collected."
                )
            else:
                phase_instruction = (
                    f"Reliable TRV data has been available for "
                    f"{days_reliable if days_reliable is not None else 'an unknown number of'} "
                    f"days, but no heating sessions were detected. Flag to the homeowner that "
                    f"TRVs may not be reporting hvac_action_str to InfluxDB correctly."
                )

            no_session_prompt = (
                f"No heating sessions detected yet for {room.room_name}.\n"
                f"TRVs configured: {', '.join(trvs_early) if trvs_early else 'none'}\n"
                f"Reliable data since: {active_since_early}\n\n"
                f"{phase_instruction}\n\n"
                f"Respond ONLY with a valid JSON object:\n"
                f"{{\n"
                f'  "performance": "unknown",\n'
                f'  "root_cause": "none",\n'
                f'  "confidence": "low",\n'
                f'  "report": "2-3 sentence plain-language weekly report for the homeowner"\n'
                f"}}"
            )

            response = await self.ollama.async_generate(no_session_prompt)
            no_session_result = await self.ollama.async_parse_json_response(response)
            report_text = (no_session_result or {}).get("report") or phase_instruction

            return {
                "session_count": 0,
                "on_target": 0,
                "consistent_miss": False,
                "root_cause": None,
                "report": report_text,
                "performance": "unknown",
            }

        # ── 10. Per-session stats ─────────────────────────────────────
        all_sessions_sorted = sorted(matched_sessions, key=lambda s: s["date"])
        target_comfort_temp = primary["target_temp"]

        # Gradient: TRV setpoint − comfort sensor at ready time
        gradients: list[float] = []
        comfort_at_ready_list: list[float] = []
        for s in matched_sessions:
            if s.get("observed_rate") is None:
                _LOGGER.debug(
                    "[%s] Session %s excluded from rate/gradient calculation"
                    " — no valid sensor readings",
                    room.room_name, s.get("date"),
                )
                continue
            trv_t = s.get("trv_target")
            room_at_on = s.get("room_temp_at_schedule_start") or s.get("temp_at_schedule_start")
            if room_at_on is not None:
                comfort_at_ready_list.append(room_at_on)
                if trv_t is not None and trv_t > room_at_on:
                    gradients.append(trv_t - room_at_on)
        avg_gradient: float | None = (
            round(sum(gradients) / len(gradients), 1) if gradients else None
        )
        avg_comfort_at_ready: float | None = (
            round(sum(comfort_at_ready_list) / len(comfort_at_ready_list), 1)
            if comfort_at_ready_list else None
        )

        # ── 11. Consistent miss and root cause ───────────────────────
        sessions_missed = sessions_total - sessions_on_target
        consistent_miss = sessions_total > 0 and sessions_missed > sessions_total * 0.6

        root_cause: str | None = None
        if consistent_miss:
            avg_rate_val = avg_rate or 0.0
            if average_miss > 2.0 and avg_rate_val < 0.01:
                root_cause = "HARDWARE_INSUFFICIENT"
            elif average_miss > 0.5 and avg_rate_val >= 0.01:
                root_cause = "PREHEAT_TOO_SHORT"
            elif avg_gradient is not None and avg_gradient > 3.0:
                root_cause = "TRV_SETPOINT_TOO_LOW"
            elif avg_rate_val < 0.005 and weather["outside_temp"] < 5.0:
                root_cause = "HEAT_LOSS_HIGH"
            else:
                # Recent degradation: last 7 full-setup sessions vs overall
                full_only = [s for s in all_sessions_sorted if s.get("full_setup", True)]
                if full_setup_count >= 7 and len(full_only) >= 7:
                    last7 = full_only[-7:]
                    last7_ot = sum(1 for s in last7 if s.get("target_reached", False))
                    overall_rate_frac = sessions_on_target / full_setup_count
                    if (last7_ot / 7) < overall_rate_frac - 0.2:
                        root_cause = "RECENT_DEGRADATION"

        _LOGGER.debug(
            "[%s] Weekly: consistent_miss=%s root_cause=%s avg_gradient=%s",
            room.room_name, consistent_miss, root_cause, avg_gradient,
        )

        # ── 12. Session detail table (last 5 sessions) ───────────────
        last5 = all_sessions_sorted[-5:]
        table_lines = ["Date        Comfort   Target  Miss"]
        for s in last5:
            d = str(s.get("date", "?"))[:10]
            comfort = s.get("room_temp_at_schedule_start") or s.get("temp_at_schedule_start")
            tgt = s.get("target_temp", target_comfort_temp)
            miss = s.get("target_miss")
            comfort_str = f"{comfort:.1f}°C" if comfort is not None else "n/a   "
            miss_str = f"{miss:+.1f}°C" if miss is not None else "n/a   "
            table_lines.append(f"{d}  {comfort_str:7}  {tgt:.1f}°C  {miss_str}")
        session_detail_table = "\n".join(table_lines)

        # ── 13. Build prompt ──────────────────────────────────────────
        trvs = room_trvs
        humidity_sensor_entity = self._get_room_humidity_sensor(room)
        humidity_analysis_text = build_humidity_analysis_text(humidity_readings, matched_sessions)

        prompt = await self.hass.async_add_executor_job(
            load_prompt,
            "weekly_analysis.md",
            {
                "room_name": room.room_name,
                "target_comfort_temp": target_comfort_temp,
                "target_ready_time": primary["schedule_time"],
                "sessions_total": sessions_total,
                "sessions_on_target": sessions_on_target,
                "avg_comfort_at_ready": (
                    str(avg_comfort_at_ready) if avg_comfort_at_ready is not None else "n/a"
                ),
                "avg_miss": str(average_miss),
                "consistent_miss": "Yes" if consistent_miss else "No",
                "root_cause": root_cause or "none",
                "outside_temp_now": weather["outside_temp"],
                "season": season,
                "session_detail_table": session_detail_table,
                # Extra context supplied to Ollama
                "schedule_count": len(schedules),
                "schedule_lines": build_schedule_lines(schedules),
                "avg_heating_rate": str(avg_rate or "n/a"),
                "avg_gradient": str(avg_gradient) if avg_gradient is not None else "n/a",
                "success_rate_pct": round(success_rate_pct, 1),
                "schedules_analysis_text": schedules_analysis_text,
                "humidity_analysis_text": humidity_analysis_text,
                "humidity_sensor": humidity_sensor_entity or "not configured",
            },
            self.hass.config.config_dir,
        )

        # ── 14. Ollama ────────────────────────────────────────────────
        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result:
            _LOGGER.error(
                "[%s] Invalid weekly Ollama response (first 200 chars): %s",
                room.room_name, (response or "")[:200],
            )
            return {
                "session_count": sessions_total,
                "on_target": sessions_on_target,
                "consistent_miss": consistent_miss,
                "root_cause": root_cause,
                "report": "Analysis failed — no AI response.",
                "performance": "unknown",
            }

        # ── 15. Parse result ──────────────────────────────────────────
        performance = result.get("performance", "unknown")
        # Ollama may refine root_cause — accept it when not "none"
        ollama_root_cause = result.get("root_cause", "")
        if ollama_root_cause and ollama_root_cause not in ("none", ""):
            root_cause = ollama_root_cause
        confidence = result.get("confidence", "unknown")
        report_text = result.get("report", "No weekly report generated.")

        # ── 16. Persist in room_states ────────────────────────────────
        if room.room_id not in self.room_states:
            self.room_states[room.room_id] = {}
        self.room_states[room.room_id]["confidence"] = confidence
        self.room_states[room.room_id]["weekly_report"] = report_text
        self.room_states[room.room_id]["consistent_miss"] = consistent_miss

        # ── 17. Radiator capacity check ───────────────────────────────
        await self._async_check_radiator_capacity(room, per_schedule, current_rate, avg_rate)

        # ── 18. Log when consistently missing target ──────────────────
        if consistent_miss:
            rc_plain = _ROOT_CAUSE_PLAIN.get(root_cause or "", root_cause or "unknown")
            _LOGGER.warning(
                "[%s] Weekly: room needs attention — %d/%d sessions on target (%d%%), "
                "avg miss %+.1f°C, root cause: %s.",
                room.room_name, sessions_on_target, sessions_total,
                int(success_rate_pct), average_miss, rc_plain,
            )

        _LOGGER.info(
            "[%s] Weekly report complete — sessions: %d, on_target: %d, "
            "consistent_miss: %s, root_cause: %s, confidence: %s",
            room.room_name, sessions_total, sessions_on_target,
            consistent_miss, root_cause, confidence,
        )

        return {
            "session_count": sessions_total,
            "on_target": sessions_on_target,
            "consistent_miss": consistent_miss,
            "root_cause": root_cause,
            "report": report_text,
            "performance": performance,
        }
