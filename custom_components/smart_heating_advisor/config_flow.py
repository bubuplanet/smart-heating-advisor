"""Config flow for Smart Heating Advisor."""
import logging
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.data_entry_flow import section
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    DurationSelector,
    DurationSelectorConfig,
)

from .const import (
    DOMAIN,
    CONF_OLLAMA_URL,
    CONF_OLLAMA_MODEL,
    CONF_INFLUXDB_URL,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_BUCKET,
    CONF_WEATHER_ENTITY,
    CONF_OUTSIDE_TEMP_SENSOR,
    CONF_DEBUG_LOGGING,
    CONF_ROOM_CONFIGS,
    CONF_VACATION_ENABLED,
    CONF_VACATION_MODE,
    CONF_VACATION_START_DATE,
    CONF_VACATION_END_DATE,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_INFLUXDB_URL,
    DEFAULT_INFLUXDB_ORG,
    DEFAULT_INFLUXDB_BUCKET,
    DEFAULT_VACATION_MODE,
    DEFAULT_COMFORT_TEMP,
    MIN_COMFORT_TEMP,
    MAX_COMFORT_TEMP,
)
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Module-level static schemas
# ──────────────────────────────────────────────────────────────────────

STEP_1_SCHEMA_BASE = vol.Schema(
    {
        vol.Required("thermostat_sensor"): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Required("trvs"): EntitySelector(
            EntitySelectorConfig(domain="climate", multiple=True)
        ),
        vol.Optional("fixed_trvs_section"): section(
            vol.Schema(
                {
                    vol.Optional("fixed_trvs", default=[]): EntitySelector(
                        EntitySelectorConfig(domain="climate", multiple=True)
                    ),
                    vol.Optional("fixed_trv_temp", default=20.0): NumberSelector(
                        NumberSelectorConfig(
                            min=4.0, max=35.0, step=0.5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="°C",
                        )
                    ),
                }
            ),
            {"collapsed": True},
        ),
        vol.Optional("override_section"): section(
            vol.Schema(
                {
                    vol.Optional("override_enabled", default=False): BooleanSelector(),
                    vol.Optional("override_duration_minutes", default=60): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=480, step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            {"collapsed": True},
        ),
        vol.Optional("humidity_section"): section(
            vol.Schema(
                {
                    vol.Optional("humidity_enabled", default=False): BooleanSelector(),
                    vol.Optional("humidity_sensor"): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="humidity")
                    ),
                }
            ),
            {"collapsed": True},
        ),
    }
)

STEP_2_SCHEMA = vol.Schema(
    {
        vol.Optional("window_sensors", default=[]): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class=["window", "door", "opening"],
                multiple=True,
            )
        ),
        vol.Required("airing_mode_enabled", default=False): BooleanSelector(),
        vol.Optional(
            "airing_duration",
            default={"hours": 0, "minutes": 2, "seconds": 0},
        ): DurationSelector(DurationSelectorConfig(enable_day=False)),
    }
)


# ──────────────────────────────────────────────────────────────────────
# Connection test helpers
# ──────────────────────────────────────────────────────────────────────

