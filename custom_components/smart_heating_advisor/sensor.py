"""Sensor platform for Smart Heating Advisor — placeholder (no entities)."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Sensor platform — reserved for future use. No sensor entities are created currently. The platform setup is kept as a placeholder for prediction sensors planned in a future release."""
    _LOGGER.debug("sensor platform: no sensor entities configured")
