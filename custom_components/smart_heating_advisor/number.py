"""SHA number entities — per-room heating rate."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, DEFAULT_HEATING_RATE, MIN_HEATING_RATE, MAX_HEATING_RATE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover rooms and create all heating rate number entities."""
    from .coordinator import SmartHeatingCoordinator

    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    _LOGGER.debug("number platform: starting entity setup")
    rooms = coordinator.discover_rooms()
    _LOGGER.debug("number platform: discovered %d room(s): %s", len(rooms), [r.room_name for r in rooms])

    entities: list[SHAHeatingRateNumber] = []
    for room in rooms:
        entity = SHAHeatingRateNumber(room.room_name, room.room_id, entry.entry_id)
        entities.append(entity)
        coordinator.register_heating_rate_entity(room.room_id, entity)
        _LOGGER.debug("number platform: created %s for room '%s'", entity.entity_id, room.room_name)

    async_add_entities(entities)
    _LOGGER.debug("number platform: registered %d heating rate entity(ies)", len(entities))


class SHAHeatingRateNumber(NumberEntity, RestoreEntity):
    """Per-room heating rate — writable, restored across restarts.

    The AI daily analysis updates this value automatically; users can also
    adjust it manually from the UI.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_native_min_value = MIN_HEATING_RATE
    _attr_native_max_value = MAX_HEATING_RATE
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "°C/min"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-auto"

    def __init__(self, room_name: str, room_id: str, entry_id: str) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._value = DEFAULT_HEATING_RATE

        self._attr_name = "Heating Rate"
        self._attr_unique_id = f"sha_{room_id}_heating_rate"
        self.entity_id = f"number.sha_{room_id}_heating_rate"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            "name": f"SHA — {self._room_name}",
            "manufacturer": "Smart Heating Advisor",
        }

    @property
    def native_value(self) -> float:
        return round(self._value, 3)

    async def async_set_native_value(self, value: float) -> None:
        old = self._value
        self._value = round(value, 3)
        _LOGGER.debug(
            "[%s] Heating rate updated: %.3f → %.3f °C/min",
            self._room_name, old, self._value,
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                val = float(last.state)
                if MIN_HEATING_RATE <= val <= MAX_HEATING_RATE:
                    self._value = val
                    _LOGGER.debug(
                        "[%s] Heating rate restored: %.3f °C/min",
                        self._room_name, self._value,
                    )
                else:
                    _LOGGER.debug(
                        "[%s] Restored value %.3f out of range [%.3f, %.3f] — using default",
                        self._room_name, val, MIN_HEATING_RATE, MAX_HEATING_RATE,
                    )
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "[%s] Could not restore heating rate from state '%s' — using default",
                    self._room_name, last.state,
                )
        else:
            _LOGGER.debug("[%s] No previous state found — using default %.3f °C/min", self._room_name, self._value)