async def _test_ollama(url: str, model: str) -> bool:
    """Test Ollama connection and verify model exists."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url.rstrip('/')}/api/tags",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    return False
                data = await response.json()
                models = [m["name"] for m in data.get("models", [])]
                return any(model in m for m in models)
    except Exception:
        return False


async def _test_influxdb(url: str, token: str, org: str, bucket: str) -> bool:
    """Test InfluxDB connection."""
    try:
        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }
        flux = f'from(bucket: "{bucket}") |> range(start: -1h) |> limit(n: 1)'
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{url.rstrip('/')}/api/v2/query",
                params={"org": org},
                headers=headers,
                data=flux,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                return response.status == 200
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────
# Subentry flow — Add / Edit Room (4-step wizard)
# ──────────────────────────────────────────────────────────────────────

class SHARoomSubentryFlowHandler(ConfigSubentryFlow):
    """4-step room wizard for adding or reconfiguring a room.

    Step 1 (user):        Room name + sensors + TRVs + override + humidity
    Step 2 (temperature): Schedules (multi-pass loop) + comfort temperature
    Step 3 (windows):     Window sensors + airing mode
    Step 4 (confirm):     Review and create/update
    """

    def __init__(self) -> None:
        self._data: dict = {}

    @property
    def _is_edit(self) -> bool:
        return self.source == config_entries.SOURCE_RECONFIGURE

    def _existing_room_ids(self) -> set[str]:
        """Return room_ids already configured, excluding the subentry being edited."""
        entry = self._get_entry()
        exclude = self._get_reconfigure_subentry().subentry_id if self._is_edit else None
        return {
            _room_name_to_id(s.data.get("room_name", ""))
            for s in entry.subentries.values()
            if s.subentry_type == "room"
            and s.data.get("room_name")
            and s.subentry_id != exclude
        }

    @staticmethod
    def _seconds_from_duration(val) -> tuple[int, str]:
        """Parse DurationSelector dict or legacy HH:MM:SS string to (total_seconds, normalised_str)."""
        if isinstance(val, dict):
            h = int(val.get("hours", 0))
            m = int(val.get("minutes", 0))
            s = int(val.get("seconds", 0))
        else:
            import re
            match = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", str(val).strip())
            if not match:
                raise ValueError(f"invalid: {val!r}")
            h = int(match.group(1))
            m = int(match.group(2))
            s = int(match.group(3))
        total = h * 3600 + m * 60 + s
        if total < 10:
            raise ValueError("too short")
        return total, f"{h:02d}:{m:02d}:{s:02d}"

    # ── Edit mode entry point ────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Pre-fill from existing subentry and route to Step 1."""
        subentry = self._get_reconfigure_subentry()
        if subentry:
            self._data = dict(subentry.data)
        return await self.async_step_user()

    # ── Step 1: Room identity + sensor + TRVs + override + humidity ──

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 1/4 — Room name, sensors, TRVs, override settings, humidity."""
        existing_ids = self._existing_room_ids()
        errors: dict[str, str] = {}
        d = self._data

        if user_input is not None:
            room_name = (
                d.get("room_name", "")
                if self._is_edit
                else user_input.get("room_name", "").strip()
            )
            thermostat_sensor = user_input.get("thermostat_sensor", "") or ""
            trvs_raw = user_input.get("trvs", [])
            trvs = list(trvs_raw) if isinstance(trvs_raw, list) else ([trvs_raw] if trvs_raw else [])

            if not self._is_edit:
                if not room_name:
                    errors["room_name"] = "required"
                elif _room_name_to_id(room_name) in existing_ids:
                    errors["room_name"] = "room_already_exists"

            if not thermostat_sensor:
                errors["thermostat_sensor"] = "required"
            if not trvs:
                errors["trvs"] = "required"

            if not errors:
                fixed_sec = user_input.get("fixed_trvs_section") or {}
                override_sec = user_input.get("override_section") or {}
                humidity_sec = user_input.get("humidity_section") or {}
                fixed_raw = fixed_sec.get("fixed_trvs", [])
                d.update({
                    "room_name": room_name,
                    "thermostat_sensor": thermostat_sensor,
                    "trvs": trvs,
                    "fixed_trvs": (
                        list(fixed_raw) if isinstance(fixed_raw, list)
                        else ([fixed_raw] if fixed_raw else [])
                    ),
                    "fixed_trv_temp": float(fixed_sec.get("fixed_trv_temp", 20.0)),
                    "override_enabled": bool(override_sec.get("override_enabled", False)),
                    "override_duration_minutes": int(
                        override_sec.get("override_duration_minutes", 60)
                    ),
                    "humidity_enabled": bool(humidity_sec.get("humidity_enabled", False)),
                    "humidity_sensor": humidity_sec.get("humidity_sensor", "") or "",
                })
                return await self.async_step_temperature_select()

        # Build nested suggested values so sections pre-fill correctly
        suggested = {
            "thermostat_sensor": d.get("thermostat_sensor"),
            "trvs": d.get("trvs", []),
            "fixed_trvs_section": {
                "fixed_trvs": d.get("fixed_trvs", []),
                "fixed_trv_temp": d.get("fixed_trv_temp", 20.0),
            },
            "override_section": {
                "override_enabled": d.get("override_enabled", False),
                "override_duration_minutes": d.get("override_duration_minutes", 60),
            },
            "humidity_section": {
                "humidity_enabled": d.get("humidity_enabled", False),
                "humidity_sensor": d.get("humidity_sensor"),
            },
        }

        if not self._is_edit:
            areas = ar.async_get(self.hass).async_list_areas()
            area_options = [
                {"value": a.name, "label": a.name}
                for a in sorted(areas, key=lambda a: a.name)
            ]
            data_schema = self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("room_name"): SelectSelector(
                            SelectSelectorConfig(
                                options=area_options,
                                custom_value=True,
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        **STEP_1_SCHEMA_BASE.schema,
                    }
                ),
                suggested,
            )
        else:
            data_schema = self.add_suggested_values_to_schema(STEP_1_SCHEMA_BASE, suggested)

        placeholders = {}
        if self._is_edit:
            placeholders["room_name"] = d.get("room_name", "")

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=placeholders or None,
            last_step=False,
        )

    # ── Step 2a: Temperature profile — select schedules ─────────────────

    async def async_step_temperature_select(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 2a — Select schedules and comfort temperature."""
        errors: dict[str, str] = {}
        d = self._data

        if "schedules" not in d:
            d["schedules"] = []

        if user_input is not None:
            schedule_entities = list(user_input.get("schedules") or [])
            comfort_temp_enabled = bool(user_input.get("comfort_temp_enabled", False))
            comfort_temp = float(user_input.get("comfort_temp", DEFAULT_COMFORT_TEMP))

            d["comfort_temp_enabled"] = comfort_temp_enabled
            d["comfort_temp"] = comfort_temp

            if not schedule_entities and not comfort_temp_enabled:
                errors["schedules"] = "temperature_profile_required"
            elif schedule_entities:
                d["_schedule_entities"] = schedule_entities
                return await self.async_step_temperature_temps()
            else:
                # Comfort only — no schedules
                d["schedules"] = []
                d.pop("_schedule_entities", None)
                return await self.async_step_windows()

        # Pre-fill entity_ids — handle both dict and legacy string format
        prefill_schedules = [
            s["entity_id"] if isinstance(s, dict) else s
            for s in d.get("schedules", [])
        ]

        schema = vol.Schema({
            vol.Optional("schedules", default=prefill_schedules): EntitySelector(
                EntitySelectorConfig(domain="schedule", multiple=True)
            ),
            vol.Optional("comfort_temp_enabled", default=d.get("comfort_temp_enabled", False)): BooleanSelector(),
            vol.Optional("comfort_temp", default=d.get("comfort_temp", DEFAULT_COMFORT_TEMP)): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_COMFORT_TEMP, max=MAX_COMFORT_TEMP, step=0.5,
                    mode=NumberSelectorMode.BOX, unit_of_measurement="°C",
                )
            ),
        })

        return self.async_show_form(
            step_id="temperature_select",
            data_schema=schema,
            errors=errors,
            description_placeholders={"room_name": d.get("room_name", "")},
            last_step=False,
        )

    # ── Step 2b: Temperature profile — set temp per schedule ─────────────

    async def async_step_temperature_temps(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 2b — Set target temperature for each selected schedule."""
        d = self._data
        schedule_entities = d.get("_schedule_entities", [])

        # Build schema: one NumberSelector per schedule, keyed by sanitised entity slug
        schema_dict: dict = {}
        for entity_id in schedule_entities:
            field_key = entity_id.replace("schedule.", "").replace(".", "_").replace("-", "_")
            existing_temp = next(
                (
                    s["target_temp"] if isinstance(s, dict) else 21.0
                    for s in d.get("schedules", [])
                    if (s["entity_id"] if isinstance(s, dict) else s) == entity_id
                ),
                21.0,
            )
            schema_dict[vol.Optional(field_key, default=existing_temp)] = NumberSelector(
                NumberSelectorConfig(
                    min=4.0, max=35.0, step=0.5,
                    mode=NumberSelectorMode.BOX, unit_of_measurement="°C",
                )
            )

        if user_input is not None:
            schedules = []
            for entity_id in schedule_entities:
                field_key = entity_id.replace("schedule.", "").replace(".", "_").replace("-", "_")
                schedules.append({
                    "entity_id": entity_id,
                    "target_temp": float(user_input.get(field_key, 21.0)),
                })
            d["schedules"] = schedules
            d.pop("_schedule_entities", None)
            return await self.async_step_windows()

        lines = []
        for entity_id in schedule_entities:
            state = self.hass.states.get(entity_id)
            if state:
                friendly = state.attributes.get(
                    "friendly_name",
                    entity_id.replace("schedule.", "").replace("_", " ").title(),
                )
            else:
                friendly = entity_id.replace("schedule.", "").replace("_", " ").title()
            lines.append(f"• {friendly}")

        return self.async_show_form(
            step_id="temperature_temps",
            data_schema=vol.Schema(schema_dict),
            errors={},
            description_placeholders={
                "room_name": d.get("room_name", ""),
                "schedule_names": "\n".join(lines),
            },
            last_step=False,
        )

    # ── Step 3: Windows + airing ──────────────────────────────────────

    async def async_step_windows(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 3/4 — Window sensors and airing mode duration."""
        errors: dict[str, str] = {}
        d = self._data

        if user_input is not None:
            sensors = user_input.get("window_sensors", [])
            airing_enabled = bool(user_input.get("airing_mode_enabled", False))
            raw_duration = user_input.get("airing_duration") or {"hours": 0, "minutes": 2, "seconds": 0}
            try:
                total_secs, norm_str = self._seconds_from_duration(raw_duration)
            except ValueError:
                if airing_enabled:
                    errors["airing_duration"] = "invalid_airing_duration"
                total_secs, norm_str = 120, "00:02:00"

            if not errors:
                d.update({
                    "window_sensors": (
                        list(sensors) if isinstance(sensors, list)
                        else ([sensors] if sensors else [])
                    ),
                    "airing_mode_enabled": airing_enabled,
                    "airing_duration": norm_str,
                    "airing_duration_seconds": total_secs,
                })
                return await self.async_step_confirm()

        # Convert stored HH:MM:SS string to dict for DurationSelector pre-fill
        suggested = dict(d)
        stored_ad = suggested.get("airing_duration")
        if isinstance(stored_ad, str):
            parts = stored_ad.split(":")
            suggested["airing_duration"] = {
                "hours": int(parts[0]),
                "minutes": int(parts[1]),
                "seconds": int(parts[2]),
            }

        return self.async_show_form(
            step_id="windows",
            data_schema=self.add_suggested_values_to_schema(STEP_2_SCHEMA, suggested),
            description_placeholders={"room_name": d.get("room_name", "")},
            errors=errors,
            last_step=False,
        )

    # ── Step 4: Confirm and create / update ──────────────────────────

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 4/4 — Review configuration and create or update room subentry."""
        d = self._data
        room_name = d.get("room_name", "")

        if user_input is not None:
            new_data = {
                "room_name": room_name,
                "thermostat_sensor": d.get("thermostat_sensor", ""),
                "trvs": d.get("trvs", []),
                "fixed_trvs": d.get("fixed_trvs", []),
                "fixed_trv_temp": d.get("fixed_trv_temp", 20.0),
                "override_enabled": d.get("override_enabled", False),
                "override_duration_minutes": d.get("override_duration_minutes", 60),
                "window_sensors": d.get("window_sensors", []),
                "airing_mode_enabled": d.get("airing_mode_enabled", False),
                "airing_duration": d.get("airing_duration", "00:02:00"),
                "airing_duration_seconds": d.get("airing_duration_seconds", 120),
                "schedules": d.get("schedules", []),
                "comfort_temp_enabled": d.get("comfort_temp_enabled", False),
                "comfort_temp": d.get("comfort_temp", DEFAULT_COMFORT_TEMP),
                "humidity_enabled": d.get("humidity_enabled", False),
                "humidity_sensor": d.get("humidity_sensor", ""),
            }
            if self._is_edit:
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=new_data,
                )
            return self.async_create_entry(title=room_name, data=new_data)

        trvs = d.get("trvs", [])
        fixed_trvs = d.get("fixed_trvs", [])
        window_sensors = d.get("window_sensors", [])
        schedules = d.get("schedules", [])
        trvs_s = f"{len(trvs)} radiator(s)" if trvs else "none"
        fixed_s = f"{len(fixed_trvs)} fixed" if fixed_trvs else "none"
        windows_s = f"{len(window_sensors)} sensor(s)" if window_sensors else "none"
        airing_s = (
            f"enabled ({d.get('airing_duration', '00:02:00')})"
            if d.get("airing_mode_enabled") else "disabled"
        )
        comfort_temp_s = (
            f"{d.get('comfort_temp', DEFAULT_COMFORT_TEMP)}°C"
            if d.get("comfort_temp_enabled") else "disabled"
        )
        schedules_s = f"{len(schedules)} schedule(s)" if schedules else "none"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "room_name": room_name,
                "thermostat_sensor": d.get("thermostat_sensor", "") or "none",
                "trvs": trvs_s,
                "fixed_trvs": fixed_s,
                "override_enabled": "enabled" if d.get("override_enabled") else "disabled",
                "window_sensors": windows_s,
                "airing_enabled": airing_s,
                "comfort_temp": comfort_temp_s,
                "schedules_count": schedules_s,
                "humidity_enabled": "enabled" if d.get("humidity_enabled") else "disabled",
            },
            last_step=True,
        )


# ──────────────────────────────────────────────────────────────────────
# Subentry flow — Vacation Settings (single-step, singleton)
# ──────────────────────────────────────────────────────────────────────

_VACATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_VACATION_ENABLED, default=False): BooleanSelector(),
        vol.Required(CONF_VACATION_MODE, default=DEFAULT_VACATION_MODE): SelectSelector(
            SelectSelectorConfig(
                options=["frost", "eco", "off"],
                mode=SelectSelectorMode.LIST,
                translation_key="vacation_mode",
            )
        ),
        vol.Optional(CONF_VACATION_START_DATE): DateSelector(),
        vol.Optional(CONF_VACATION_END_DATE): DateSelector(),
    }
)


class SHAVacationSubentryFlowHandler(ConfigSubentryFlow):
    """Single-step vacation wizard.

    Only one vacation subentry is allowed per config entry.
    Delete the existing subentry and recreate to change settings.
    """

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 1/1 — Vacation configuration."""
        entry = self._get_entry()

        # Guard: only one vacation subentry allowed
        for s in entry.subentries.values():
            if s.subentry_type == "vacation":
                return self.async_abort(reason="already_configured")

        # Seed defaults from legacy options so migration is seamless
        suggested = {
            CONF_VACATION_ENABLED: entry.options.get(CONF_VACATION_ENABLED, False),
            CONF_VACATION_MODE: entry.options.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
        }

        errors: dict[str, str] = {}

        if user_input is not None:
            start_date = user_input.get(CONF_VACATION_START_DATE) or None
            end_date = user_input.get(CONF_VACATION_END_DATE) or None

            if bool(start_date) != bool(end_date):
                errors[CONF_VACATION_END_DATE] = "end_date_required"
            elif start_date and end_date and end_date <= start_date:
                errors[CONF_VACATION_END_DATE] = "end_before_start"

            if not errors:
                return self.async_create_entry(
                    title="Vacation",
                    data={
                        CONF_VACATION_ENABLED: bool(user_input.get(CONF_VACATION_ENABLED, False)),
                        CONF_VACATION_MODE: user_input.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
                        CONF_VACATION_START_DATE: start_date,
                        CONF_VACATION_END_DATE: end_date,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_VACATION_SCHEMA, suggested),
            errors=errors,
        )


# ──────────────────────────────────────────────────────────────────────
# Main config flow
# ──────────────────────────────────────────────────────────────────────

class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "SHAOptionsFlow":
        return SHAOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            "room": SHARoomSubentryFlowHandler,
            "vacation": SHAVacationSubentryFlowHandler,
        }

    def __init__(self) -> None:
        self._ollama_data: dict = {}
        self._influxdb_data: dict = {}

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Step 1 — Ollama configuration."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors = {}

        if user_input is not None:
            if await _test_ollama(user_input[CONF_OLLAMA_URL], user_input[CONF_OLLAMA_MODEL]):
                self._ollama_data = user_input
                return await self.async_step_influxdb()
            errors["base"] = "ollama_connection_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OLLAMA_URL, default=DEFAULT_OLLAMA_URL): str,
                    vol.Required(CONF_OLLAMA_MODEL, default=DEFAULT_OLLAMA_MODEL): str,
                    vol.Optional(CONF_DEBUG_LOGGING, default=False): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_influxdb(self, user_input=None) -> ConfigFlowResult:
        """Step 2 — InfluxDB configuration."""
        errors = {}

        if user_input is not None:
            if await _test_influxdb(
                user_input[CONF_INFLUXDB_URL],
                user_input[CONF_INFLUXDB_TOKEN],
                user_input[CONF_INFLUXDB_ORG],
                user_input[CONF_INFLUXDB_BUCKET],
            ):
                self._influxdb_data = user_input
                return await self.async_step_weather()
            errors["base"] = "influxdb_connection_failed"

        return self.async_show_form(
            step_id="influxdb",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INFLUXDB_URL, default=DEFAULT_INFLUXDB_URL): str,
                    vol.Required(CONF_INFLUXDB_TOKEN): str,
                    vol.Required(CONF_INFLUXDB_ORG, default=DEFAULT_INFLUXDB_ORG): str,
                    vol.Required(CONF_INFLUXDB_BUCKET, default=DEFAULT_INFLUXDB_BUCKET): str,
                }
            ),
            errors=errors,
        )

    async def async_step_weather(self, user_input=None) -> ConfigFlowResult:
        """Step 3 — Outside temperature sensor and weather entity."""
        if user_input is not None:
            return self.async_create_entry(
                title="Smart Heating Advisor",
                data={
                    CONF_OLLAMA_URL: self._ollama_data[CONF_OLLAMA_URL],
                    CONF_OLLAMA_MODEL: self._ollama_data[CONF_OLLAMA_MODEL],
                    **self._influxdb_data,
                    CONF_WEATHER_ENTITY: user_input[CONF_WEATHER_ENTITY],
                    CONF_ROOM_CONFIGS: [],
                },
                options={
                    CONF_DEBUG_LOGGING: bool(self._ollama_data.get(CONF_DEBUG_LOGGING, False)),
                    CONF_OUTSIDE_TEMP_SENSOR: user_input.get(CONF_OUTSIDE_TEMP_SENSOR) or None,
                },
            )

        # Auto-detect weather.forecast_home
        default_weather = (
            "weather.forecast_home"
            if self.hass.states.get("weather.forecast_home")
            else ""
        )

        weather_key = (
            vol.Required(CONF_WEATHER_ENTITY, default=default_weather)
            if default_weather
            else vol.Required(CONF_WEATHER_ENTITY)
        )

        return self.async_show_form(
            step_id="weather",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_OUTSIDE_TEMP_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    weather_key: EntitySelector(
                        EntitySelectorConfig(domain="weather")
                    ),
                }
            ),
        )


