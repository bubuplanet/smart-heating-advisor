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
    """Sensor platform — all sensor entities removed in Phase 1 cleanup."""
    _LOGGER.debug("sensor platform: no sensor entities configured")
