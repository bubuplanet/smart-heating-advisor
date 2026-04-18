"""SHA number entities — per-room heating rate."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    DEFAULT_HEATING_RATE, MIN_HEATING_RATE, MAX_HEATING_RATE,
    DEFAULT_TRV_SETPOINT, MIN_TRV_SETPOINT, MAX_TRV_SETPOINT,
    DEFAULT_DEFAULT_TEMP, MIN_DEFAULT_TEMP, MAX_DEFAULT_TEMP,
)
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Discover rooms and create all heating rate number entities."""
    from .coordinator import SmartHeatingCoordinator

    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _create_entities(_event=None) -> None:
        _LOGGER.debug(
            "number platform: starting entity setup (hass_state=%s, event=%s)",
            hass.state,
            type(_event).__name__ if _event is not None else "direct",
        )
        rooms = coordinator.discover_rooms()
        _LOGGER.info("number platform: discovered %d room(s): %s", len(rooms), [r.room_name for r in rooms] if _LOGGER.isEnabledFor(logging.INFO) else "")

        room_id_to_subentry: dict[str, str] = {
            _room_name_to_id(s.data["room_name"]): s.subentry_id
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

        entities: list = []
        for room in rooms:
            rate_entity = SHAHeatingRateNumber(room.room_name, room.room_id, entry.entry_id, room_id_to_subentry.get(room.room_id))
            entities.append(rate_entity)
            coordinator.register_heating_rate_entity(room.room_id, rate_entity)
            _LOGGER.debug(
                "number platform: prepared entity unique_id=%s expected_entity_id=number.sha_%s_heating_rate room='%s'",
                rate_entity.unique_id,
                room.room_id,
                room.room_name,
            )

            setpoint_entity = SHATRVSetpointNumber(room.room_name, room.room_id, entry.entry_id, room_id_to_subentry.get(room.room_id))
            entities.append(setpoint_entity)
            coordinator.register_trv_setpoint_entity(room.room_id, setpoint_entity)
            _LOGGER.debug(
                "number platform: prepared entity unique_id=%s expected_entity_id=number.sha_%s_trv_setpoint room='%s'",
                setpoint_entity.unique_id,
                room.room_id,
                room.room_name,
            )

            default_temp_entity = SHADefaultTempNumber(room.room_name, room.room_id, entry.entry_id, room_id_to_subentry.get(room.room_id))
            entities.append(default_temp_entity)
            _LOGGER.debug(
                "number platform: prepared entity unique_id=%s expected_entity_id=number.sha_%s_default_temp room='%s'",
                default_temp_entity.unique_id,
                room.room_id,
                room.room_name,
            )

        async_add_entities(entities)
        _LOGGER.info("number platform: registered %d entities (%d rooms)", len(entities), len(rooms))

    if hass.state == CoreState.running:
        await _create_entities()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _create_entities)


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

    def __init__(self, room_name: str, room_id: str, entry_id: str, subentry_id: str | None = None) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._value = DEFAULT_HEATING_RATE

        self._attr_name = "Heating Rate"
        self._attr_unique_id = f"sha_{room_id}_heating_rate"

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
        if self._subentry_id:
            er.async_get(self.hass).async_update_entity(
                self.entity_id, config_subentry_id=self._subentry_id
            )


