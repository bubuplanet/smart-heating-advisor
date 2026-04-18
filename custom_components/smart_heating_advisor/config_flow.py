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
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
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
    DEFAULT_AIRING_DURATION,
    DEFAULT_HUMIDITY_THRESHOLD,
    MIN_HUMIDITY_THRESHOLD,
    MAX_HUMIDITY_THRESHOLD,
    MIN_DEFAULT_TEMP,
    MAX_DEFAULT_TEMP,
)
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY_KEY = "__manual__"


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
# Area / entity registry helpers
# ──────────────────────────────────────────────────────────────────────

def _get_area_entities(
    hass, area_id: str, domain: str, device_class: str | None = None
) -> list[str]:
    """Find entity IDs in an area by domain and optional device class.

    Checks both the entity's own area assignment and the area of its parent device.
    Skips disabled entities.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    result = []

    for entry in ent_reg.entities.values():
        if entry.domain != domain:
            continue
        if entry.disabled:
            continue
        if device_class and (
            entry.device_class != device_class
            and entry.original_device_class != device_class
        ):
            continue

        entity_area = entry.area_id
        device_area = None
        if entry.device_id:
            dev = dev_reg.async_get(entry.device_id)
            if dev:
                device_area = dev.area_id

        if area_id in (entity_area, device_area):
            result.append(entry.entity_id)

    return result


# ──────────────────────────────────────────────────────────────────────
# Subentry flow — Add Room (6-step wizard)
# ──────────────────────────────────────────────────────────────────────

class SHARoomSubentryFlowHandler(ConfigSubentryFlow):
    """6-step room wizard for adding a room to Smart Heating Advisor.

    Step 1 (user):        Room identity — area selection + room name
    Step 2 (trvs):        TRV configuration — main + fixed TRVs
    Step 3 (windows):     Window sensors + airing mode
    Step 4 (temperature): Temperature profile — sensor + standby temp
    Step 5 (humidity):    Humidity control
    Step 6 (confirm):     Review and create
    """

    def __init__(self) -> None:
        self._area_id: str | None = None
        self._room_name: str = ""
        self._detected_trvs: list[str] = []
        self._detected_temp: str = ""
        # accumulated room config
        self._trvs: list[str] = []
        self._fixed_trvs: list[str] = []
        self._fixed_trv_temp: float = DEFAULT_DEFAULT_TEMP
        self._window_sensors: list[str] = []
        self._airing_mode_enabled: bool = True
        self._airing_duration_minutes: int = DEFAULT_AIRING_DURATION
        self._temp_sensor: str = ""
        self._thermostat_sensor: str = ""
        self._default_temp_enabled: bool = True
        self._default_temp: float = DEFAULT_DEFAULT_TEMP
        self._humidity_enabled: bool = False
        self._humidity_sensor: str = ""
        self._humidity_threshold: float = DEFAULT_HUMIDITY_THRESHOLD

    def _existing_room_ids(self) -> set[str]:
        """Return room_ids of rooms already configured as subentries."""
        entry = self._get_entry()
        return {
            _room_name_to_id(s.data.get("room_name", ""))
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

    # ── Step 1: Room identity ─────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 1/6 — Room identity: select area and confirm room name."""
        existing_ids = self._existing_room_ids()
        area_reg = ar.async_get(self.hass)
        available_areas: dict[str, str] = {
            area.id: area.name
            for area in sorted(area_reg.async_list_areas(), key=lambda a: a.name)
            if _room_name_to_id(area.name) not in existing_ids
        }

        errors: dict[str, str] = {}

        if user_input is not None:
            area_id = user_input.get("area_id", MANUAL_ENTRY_KEY)
            room_name = user_input.get("room_name", "").strip()

            if area_id != MANUAL_ENTRY_KEY:
                area_name = available_areas.get(area_id, "")
                if not room_name:
                    room_name = area_name
                if _room_name_to_id(room_name) in existing_ids:
                    errors["room_name"] = "room_already_exists"
                else:
                    self._area_id = area_id
                    self._room_name = room_name
                    self._detected_trvs = _get_area_entities(self.hass, area_id, "climate")
                    detected_temps = _get_area_entities(self.hass, area_id, "sensor", "temperature")
                    self._detected_temp = detected_temps[0] if detected_temps else ""
                    return await self.async_step_trvs()
            else:
                if not room_name:
                    errors["room_name"] = "required"
                elif _room_name_to_id(room_name) in existing_ids:
                    errors["room_name"] = "room_already_exists"
                else:
                    self._area_id = None
                    self._room_name = room_name
                    self._detected_trvs = []
                    self._detected_temp = ""
                    return await self.async_step_trvs()

        area_options = [
            {"value": MANUAL_ENTRY_KEY, "label": "➕ Enter room name manually"},
        ] + [
            {"value": aid, "label": aname}
            for aid, aname in available_areas.items()
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("area_id", default=MANUAL_ENTRY_KEY): SelectSelector(
                        SelectSelectorConfig(
                            options=area_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional("room_name"): str,
                }
            ),
            errors=errors,
        )

    # ── Step 2: TRVs ─────────────────────────────────────────────────

    async def async_step_trvs(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 2/6 — TRV configuration."""
        if user_input is not None:
            trvs = user_input.get("trvs", [])
            self._trvs = list(trvs) if isinstance(trvs, list) else ([trvs] if trvs else [])
            fixed_trvs = user_input.get("fixed_trvs", [])
            self._fixed_trvs = list(fixed_trvs) if isinstance(fixed_trvs, list) else ([fixed_trvs] if fixed_trvs else [])
            self._fixed_trv_temp = float(user_input.get("fixed_trv_temp", DEFAULT_DEFAULT_TEMP))
            return await self.async_step_windows()

        schema_dict: dict = {}
        if self._detected_trvs:
            schema_dict[vol.Optional("trvs", default=self._detected_trvs)] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )
        else:
            schema_dict[vol.Optional("trvs")] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )
        schema_dict[vol.Optional("fixed_trvs")] = EntitySelector(
            EntitySelectorConfig(domain="climate", multiple=True)
        )
        schema_dict[vol.Optional("fixed_trv_temp", default=DEFAULT_DEFAULT_TEMP)] = NumberSelector(
            NumberSelectorConfig(
                min=5.0, max=35.0, step=0.5,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="°C",
            )
        )

        return self.async_show_form(
            step_id="trvs",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"room_name": self._room_name},
        )

    # ── Step 3: Windows / airing mode ────────────────────────────────

    async def async_step_windows(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 3/6 — Window sensors and airing mode."""
        if user_input is not None:
            sensors = user_input.get("window_sensors", [])
            self._window_sensors = list(sensors) if isinstance(sensors, list) else ([sensors] if sensors else [])
            self._airing_mode_enabled = bool(user_input.get("airing_mode_enabled", True))
            self._airing_duration_minutes = int(user_input.get("airing_duration_minutes", DEFAULT_AIRING_DURATION))
            return await self.async_step_temperature()

        return self.async_show_form(
            step_id="windows",
            data_schema=vol.Schema(
                {
                    vol.Optional("window_sensors"): EntitySelector(
                        EntitySelectorConfig(
                            domain="binary_sensor",
                            device_class="window",
                            multiple=True,
                        )
                    ),
                    vol.Required("airing_mode_enabled", default=True): BooleanSelector(),
                    vol.Required(
                        "airing_duration_minutes", default=DEFAULT_AIRING_DURATION
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=180, step=5,
                            mode=NumberSelectorMode.SLIDER,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
            description_placeholders={"room_name": self._room_name},
        )

    # ── Step 4: Temperature profile ───────────────────────────────────

    async def async_step_temperature(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 4/6 — Temperature profile: sensor and standby temperature."""
        if user_input is not None:
            self._temp_sensor = user_input.get("temp_sensor", "")
            self._thermostat_sensor = user_input.get("thermostat_sensor", "")
            self._default_temp_enabled = bool(user_input.get("default_temp_enabled", True))
            self._default_temp = float(user_input.get("default_temp", DEFAULT_DEFAULT_TEMP))
            return await self.async_step_humidity()

        schema_dict: dict = {}
        if self._detected_temp:
            schema_dict[vol.Optional("temp_sensor", default=self._detected_temp)] = EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        else:
            schema_dict[vol.Optional("temp_sensor")] = EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        schema_dict[vol.Optional("thermostat_sensor")] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        )
        schema_dict[vol.Required("default_temp_enabled", default=True)] = BooleanSelector()
        schema_dict[vol.Required("default_temp", default=DEFAULT_DEFAULT_TEMP)] = NumberSelector(
            NumberSelectorConfig(
                min=MIN_DEFAULT_TEMP, max=MAX_DEFAULT_TEMP, step=0.5,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="°C",
            )
        )

        return self.async_show_form(
            step_id="temperature",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"room_name": self._room_name},
        )

    # ── Step 5: Humidity control ──────────────────────────────────────

    async def async_step_humidity(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 5/6 — Humidity control."""
        if user_input is not None:
            self._humidity_enabled = bool(user_input.get("humidity_enabled", False))
            self._humidity_sensor = user_input.get("humidity_sensor", "")
            self._humidity_threshold = float(user_input.get("humidity_threshold", DEFAULT_HUMIDITY_THRESHOLD))
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="humidity",
            data_schema=vol.Schema(
                {
                    vol.Required("humidity_enabled", default=False): BooleanSelector(),
                    vol.Optional("humidity_sensor"): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="humidity")
                    ),
                    vol.Required(
                        "humidity_threshold", default=DEFAULT_HUMIDITY_THRESHOLD
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_HUMIDITY_THRESHOLD,
                            max=MAX_HUMIDITY_THRESHOLD,
                            step=1.0,
                            mode=NumberSelectorMode.SLIDER,
                            unit_of_measurement="%",
                        )
                    ),
                }
            ),
            description_placeholders={"room_name": self._room_name},
        )

    # ── Step 6: Confirm and create ────────────────────────────────────

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Step 6/6 — Confirm room configuration and create subentry."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._room_name,
                data={
                    "room_name": self._room_name,
                    "area_id": self._area_id,
                    "temp_sensor": self._temp_sensor,
                    "thermostat_sensor": self._thermostat_sensor,
                    "trvs": self._trvs,
                    "fixed_trvs": self._fixed_trvs,
                    "fixed_trv_temp": self._fixed_trv_temp,
                    "window_sensors": self._window_sensors,
                    "airing_mode_enabled": self._airing_mode_enabled,
                    "airing_duration_minutes": self._airing_duration_minutes,
                    "default_temp_enabled": self._default_temp_enabled,
                    "default_temp": self._default_temp,
                    "humidity_enabled": self._humidity_enabled,
                    "humidity_sensor": self._humidity_sensor,
                    "humidity_threshold": self._humidity_threshold,
                },
            )

        trv_summary = ", ".join(self._trvs) if self._trvs else "none"
        window_summary = ", ".join(self._window_sensors) if self._window_sensors else "none"
        sensor_summary = self._temp_sensor or "none"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "room_name": self._room_name,
                "trvs": trv_summary,
                "temp_sensor": sensor_summary,
                "window_sensors": window_summary,
                "default_temp": str(self._default_temp),
                "humidity_enabled": "yes" if self._humidity_enabled else "no",
            },
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
        return {"room": SHARoomSubentryFlowHandler}

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
    """Handle SHA options — connection settings, weather entity, debug, and vacation.

    Step 1 (init):     Connection settings + weather entity + debug toggle
    Step 2 (vacation): Vacation mode configuration
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._init_data: dict = {}

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Step 1 — Connection settings and global entities."""
        cd = self._config_entry.data
        current_debug = self._config_entry.options.get(CONF_DEBUG_LOGGING, False)

        if user_input is not None:
            self._init_data = user_input
            return await self.async_step_vacation()

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

    async def async_step_vacation(self, user_input=None) -> ConfigFlowResult:
        """Step 2 — Vacation mode settings."""
        current_opts = self._config_entry.options

        if user_input is not None:
            init = self._init_data
            debug = init.get(CONF_DEBUG_LOGGING, False)

            data_keys = [
                CONF_OLLAMA_URL, CONF_OLLAMA_MODEL,
                CONF_INFLUXDB_URL, CONF_INFLUXDB_TOKEN,
                CONF_INFLUXDB_ORG, CONF_INFLUXDB_BUCKET,
                CONF_WEATHER_ENTITY,
            ]
            cd = self._config_entry.data
            data_changed = any(
                init.get(k) != cd.get(k)
                for k in data_keys
                if k in init
            )

            if data_changed:
                new_data = dict(cd)
                for k in data_keys:
                    if k in init:
                        new_data[k] = init[k]
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
                    CONF_VACATION_ENABLED: bool(user_input.get(CONF_VACATION_ENABLED, False)),
                    CONF_VACATION_MODE: user_input.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
                    CONF_VACATION_CALENDAR: user_input.get(CONF_VACATION_CALENDAR, ""),
                },
            )

        return self.async_show_form(
            step_id="vacation",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_VACATION_ENABLED,
                        default=current_opts.get(CONF_VACATION_ENABLED, False),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_VACATION_MODE,
                        default=current_opts.get(CONF_VACATION_MODE, DEFAULT_VACATION_MODE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["frost", "eco", "off"],
                            mode=SelectSelectorMode.LIST,
                            translation_key="vacation_mode",
                        )
                    ),
                    vol.Optional(
                        CONF_VACATION_CALENDAR,
                        default=current_opts.get(CONF_VACATION_CALENDAR, ""),
                    ): EntitySelector(
                        EntitySelectorConfig(domain="calendar")
                    ),
                }
            ),
        )
