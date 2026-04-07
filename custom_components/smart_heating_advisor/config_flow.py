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
    EntitySelector,
    EntitySelectorConfig,
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
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_INFLUXDB_URL,
    DEFAULT_INFLUXDB_ORG,
    DEFAULT_INFLUXDB_BUCKET,
)
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)

# Sentinel value for the "create room manually" option in the area selector
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
# Subentry flow — Add Room
# ──────────────────────────────────────────────────────────────────────

class SHARoomSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding a room to Smart Heating Advisor.

    Flow paths:
      async_step_user  →  area selected  →  async_step_entities  →  create
                       →  manual chosen  →  async_step_manual    →  create
    """

    def __init__(self) -> None:
        self._area_id: str | None = None
        self._area_name: str = ""

    def _existing_room_ids(self) -> set[str]:
        """Return room_ids of rooms already configured as subentries."""
        entry = self._get_entry()
        return {
            _room_name_to_id(s.data.get("room_name", ""))
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Entry point — select an HA Area or choose manual creation."""
        existing_ids = self._existing_room_ids()

        # Build the area options: manual sentinel first, then available areas
        area_reg = ar.async_get(self.hass)
        available_areas: dict[str, str] = {}
        for area in sorted(area_reg.async_list_areas(), key=lambda a: a.name):
            if _room_name_to_id(area.name) not in existing_ids:
                available_areas[area.id] = area.name

        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get("area_id", MANUAL_ENTRY_KEY)
            if selected == MANUAL_ENTRY_KEY:
                return await self.async_step_manual()
            if selected in available_areas:
                self._area_id = selected
                self._area_name = available_areas[selected]
                return await self.async_step_entities()
            errors["area_id"] = "no_areas_available"

        area_options = [
            {"value": MANUAL_ENTRY_KEY, "label": "➕ Create room manually (not an HA Area)"},
        ]
        area_options += [
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
                }
            ),
            errors=errors,
        )

    async def async_step_entities(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Confirm auto-detected entities for the selected HA Area."""
        if self._area_id:
            detected_sensors = _get_area_entities(
                self.hass, self._area_id, "sensor", "temperature"
            )
            detected_trvs = _get_area_entities(self.hass, self._area_id, "climate")
        else:
            detected_sensors = []
            detected_trvs = []

        if user_input is not None:
            room_name = user_input.get("room_name", self._area_name).strip()
            trvs_raw = user_input.get("trvs", [])
            if isinstance(trvs_raw, str):
                trvs_raw = [trvs_raw] if trvs_raw else []
            return self.async_create_entry(
                title=room_name,
                data={
                    "room_name": room_name,
                    "area_id": self._area_id,
                    "temp_sensor": user_input.get("temp_sensor", ""),
                    "trvs": trvs_raw,
                },
            )

        detected_temp = detected_sensors[0] if detected_sensors else ""
        schema_dict: dict = {
            vol.Required("room_name", default=self._area_name): str,
        }
        if detected_temp:
            schema_dict[vol.Optional("temp_sensor", default=detected_temp)] = EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        else:
            schema_dict[vol.Optional("temp_sensor")] = EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        if detected_trvs:
            schema_dict[vol.Optional("trvs", default=detected_trvs)] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )
        else:
            schema_dict[vol.Optional("trvs")] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"room_name": self._area_name},
        )

    async def async_step_manual(
        self, user_input: dict | None = None
    ) -> SubentryFlowResult:
        """Manual room creation — free-text name + optional entity selectors."""
        errors: dict[str, str] = {}
        existing_ids = self._existing_room_ids()

        if user_input is not None:
            room_name = user_input.get("room_name", "").strip()
            if not room_name:
                errors["room_name"] = "required"
            elif _room_name_to_id(room_name) in existing_ids:
                errors["room_name"] = "room_already_exists"
            else:
                trvs_raw = user_input.get("trvs", [])
                if isinstance(trvs_raw, str):
                    trvs_raw = [trvs_raw] if trvs_raw else []
                return self.async_create_entry(
                    title=room_name,
                    data={
                        "room_name": room_name,
                        "area_id": None,
                        "temp_sensor": user_input.get("temp_sensor", ""),
                        "trvs": trvs_raw,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): str,
                    vol.Optional("temp_sensor"): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional("trvs"): EntitySelector(
                        EntitySelectorConfig(domain="climate", multiple=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "tip": (
                    "Tip: you can also create an Area first in "
                    "Settings → Areas, then use it here for "
                    "auto-detection of entities."
                )
            },
        )


# ──────────────────────────────────────────────────────────────────────
# Main config flow
# ──────────────────────────────────────────────────────────────────────

class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor.

    5 steps:
      1. Ollama — URL + model, connection tested
      2. InfluxDB — URL, token, org, bucket, connection tested
      3. Rooms — multi-select HA Areas (optional, skip for later)
      4. Room entities — per-room entity confirmation (iterates once per area)
      5. HA entities — weather entity

    Room management after initial setup:
      Add Room: "➕ Add Room" button on integration card → SHARoomSubentryFlowHandler
      Remove Room: ⋮ → Delete on a room subentry card
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
        self._influxdb_data: dict = {}
        self._pending_rooms: list[dict] = []
        self._room_configs: list[dict] = []

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

    # ── Step 2: InfluxDB ──────────────────────────────────────────────

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
                return await self.async_step_rooms()
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

    # ── Step 3: Room selection ────────────────────────────────────────

    async def async_step_rooms(self, user_input=None) -> ConfigFlowResult:
        """Step 3 — Select rooms from HA Areas (optional)."""
        area_reg = ar.async_get(self.hass)
        areas = {a.id: a.name for a in area_reg.async_list_areas()}

        if not areas:
            _LOGGER.info("SHA config flow: no HA areas found — skipping room selection")
            self._pending_rooms = []
            self._room_configs = []
            return await self.async_step_entities()

        if user_input is not None:
            selected_ids = user_input.get("selected_areas", [])
            if not selected_ids:
                self._pending_rooms = []
                self._room_configs = []
                return await self.async_step_entities()

            self._room_configs = []
            self._pending_rooms = []
            for area_id in selected_ids:
                area_name = areas.get(area_id, area_id)
                temp_sensors = _get_area_entities(self.hass, area_id, "sensor", "temperature")
                trvs = _get_area_entities(self.hass, area_id, "climate")
                self._pending_rooms.append({
                    "area_id": area_id,
                    "room_name": area_name,
                    "temp_sensor": temp_sensors[0] if temp_sensors else "",
                    "trvs": trvs,
                })
                _LOGGER.debug(
                    "SHA config flow: area '%s' — temp_sensors=%s trvs=%s",
                    area_name, temp_sensors, trvs,
                )
            return await self.async_step_room_entities()

        sorted_areas = sorted(areas.items(), key=lambda x: x[1])
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema(
                {
                    vol.Optional("selected_areas"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": aid, "label": aname}
                                for aid, aname in sorted_areas
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    # ── Step 4: Per-room entity confirmation ──────────────────────────

    async def async_step_room_entities(self, user_input=None) -> ConfigFlowResult:
        """Step 4 — Confirm detected entities for each selected room (iterates)."""
        if user_input is not None and self._pending_rooms:
            room_draft = self._pending_rooms[0]
            trvs_raw = user_input.get("trvs", [])
            if isinstance(trvs_raw, str):
                trvs_raw = [trvs_raw] if trvs_raw else []
            self._room_configs.append({
                "area_id": room_draft["area_id"],
                "room_name": user_input.get("room_name", room_draft["room_name"]).strip(),
                "temp_sensor": user_input.get("temp_sensor", ""),
                "trvs": trvs_raw,
            })
            self._pending_rooms.pop(0)

        if not self._pending_rooms:
            return await self.async_step_entities()

        room_draft = self._pending_rooms[0]
        room_name = room_draft["room_name"]
        detected_temp = room_draft.get("temp_sensor", "")
        detected_trvs = room_draft.get("trvs", [])

        schema_dict: dict = {
            vol.Required("room_name", default=room_name): str,
        }
        if detected_temp:
            schema_dict[vol.Optional("temp_sensor", default=detected_temp)] = EntitySelector(
                EntitySelectorConfig(domain="sensor")
            )
        else:
            schema_dict[vol.Optional("temp_sensor")] = EntitySelector(
                EntitySelectorConfig(domain="sensor")
            )
        if detected_trvs:
            schema_dict[vol.Optional("trvs", default=detected_trvs)] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )
        else:
            schema_dict[vol.Optional("trvs")] = EntitySelector(
                EntitySelectorConfig(domain="climate", multiple=True)
            )

        remaining = len(self._pending_rooms)
        return self.async_show_form(
            step_id="room_entities",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "room_name": room_name,
                "remaining": str(remaining),
            },
        )

    # ── Step 5: Weather entity ────────────────────────────────────────

    async def async_step_entities(self, user_input=None) -> ConfigFlowResult:
        """Step 5 — HA entity configuration (weather)."""
        errors = {}

        if user_input is not None:
            weather = self.hass.states.get(user_input[CONF_WEATHER_ENTITY])
            if not weather:
                errors[CONF_WEATHER_ENTITY] = "entity_not_found"
            else:
                return self.async_create_entry(
                    title="Smart Heating Advisor",
                    data={
                        **self._ollama_data,
                        **self._influxdb_data,
                        **user_input,
                        CONF_ROOM_CONFIGS: self._room_configs,
                    },
                )

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_WEATHER_ENTITY, default="weather.forecast_home"): str,
                }
            ),
            errors=errors,
        )


# ──────────────────────────────────────────────────────────────────────
# Options flow — global settings only
# ──────────────────────────────────────────────────────────────────────

class SHAOptionsFlow(config_entries.OptionsFlow):
    """Handle SHA options — global connection settings and debug toggle.

    Room management is done via:
      ➕ Add Room button on the integration card (subentry flow)
      ⋮ Delete on each room card (triggers full cleanup via reload)
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Show global settings form."""
        cd = self._config_entry.data
        current_debug = self._config_entry.options.get(CONF_DEBUG_LOGGING, False)

        if user_input is not None:
            debug = user_input.get(CONF_DEBUG_LOGGING, current_debug)

            # If any connection/entity setting changed, update entry.data and reload.
            # Debug logging is always saved to entry.options (live update, no reload).
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
                new_data = dict(cd)
                for k in data_keys:
                    if k in user_input:
                        new_data[k] = user_input[k]
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                _LOGGER.info("SHA options: connection settings changed — scheduling reload")
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._config_entry.entry_id)
                )

            return self.async_create_entry(
                title="",
                data={CONF_DEBUG_LOGGING: debug},
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
                    ): str,
                    vol.Required(CONF_DEBUG_LOGGING, default=current_debug): bool,
                }
            ),
        )
