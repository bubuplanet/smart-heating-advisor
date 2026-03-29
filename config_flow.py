"""Config flow for Smart Heating Advisor."""
import logging
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


async def _test_ollama(url: str, model: str) -> bool:
    """Test Ollama connection."""
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
                # Store and move to step 2
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
            heating_helper = self.hass.states.get(user_input[CONF_HEATING_RATE_HELPER])
            weather = self.hass.states.get(user_input[CONF_WEATHER_ENTITY])

            if not temp_sensor:
                errors[CONF_TEMP_SENSOR] = "entity_not_found"
            elif not heating_helper:
                errors[CONF_HEATING_RATE_HELPER] = "entity_not_found"
            elif not weather:
                errors[CONF_WEATHER_ENTITY] = "entity_not_found"
            else:
                # All good — create entry
                all_data = {
                    **self._ollama_data,
                    **self._influxdb_data,
                    **user_input,
                }
                return self.async_create_entry(
                    title="Smart Heating Advisor",
                    data=all_data,
                )

        schema = vol.Schema({
            vol.Required(
                CONF_TEMP_SENSOR,
                default="sensor.bathroom_thermostat_temperature"
            ): str,
            vol.Required(
                CONF_HEATING_RATE_HELPER,
                default="input_number.bathroom_heating_rate"
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