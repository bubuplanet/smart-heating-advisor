"""Config flow for Smart Heating Advisor."""
import logging
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
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
# Area / entity registry helpers
# ──────────────────────────────────────────────────────────────────────

def _get_area_entities(hass, area_id: str, domain: str, device_class: str | None = None) -> list[str]:
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
# Config flow
# ──────────────────────────────────────────────────────────────────────

class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor.

    5 steps:
      1. Ollama — URL + model, connection tested
      2. InfluxDB — URL, token, org, bucket, connection tested
      3. Rooms — multi-select HA Areas
      4. Room entities — per-room entity confirmation (iterates once per area)
      5. HA entities — weather entity
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "SHAOptionsFlow":
        return SHAOptionsFlow(config_entry)

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
        """Step 3 — Select rooms from HA Areas."""
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
                # User skipped — proceed without rooms
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
                            options=[{"value": aid, "label": aname} for aid, aname in sorted_areas],
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
# Options flow
# ──────────────────────────────────────────────────────────────────────

class SHAOptionsFlow(config_entries.OptionsFlow):
    """Handle SHA options — debug logging toggle plus room management."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._debug_logging: bool = False
        self._pending_rooms: list[dict] = []
        self._new_room_configs: list[dict] = []

    # ── Step: init ────────────────────────────────────────────────────

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Show current rooms + options to add/remove."""
        current_debug = self._config_entry.options.get(CONF_DEBUG_LOGGING, False)
        current_rooms = self._config_entry.data.get(CONF_ROOM_CONFIGS, [])
        room_names = [r.get("room_name", "?") for r in current_rooms]

        if user_input is not None:
            self._debug_logging = user_input.get(CONF_DEBUG_LOGGING, current_debug)
            action = user_input.get("action", "settings")

            if action == "add":
                self._new_room_configs = []
                self._pending_rooms = []
                return await self.async_step_add_rooms()
            if action == "remove":
                return await self.async_step_remove_rooms()
            # settings only
            return self.async_create_entry(
                title="",
                data={CONF_DEBUG_LOGGING: self._debug_logging},
            )

        action_options = [
            {"value": "settings", "label": "Save settings only"},
            {"value": "add", "label": "Add new room(s)"},
        ]
        if current_rooms:
            action_options.append({"value": "remove", "label": "Remove room(s)"})

        description = (
            f"Currently managing {len(current_rooms)} room(s)"
            + (f": {', '.join(room_names)}" if room_names else "")
            + "."
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEBUG_LOGGING, default=current_debug): bool,
                    vol.Required("action", default="settings"): SelectSelector(
                        SelectSelectorConfig(
                            options=action_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders={"rooms_summary": description},
        )

    # ── Step: add_rooms ───────────────────────────────────────────────

    async def async_step_add_rooms(self, user_input=None) -> ConfigFlowResult:
        """Select new areas to add as rooms."""
        area_reg = ar.async_get(self.hass)
        existing_rooms = self._config_entry.data.get(CONF_ROOM_CONFIGS, [])
        existing_area_ids = {r.get("area_id") for r in existing_rooms}
        all_areas = {a.id: a.name for a in area_reg.async_list_areas()}
        available_areas = {
            aid: aname for aid, aname in all_areas.items()
            if aid not in existing_area_ids
        }

        if not available_areas:
            # All areas already added — go back
            return await self.async_step_init()

        if user_input is not None:
            selected_ids = user_input.get("selected_areas", [])
            if not selected_ids:
                return await self.async_step_init()

            self._pending_rooms = []
            for area_id in selected_ids:
                area_name = available_areas.get(area_id, area_id)
                temp_sensors = _get_area_entities(self.hass, area_id, "sensor", "temperature")
                trvs = _get_area_entities(self.hass, area_id, "climate")
                self._pending_rooms.append({
                    "area_id": area_id,
                    "room_name": area_name,
                    "temp_sensor": temp_sensors[0] if temp_sensors else "",
                    "trvs": trvs,
                })
            return await self.async_step_add_room_entities()

        sorted_areas = sorted(available_areas.items(), key=lambda x: x[1])
        return self.async_show_form(
            step_id="add_rooms",
            data_schema=vol.Schema(
                {
                    vol.Optional("selected_areas"): SelectSelector(
                        SelectSelectorConfig(
                            options=[{"value": aid, "label": aname} for aid, aname in sorted_areas],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    # ── Step: add_room_entities ───────────────────────────────────────

    async def async_step_add_room_entities(self, user_input=None) -> ConfigFlowResult:
        """Confirm detected entities for each new room (iterates)."""
        if user_input is not None and self._pending_rooms:
            room_draft = self._pending_rooms[0]
            trvs_raw = user_input.get("trvs", [])
            if isinstance(trvs_raw, str):
                trvs_raw = [trvs_raw] if trvs_raw else []
            self._new_room_configs.append({
                "area_id": room_draft["area_id"],
                "room_name": user_input.get("room_name", room_draft["room_name"]).strip(),
                "temp_sensor": user_input.get("temp_sensor", ""),
                "trvs": trvs_raw,
            })
            self._pending_rooms.pop(0)

        if not self._pending_rooms:
            # All new rooms confirmed — save and reload
            return await self._async_save_rooms_and_complete(
                added=self._new_room_configs,
                removed_names=[],
            )

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

        return self.async_show_form(
            step_id="add_room_entities",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"room_name": room_name},
        )

    # ── Step: remove_rooms ────────────────────────────────────────────

    async def async_step_remove_rooms(self, user_input=None) -> ConfigFlowResult:
        """Select rooms to remove."""
        current_rooms = self._config_entry.data.get(CONF_ROOM_CONFIGS, [])

        if not current_rooms:
            return await self.async_step_init()

        if user_input is not None:
            names_to_remove = user_input.get("rooms_to_remove", [])
            return await self._async_save_rooms_and_complete(
                added=[],
                removed_names=names_to_remove,
            )

        room_options = [
            {"value": r["room_name"], "label": r["room_name"]}
            for r in current_rooms
        ]
        return self.async_show_form(
            step_id="remove_rooms",
            data_schema=vol.Schema(
                {
                    vol.Optional("rooms_to_remove"): SelectSelector(
                        SelectSelectorConfig(
                            options=room_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    # ── Internal: save rooms to entry.data + reload ───────────────────

    async def _async_save_rooms_and_complete(
        self,
        added: list[dict],
        removed_names: list[str],
    ) -> ConfigFlowResult:
        """Merge room changes into entry.data, trigger reload, complete flow."""
        current_rooms = list(self._config_entry.data.get(CONF_ROOM_CONFIGS, []))

        # Remove requested rooms
        if removed_names:
            current_rooms = [r for r in current_rooms if r.get("room_name") not in removed_names]

        # Add new rooms (deduplicate by room_name)
        existing_names = {r.get("room_name") for r in current_rooms}
        for new_room in added:
            if new_room.get("room_name") not in existing_names:
                current_rooms.append(new_room)
                existing_names.add(new_room.get("room_name"))

        self.hass.config_entries.async_update_entry(
            self._config_entry,
            data={**self._config_entry.data, CONF_ROOM_CONFIGS: current_rooms},
        )
        _LOGGER.info(
            "SHA options flow: room_configs updated — %d room(s). Scheduling reload.",
            len(current_rooms),
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self._config_entry.entry_id)
        )
        return self.async_create_entry(
            title="",
            data={CONF_DEBUG_LOGGING: self._debug_logging},
        )