class SHATRVSetpointNumber(NumberEntity, RestoreEntity):
    """Per-room TRV setpoint — the temperature SHA commands the TRV to reach.

    SHA daily analysis calculates this from the observed gradient between the
    TRV setpoint and the comfort sensor reading at schedule ON time.  The value
    is restored across HA restarts.  Users can also adjust it manually.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_native_min_value = MIN_TRV_SETPOINT
    _attr_native_max_value = MAX_TRV_SETPOINT
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "°C"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-chevron-up"

    def __init__(self, room_name: str, room_id: str, entry_id: str, subentry_id: str | None = None) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._value = DEFAULT_TRV_SETPOINT

        self._attr_name = "TRV Setpoint"
        self._attr_unique_id = f"sha_{room_id}_trv_setpoint"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            "name": f"SHA — {self._room_name}",
            "manufacturer": "Smart Heating Advisor",
        }

    @property
    def native_value(self) -> float:
        return round(self._value, 1)

    async def async_set_native_value(self, value: float) -> None:
        old = self._value
        self._value = round(value, 1)
        _LOGGER.debug(
            "[%s] TRV setpoint updated: %.1f → %.1f °C",
            self._room_name, old, self._value,
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                val = float(last.state)
                if MIN_TRV_SETPOINT <= val <= MAX_TRV_SETPOINT:
                    self._value = val
                    _LOGGER.debug(
                        "[%s] TRV setpoint restored: %.1f °C",
                        self._room_name, self._value,
                    )
                else:
                    _LOGGER.debug(
                        "[%s] Restored TRV setpoint %.1f out of range [%.1f, %.1f] — using default",
                        self._room_name, val, MIN_TRV_SETPOINT, MAX_TRV_SETPOINT,
                    )
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "[%s] Could not restore TRV setpoint from state '%s' — using default",
                    self._room_name, last.state,
                )
        else:
            _LOGGER.debug("[%s] No previous TRV setpoint state — using default %.1f °C", self._room_name, self._value)
        if self._subentry_id:
            er.async_get(self.hass).async_update_entity(
                self.entity_id, config_subentry_id=self._subentry_id
            )


class SHADefaultTempNumber(NumberEntity, RestoreEntity):
    """Per-room default (standby) temperature — user-adjustable, restored across restarts.

    Set by user from the HA UI. Read by blueprint (Phase 5) and by analyzer
    for ambient accuracy checks (Phase 6). Never updated by AI.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_native_min_value = MIN_DEFAULT_TEMP
    _attr_native_max_value = MAX_DEFAULT_TEMP
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "°C"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer"

    def __init__(self, room_name: str, room_id: str, entry_id: str, subentry_id: str | None = None) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._value = DEFAULT_DEFAULT_TEMP

        self._attr_name = "Default Temperature"
        self._attr_unique_id = f"sha_{room_id}_default_temp"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            "name": f"SHA — {self._room_name}",
            "manufacturer": "Smart Heating Advisor",
        }

    @property
    def native_value(self) -> float:
        return round(self._value, 1)

    async def async_set_native_value(self, value: float) -> None:
        old = self._value
        self._value = round(value, 1)
        _LOGGER.debug(
            "[%s] Default temperature updated: %.1f → %.1f °C",
            self._room_name, old, self._value,
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                val = float(last.state)
                if MIN_DEFAULT_TEMP <= val <= MAX_DEFAULT_TEMP:
                    self._value = val
                    _LOGGER.debug(
                        "[%s] Default temperature restored: %.1f °C",
                        self._room_name, self._value,
                    )
                else:
                    _LOGGER.debug(
                        "[%s] Restored default temp %.1f out of range [%.1f, %.1f] — using default",
                        self._room_name, val, MIN_DEFAULT_TEMP, MAX_DEFAULT_TEMP,
                    )
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "[%s] Could not restore default temp from state '%s' — using default",
                    self._room_name, last.state,
                )
        else:
            _LOGGER.debug("[%s] No previous default temp state — using default %.1f °C", self._room_name, self._value)
        registry = er.async_get(self.hass)
        expected_entity_id = f"number.sha_{self._room_id}_default_temp"
        current_entity_id = self.entity_id
        if current_entity_id != expected_entity_id:
            _LOGGER.warning(
                "SHA number entity_id mismatch — renaming %s → %s",
                current_entity_id, expected_entity_id,
            )
            registry.async_update_entity(current_entity_id, new_entity_id=expected_entity_id)
            current_entity_id = expected_entity_id
        if self._subentry_id:
            registry.async_update_entity(
                current_entity_id, config_subentry_id=self._subentry_id
            )
