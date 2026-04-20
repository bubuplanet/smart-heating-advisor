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
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_DEBUG_LOGGING,
    CONF_ROOM_CONFIGS,
    CONF_VACATION_ENABLED,
    CONF_VACATION_MODE,
    CONF_VACATION_CALENDAR,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_INFLUXDB_URL,
    DEFAULT_INFLUXDB_ORG,
    DEFAULT_INFLUXDB_BUCKET,
    DEFAULT_VACATION_MODE,
    DEFAULT_DEFAULT_TEMP,
    MIN_DEFAULT_TEMP,
    MAX_DEFAULT_TEMP,
)
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)


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
# Subentry flow — Add Room (5-step wizard)
# ──────────────────────────────────────────────────────────────────────

class SHARoomSubentryFlowHandler(ConfigSubentryFlow):
    """5-step room wizard for adding a room to Smart Heating Advisor.

    Step 1 (user):        Room identity + sensor + TRVs + override
    Step 2 (windows):     Window sensors + airing mode (HH:MM:SS duration)
    Step 3 (temperature): Temperature profile + schedule helpers
    Step 4 (humidity):    Humidity monitoring
    Step 5 (confirm):     Review and create
    """

    def __init__(self) -> None:
        # Step 1 data
        self._room_name: str = ""
        self._thermostat_sensor: str = ""
        self._trvs: list[str] = []
        self._fixed_trvs: list[str] = []
        self._fixed_trv_temp: float = 20.0
        self._override_enabled: bool = False
        self._override_duration_minutes: int = 60
        # Step 2 data
        self._window_sensors: list[str] = []
        self._airing_mode_enabled: bool = False
        self._airing_duration: str = "00:02:00"
        self._airing_duration_seconds: int = 120
        # Step 3 data
        self._default_temp_enabled: bool = False
        self._default_temp: float = DEFAULT_DEFAULT_TEMP
        self._schedules: list[str] = []
        # Step 4 data
        self._humidity_enabled: bool = False
        self._humidity_sensor: str = ""

    def _existing_room_ids(self) -> set[str]:
        """Return room_ids of rooms already configured as room subentries."""
        entry = self._get_entry()
        return {
            _room_name_to_id(s.data.get("room_name", ""))
            for s in entry.subentries.values()
            if s.subentry_type == "room" and s.data.get("room_name")
        }

    @staticmethod
    def _parse_hms(value: str) -> tuple[int, str]:
        """Parse HH:MM:SS string to (total_seconds, normalised_str).

        Raises ValueError if format is invalid or duration is below 10 seconds.
        """
        import re as _re
        m = _re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", value.strip())
        if not m:
            raise ValueError(f"invalid format: {value!r}")
        h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mn >= 60 or s >= 60:
            raise ValueError(f"invalid time components: {value!r}")
        total = h * 3600 + mn * 60 + s
        if total < 10:
            raise ValueError(f"duration below 10 s: {value!r}")
        return total, f"{h:02d}:{mn:02d}:{s:02d}"

    # ── Step 1: Room identity + sensor + TRVs + override ─────────────

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 1/5 — Room name, temperature sensor, TRVs, override."""
        existing_ids = self._existing_room_ids()
        errors: dict[str, str] = {}

        if user_input is not None:
            room_name = user_input.get("room_name", "").strip()
            thermostat_sensor = user_input.get("thermostat_sensor", "") or ""
            trvs_raw = user_input.get("trvs", [])
            trvs = list(trvs_raw) if isinstance(trvs_raw, list) else ([trvs_raw] if trvs_raw else [])

            if not room_name:
                errors["room_name"] = "required"
            elif _room_name_to_id(room_name) in existing_ids:
                errors["room_name"] = "room_already_exists"

            if not thermostat_sensor:
                errors["thermostat_sensor"] = "required"

            if not trvs:
                errors["trvs"] = "required"

            if not errors:
                self._room_name = room_name
                self._thermostat_sensor = thermostat_sensor
                self._trvs = trvs
                fixed_raw = user_input.get("fixed_trvs", [])
                self._fixed_trvs = (
                    list(fixed_raw) if isinstance(fixed_raw, list)
                    else ([fixed_raw] if fixed_raw else [])
                )
                self._fixed_trv_temp = float(user_input.get("fixed_trv_temp", 20.0))
                self._override_enabled = bool(user_input.get("override_enabled", False))
                self._override_duration_minutes = int(
                    user_input.get("override_duration_minutes", 60)
                )
                return await self.async_step_windows()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): str,
                    vol.Required("thermostat_sensor"): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Required("trvs"): EntitySelector(
                        EntitySelectorConfig(domain="climate", multiple=True)
                    ),
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
                    vol.Required("override_enabled", default=False): BooleanSelector(),
                    vol.Optional("override_duration_minutes", default=60): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=480, step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            errors=errors,
            last_step=False,
        )

    # ── Step 2: Windows + airing (HH:MM:SS) ──────────────────────────

    async def async_step_windows(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 2/5 — Window sensors and airing mode duration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sensors = user_input.get("window_sensors", [])
            self._window_sensors = (
                list(sensors) if isinstance(sensors, list)
                else ([sensors] if sensors else [])
            )
            self._airing_mode_enabled = bool(user_input.get("airing_mode_enabled", False))
            raw = (user_input.get("airing_duration") or "00:02:00").strip()
            try:
                self._airing_duration_seconds, self._airing_duration = self._parse_hms(raw)
            except ValueError:
                if self._airing_mode_enabled:
                    errors["airing_duration"] = "invalid_airing_duration"
                else:
                    self._airing_duration = "00:02:00"
                    self._airing_duration_seconds = 120

            if not errors:
                return await self.async_step_temperature()

        return self.async_show_form(
            step_id="windows",
            data_schema=vol.Schema(
                {
                    vol.Optional("window_sensors"): EntitySelector(
                        EntitySelectorConfig(
                            domain="binary_sensor",
                            device_class=["window", "door", "opening"],
                            multiple=True,
                        )
                    ),
                    vol.Required("airing_mode_enabled", default=False): BooleanSelector(),
                    vol.Optional("airing_duration", default="00:02:00"): str,
                }
            ),
            description_placeholders={"room_name": self._room_name},
            errors=errors,
            last_step=False,
        )

    # ── Step 3: Temperature profile + schedule helpers ────────────────

    async def async_step_temperature(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 3/5 — Standby temperature and comfort schedule helpers."""
        if user_input is not None:
            self._default_temp_enabled = bool(user_input.get("default_temp_enabled", False))
            self._default_temp = float(user_input.get("default_temp", DEFAULT_DEFAULT_TEMP))
            schedules_raw = user_input.get("schedules", [])
            self._schedules = (
                list(schedules_raw) if isinstance(schedules_raw, list)
                else ([schedules_raw] if schedules_raw else [])
            )
            return await self.async_step_humidity()

        return self.async_show_form(
            step_id="temperature",
            data_schema=vol.Schema(
                {
                    vol.Required("default_temp_enabled", default=False): BooleanSelector(),
                    vol.Optional("default_temp", default=DEFAULT_DEFAULT_TEMP): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_DEFAULT_TEMP,
                            max=MAX_DEFAULT_TEMP,
                            step=0.5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="°C",
                        )
                    ),
                    vol.Optional("schedules"): EntitySelector(
                        EntitySelectorConfig(domain="schedule", multiple=True)
                    ),
                }
            ),
            description_placeholders={
                "room_name": self._room_name,
                "schedule_helper_url": "/config/helpers/add?domain=schedule",
            },
            last_step=False,
        )

    # ── Step 4: Humidity monitoring ───────────────────────────────────

    async def async_step_humidity(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 4/5 — Humidity sensor (SHA calculates threshold automatically)."""
        if user_input is not None:
            self._humidity_enabled = bool(user_input.get("humidity_enabled", False))
            self._humidity_sensor = user_input.get("humidity_sensor", "") or ""
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="humidity",
            data_schema=vol.Schema(
                {
                    vol.Required("humidity_enabled", default=False): BooleanSelector(),
                    vol.Optional("humidity_sensor"): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="humidity")
                    ),
                }
            ),
            description_placeholders={"room_name": self._room_name},
            last_step=False,
        )

    # ── Step 5: Confirm and create ────────────────────────────────────

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 5/5 — Review configuration and create room subentry."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._room_name,
                data={
                    "room_name": self._room_name,
                    "thermostat_sensor": self._thermostat_sensor,
                    "trvs": self._trvs,
                    "fixed_trvs": self._fixed_trvs,
                    "fixed_trv_temp": self._fixed_trv_temp,
                    "override_enabled": self._override_enabled,
                    "override_duration_minutes": self._override_duration_minutes,
                    "window_sensors": self._window_sensors,
                    "airing_mode_enabled": self._airing_mode_enabled,
                    "airing_duration": self._airing_duration,
                    "airing_duration_seconds": self._airing_duration_seconds,
                    "default_temp_enabled": self._default_temp_enabled,
                    "default_temp": self._default_temp,
                    "schedules": self._schedules,
                    "humidity_enabled": self._humidity_enabled,
                    "humidity_sensor": self._humidity_sensor,
                },
            )

        trvs_s = f"{len(self._trvs)} radiator(s)" if self._trvs else "none"
        fixed_s = f"{len(self._fixed_trvs)} fixed" if self._fixed_trvs else "none"
        windows_s = (
            f"{len(self._window_sensors)} sensor(s)" if self._window_sensors else "none"
        )
        airing_s = (
            f"enabled ({self._airing_duration})" if self._airing_mode_enabled else "disabled"
        )
        default_temp_s = f"{self._default_temp}°C" if self._default_temp_enabled else "disabled"
        schedules_s = f"{len(self._schedules)} schedule(s)" if self._schedules else "none"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "room_name": self._room_name,
                "thermostat_sensor": self._thermostat_sensor or "none",
                "trvs": trvs_s,
                "fixed_trvs": fixed_s,
                "override_enabled": "enabled" if self._override_enabled else "disabled",
                "window_sensors": windows_s,
                "airing_enabled": airing_s,
                "default_temp": default_temp_s,
                "schedules_count": schedules_s,
                "humidity_enabled": "enabled" if self._humidity_enabled else "disabled",
            },
            last_step=True,
        )


# ──────────────────────────────────────────────────────────────────────
# Subentry flow — Vacation Settings (single-step, singleton)
# ──────────────────────────────────────────────────────────────────────

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
        current_enabled = entry.options.get(CONF_VACATION_ENABLED, False)
        current_mode = entry.options.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE)

        if user_input is not None:
            vacation_calendar = user_input.get(CONF_VACATION_CALENDAR) or ""
            return self.async_create_entry(
                title="Vacation",
                data={
                    CONF_VACATION_ENABLED: bool(user_input.get(CONF_VACATION_ENABLED, False)),
                    CONF_VACATION_MODE: user_input.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
                    CONF_VACATION_CALENDAR: vacation_calendar,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VACATION_ENABLED, default=current_enabled): BooleanSelector(),
                    vol.Required(
                        CONF_VACATION_MODE, default=current_mode
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["frost", "eco", "off"],
                            mode=SelectSelectorMode.LIST,
                            translation_key="vacation_mode",
                        )
                    ),
                    vol.Optional(CONF_VACATION_CALENDAR): EntitySelector(
                        EntitySelectorConfig(domain="calendar")
                    ),
                }
            ),
        )


# ──────────────────────────────────────────────────────────────────────
# Main config flow
# ──────────────────────────────────────────────────────────────────────

class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor.

    2 steps:
      1. Ollama — URL + model, connection tested
      2. InfluxDB — URL, token, org, bucket, connection tested → create entry

    Room management after initial setup:
      Add Room: "➕ Add Room" button on integration card → SHARoomSubentryFlowHandler
      Remove Room: ⋮ → Delete on a room subentry card
    Global settings: gear icon → options flow (connection settings + vacation)
    """

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
        """Return subentry flow handlers supported by this integration."""
        return {
            "room": SHARoomSubentryFlowHandler,
            "vacation": SHAVacationSubentryFlowHandler,
        }

    def __init__(self) -> None:
        self._ollama_data: dict = {}

    # ── Step 1: Ollama ────────────────────────────────────────────────

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
                }
            ),
            errors=errors,
        )

    # ── Step 2: InfluxDB → create entry ──────────────────────────────

    async def async_step_influxdb(self, user_input=None) -> ConfigFlowResult:
        """Step 2 — InfluxDB configuration. Creates the config entry on success."""
        errors = {}

        if user_input is not None:
            if await _test_influxdb(
                user_input[CONF_INFLUXDB_URL],
                user_input[CONF_INFLUXDB_TOKEN],
                user_input[CONF_INFLUXDB_ORG],
                user_input[CONF_INFLUXDB_BUCKET],
            ):
                return self.async_create_entry(
                    title="Smart Heating Advisor",
                    data={
                        **self._ollama_data,
                        **user_input,
                        CONF_WEATHER_ENTITY: "weather.forecast_home",
                        CONF_ROOM_CONFIGS: [],
                    },
                )
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


# ──────────────────────────────────────────────────────────────────────
# Options flow — global settings (2 steps)
# ──────────────────────────────────────────────────────────────────────

class SHAOptionsFlow(config_entries.OptionsFlow):
    """Handle SHA options — connection settings, weather entity, and debug toggle.

    Single step: connection settings + weather entity + debug toggle.
    Vacation is configured via the dedicated Vacation subentry on the integration card.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Connection settings and global options."""
        cd = self._config_entry.data
        current_debug = self._config_entry.options.get(CONF_DEBUG_LOGGING, False)

        if user_input is not None:
            debug = user_input.get(CONF_DEBUG_LOGGING, False)

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

            return self.async_create_entry(title="", data={CONF_DEBUG_LOGGING: debug})

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
                    ): str,
                    vol.Required(CONF_DEBUG_LOGGING, default=current_debug): bool,
                }
            ),
        )
