"""Config flow for Smart Heating Advisor."""
import logging
import re
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_OLLAMA_URL,
    CONF_OLLAMA_MODEL,
    CONF_INFLUXDB_URL,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_BUCKET,
    CONF_TEMP_SENSOR,
    CONF_HEATING_RATE_HELPER,
    CONF_TARGET_TEMP,
    CONF_TARGET_TIME,
    CONF_WEATHER_ENTITY,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_INFLUXDB_URL,
    DEFAULT_INFLUXDB_ORG,
    DEFAULT_INFLUXDB_BUCKET,
    DEFAULT_TARGET_TEMP,
    DEFAULT_TARGET_TIME,
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


async def _test_ollama(url: str, model: str) -> bool:
    """Test Ollama connection and verify model exists."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url.rstrip('/')}/api/tags",
                timeout=aiohttp.ClientTimeout(total=10)
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
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                return response.status == 200
    except Exception:
        return False


async def async_create_room_helpers(hass: HomeAssistant, room_name: str) -> dict:
    """Create all required SHA helpers for a room.
    
    Returns dict with created entity IDs.
    """
    room_id = _room_name_to_id(room_name)
    _LOGGER.info("Creating SHA helpers for room: %s (id: %s)", room_name, room_id)

    created = {}
    errors = []

    # ── Toggle helpers ────────────────────────────────────────────
    toggle_helpers = {
        f"sha_{room_id}_automation_running": f"SHA {room_name} Automation Running",
        f"sha_{room_id}_airing_mode":        f"SHA {room_name} Airing Mode",
        f"sha_{room_id}_preheat_notified":   f"SHA {room_name} Preheat Notified",
        f"sha_{room_id}_target_notified":    f"SHA {room_name} Target Notified",
        f"sha_{room_id}_standby_notified":   f"SHA {room_name} Standby Notified",
        f"sha_{room_id}_vacation_notified":  f"SHA {room_name} Vacation Notified",
    }

    for entity_id_suffix, name in toggle_helpers.items():
        full_entity_id = f"input_boolean.{entity_id_suffix}"
        # Skip if already exists
        if hass.states.get(full_entity_id) is not None:
            _LOGGER.debug("Helper %s already exists — skipping", full_entity_id)
            created[entity_id_suffix] = full_entity_id
            continue
        try:
            await hass.services.async_call(
                "input_boolean",
                "create",
                {
                    "name": name,
                    "icon": "mdi:toggle-switch",
                },
                blocking=True,
            )
            created[entity_id_suffix] = full_entity_id
            _LOGGER.debug("Created toggle helper: %s", full_entity_id)
        except Exception as e:
            _LOGGER.error("Failed to create toggle helper %s: %s", full_entity_id, e)
            errors.append(full_entity_id)

    # ── Number helper — heating rate ──────────────────────────────
    heating_rate_id = f"sha_{room_id}_heating_rate"
    heating_rate_entity = f"input_number.{heating_rate_id}"

    if hass.states.get(heating_rate_entity) is None:
        try:
            await hass.services.async_call(
                "input_number",
                "create",
                {
                    "name": f"SHA {room_name} Heating Rate",
                    "min": 0.05,
                    "max": 0.30,
                    "step": 0.01,
                    "initial": 0.15,
                    "unit_of_measurement": "°C/min",
                    "icon": "mdi:thermometer-auto",
                    "mode": "box",
                },
                blocking=True,
            )
            created[heating_rate_id] = heating_rate_entity
            _LOGGER.debug("Created number helper: %s", heating_rate_entity)
        except Exception as e:
            _LOGGER.error("Failed to create number helper %s: %s", heating_rate_entity, e)
            errors.append(heating_rate_entity)
    else:
        _LOGGER.debug("Helper %s already exists — skipping", heating_rate_entity)
        created[heating_rate_id] = heating_rate_entity

    # ── Timer helper — override ───────────────────────────────────
    override_timer_id = f"sha_{room_id}_override"
    override_timer_entity = f"timer.{override_timer_id}"

    if hass.states.get(override_timer_entity) is None:
        try:
            await hass.services.async_call(
                "timer",
                "create",
                {
                    "name": f"SHA {room_name} Override",
                    "icon": "mdi:hand-back-right",
                },
                blocking=True,
            )
            created[override_timer_id] = override_timer_entity
            _LOGGER.debug("Created timer helper: %s", override_timer_entity)
        except Exception as e:
            _LOGGER.error("Failed to create timer helper %s: %s", override_timer_entity, e)
            errors.append(override_timer_entity)
    else:
        _LOGGER.debug("Helper %s already exists — skipping", override_timer_entity)
        created[override_timer_id] = override_timer_entity

    if errors:
        _LOGGER.warning(
            "Some helpers could not be created for room %s: %s",
            room_name,
            errors
        )
    else:
        _LOGGER.info(
            "All SHA helpers created successfully for room: %s",
            room_name
        )

    return created


class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle step 1 — Ollama configuration."""
        errors = {}

        if user_input is not None:
            ollama_ok = await _test_ollama(
                user_input[CONF_OLLAMA_URL],
                user_input[CONF_OLLAMA_MODEL]
            )
            if not ollama_ok:
                errors["base"] = "ollama_connection_failed"
            else:
                self._ollama_data = user_input
                return await self.async_step_influxdb()

        schema = vol.Schema({
            vol.Required(CONF_OLLAMA_URL, default=DEFAULT_OLLAMA_URL): str,
            vol.Required(CONF_OLLAMA_MODEL, default=DEFAULT_OLLAMA_MODEL): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "step": "Step 1 of 3 — Ollama Configuration"
            }
        )

    async def async_step_influxdb(self, user_input=None) -> FlowResult:
        """Handle step 2 — InfluxDB configuration."""
        errors = {}

        if user_input is not None:
            influx_ok = await _test_influxdb(
                user_input[CONF_INFLUXDB_URL],
                user_input[CONF_INFLUXDB_TOKEN],
                user_input[CONF_INFLUXDB_ORG],
                user_input[CONF_INFLUXDB_BUCKET],
            )
            if not influx_ok:
                errors["base"] = "influxdb_connection_failed"
            else:
                self._influxdb_data = user_input
                return await self.async_step_entities()

        schema = vol.Schema({
            vol.Required(CONF_INFLUXDB_URL, default=DEFAULT_INFLUXDB_URL): str,
            vol.Required(CONF_INFLUXDB_TOKEN): str,
            vol.Required(CONF_INFLUXDB_ORG, default=DEFAULT_INFLUXDB_ORG): str,
            vol.Required(CONF_INFLUXDB_BUCKET, default=DEFAULT_INFLUXDB_BUCKET): str,
        })

        return self.async_show_form(
            step_id="influxdb",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "step": "Step 2 of 3 — InfluxDB Configuration"
            }
        )

    async def async_step_entities(self, user_input=None) -> FlowResult:
        """Handle step 3 — HA entity configuration."""
        errors = {}

        if user_input is not None:
            # Validate entities exist in HA
            temp_sensor = self.hass.states.get(user_input[CONF_TEMP_SENSOR])
            weather = self.hass.states.get(user_input[CONF_WEATHER_ENTITY])

            if not temp_sensor:
                errors[CONF_TEMP_SENSOR] = "entity_not_found"
            elif not weather:
                errors[CONF_WEATHER_ENTITY] = "entity_not_found"
            else:
                # Combine all config data
                all_data = {
                    **self._ollama_data,
                    **self._influxdb_data,
                    **user_input,
                }

                # Derive room name from temp sensor for helper creation
                # e.g. sensor.bathroom_thermostat_temperature → Bathroom
                room_name = (
                    user_input[CONF_TEMP_SENSOR]
                    .replace("sensor.", "")
                    .replace("_thermostat_temperature", "")
                    .replace("_temperature", "")
                    .replace("_", " ")
                    .title()
                )

                # Store room name in config
                all_data["room_name"] = room_name

                # Create all SHA helpers for this room
                await async_create_room_helpers(self.hass, room_name)

                # Update heating rate helper reference
                room_id = _room_name_to_id(room_name)
                all_data[CONF_HEATING_RATE_HELPER] = (
                    f"input_number.sha_{room_id}_heating_rate"
                )

                return self.async_create_entry(
                    title=f"Smart Heating Advisor — {room_name}",
                    data=all_data,
                )

        schema = vol.Schema({
            vol.Required(
                CONF_TEMP_SENSOR,
                default="sensor.bathroom_thermostat_temperature"
            ): str,
            vol.Required(
                CONF_WEATHER_ENTITY,
                default="weather.forecast_home"
            ): str,
            vol.Required(
                CONF_TARGET_TEMP,
                default=DEFAULT_TARGET_TEMP
            ): int,
            vol.Required(
                CONF_TARGET_TIME,
                default=DEFAULT_TARGET_TIME
            ): str,
        })

        return self.async_show_form(
            step_id="entities",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "step": "Step 3 of 3 — Home Assistant Entities"
            }
        )