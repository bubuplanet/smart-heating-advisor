"""Config flow for Smart Heating Advisor."""
import logging
import re
import voluptuous as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

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
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_INFLUXDB_URL,
    DEFAULT_INFLUXDB_ORG,
    DEFAULT_INFLUXDB_BUCKET,
)

_LOGGER = logging.getLogger(__name__)


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


class SmartHeatingAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Smart Heating Advisor.

    3 steps:
      1. Ollama — URL + model, connection tested
      2. InfluxDB — URL, token, org, bucket, connection tested
      3. HA entities — weather entity validated
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "OptionsFlowHandler":
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Step 1 — Ollama configuration."""
        errors = {}

        if user_input is not None:
            if await _test_ollama(
                user_input[CONF_OLLAMA_URL], user_input[CONF_OLLAMA_MODEL]
            ):
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
                return await self.async_step_entities()
            errors["base"] = "influxdb_connection_failed"

        return self.async_show_form(
            step_id="influxdb",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INFLUXDB_URL, default=DEFAULT_INFLUXDB_URL): str,
                    vol.Required(CONF_INFLUXDB_TOKEN): str,
                    vol.Required(CONF_INFLUXDB_ORG, default=DEFAULT_INFLUXDB_ORG): str,
                    vol.Required(
                        CONF_INFLUXDB_BUCKET, default=DEFAULT_INFLUXDB_BUCKET
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_entities(self, user_input=None) -> ConfigFlowResult:
        """Step 3 — HA entity configuration."""
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
                    },
                )

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WEATHER_ENTITY, default="weather.forecast_home"
                    ): str,
                }
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle SHA options — currently just the debug logging toggle."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_DEBUG_LOGGING, False)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEBUG_LOGGING, default=current): bool,
                }
            ),
        )