# ──────────────────────────────────────────────────────────────────────
# Options flow
# ──────────────────────────────────────────────────────────────────────

class SHAOptionsFlow(config_entries.OptionsFlow):
    """Handle SHA options — connection settings, weather entity, and debug toggle."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Connection settings and global options."""
        cd = self._config_entry.data
        current_debug = self._config_entry.options.get(CONF_DEBUG_LOGGING, False)

        if user_input is not None:
            debug = user_input.get(CONF_DEBUG_LOGGING, False)
            outside_sensor = user_input.get(CONF_OUTSIDE_TEMP_SENSOR) or None

            data_keys = [
                CONF_OLLAMA_URL, CONF_OLLAMA_MODEL,
                CONF_INFLUXDB_URL, CONF_INFLUXDB_TOKEN,
                CONF_INFLUXDB_ORG, CONF_INFLUXDB_BUCKET,
                CONF_WEATHER_ENTITY,
            ]
            data_changed = any(
                user_input.get(k) != cd.get(k)
                for k in data_keys
                if k in user_input
            )

            if data_changed:
                new_data = {**cd, **{k: user_input[k] for k in data_keys if k in user_input}}
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                _LOGGER.info("SHA options: connection settings changed — scheduling reload")
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._config_entry.entry_id)
                )

            return self.async_create_entry(
                title="",
                data={
                    CONF_DEBUG_LOGGING: debug,
                    CONF_OUTSIDE_TEMP_SENSOR: outside_sensor,
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OLLAMA_URL,
                        default=cd.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL),
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=cd.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL),
                    ): str,
                    vol.Required(
                        CONF_INFLUXDB_URL,
                        default=cd.get(CONF_INFLUXDB_URL, DEFAULT_INFLUXDB_URL),
                    ): str,
                    vol.Required(
                        CONF_INFLUXDB_TOKEN,
                        default=cd.get(CONF_INFLUXDB_TOKEN, ""),
                    ): str,
                    vol.Required(
                        CONF_INFLUXDB_ORG,
                        default=cd.get(CONF_INFLUXDB_ORG, DEFAULT_INFLUXDB_ORG),
                    ): str,
                    vol.Required(
                        CONF_INFLUXDB_BUCKET,
                        default=cd.get(CONF_INFLUXDB_BUCKET, DEFAULT_INFLUXDB_BUCKET),
                    ): str,
                    vol.Required(
                        CONF_WEATHER_ENTITY,
                        default=cd.get(CONF_WEATHER_ENTITY, "weather.forecast_home"),
                    ): EntitySelector(
                        EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(
                        CONF_OUTSIDE_TEMP_SENSOR,
                        **({} if not self._config_entry.options.get(CONF_OUTSIDE_TEMP_SENSOR) else {"default": self._config_entry.options[CONF_OUTSIDE_TEMP_SENSOR]}),
                    ): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Required(CONF_DEBUG_LOGGING, default=current_debug): bool,
                }
            ),
        )
