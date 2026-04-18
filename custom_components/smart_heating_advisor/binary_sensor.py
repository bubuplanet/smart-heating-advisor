"""SHA binary sensor entities — window open and vacation."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Discover rooms and create binary sensor entities."""
    from .coordinator import SmartHeatingCoordinator

    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _create_entities(_event=None) -> None:
        _LOGGER.debug(
            "binary_sensor platform: starting entity setup (hass_state=%s, event=%s)",
            hass.state,
            type(_event).__name__ if _event is not None else "direct",
        )
        rooms = coordinator.discover_rooms()
        _LOGGER.info("binary_sensor platform: discovered %d room(s): %s", len(rooms), [r.room_name for r in rooms] if _LOGGER.isEnabledFor(logging.INFO) else "")

        room_id_to_subentry: dict[str, str] = {
            _room_name_to_id(s.data["room_name"]): s.subentry_id
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

        room_id_to_window_sensors: dict[str, list[str]] = {
            _room_name_to_id(s.data["room_name"]): list(s.data.get("window_sensors", []))
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

        entities: list = []
        for room in rooms:
            subentry_id = room_id_to_subentry.get(room.room_id)
            window_sensors = room_id_to_window_sensors.get(room.room_id, [])

            initial_window_open = any(
                (s := hass.states.get(sid)) is not None and s.state in ("on", "open")
                for sid in window_sensors
            ) if window_sensors else False

            window_entity = SHAWindowOpenBinarySensor(
                room.room_name, room.room_id, entry.entry_id,
                subentry_id=subentry_id,
                initial_state=initial_window_open,
            )
            entities.append(window_entity)
            coordinator.register_window_open_entity(room.room_id, window_entity)
            _LOGGER.debug(
                "binary_sensor platform: prepared entity unique_id=%s expected_entity_id=binary_sensor.sha_%s_window_open room='%s'",
                window_entity.unique_id, room.room_id, room.room_name,
            )

            vacation_entity = SHAVacationBinarySensor(
                room.room_name, room.room_id, entry.entry_id,
                subentry_id=subentry_id,
            )
            entities.append(vacation_entity)
            coordinator.register_vacation_entity(room.room_id, vacation_entity)
            _LOGGER.debug(
                "binary_sensor platform: prepared entity unique_id=%s expected_entity_id=binary_sensor.sha_%s_vacation room='%s'",
                vacation_entity.unique_id, room.room_id, room.room_name,
            )

        async_add_entities(entities)
        _LOGGER.info("binary_sensor platform: registered %d entities (%d rooms)", len(entities), len(rooms))

    if hass.state == CoreState.running:
        await _create_entities()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _create_entities)


class SHAWindowOpenBinarySensor(BinarySensorEntity):
    """SHA computed window-open state for a room.

    Updated by coordinator when any configured window sensor changes state (Phase 4).
    Read by blueprint instead of window sensor templates (Phase 5).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_icon = "mdi:window-open-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        room_name: str,
        room_id: str,
        entry_id: str,
        subentry_id: str | None = None,
        initial_state: bool = False,
    ) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._is_on = initial_state

        self._attr_name = "Window Open"
        self._attr_unique_id = f"sha_{room_id}_window_open"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            "name": f"SHA — {self._room_name}",
            "manufacturer": "Smart Heating Advisor",
        }

    @property
    def is_on(self) -> bool:
        return self._is_on

    def set_window_open(self, is_open: bool) -> None:
        """Update the window open state and push to HA."""
        if self._is_on != is_open:
            self._is_on = is_open
            if self.hass is not None:
                self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        expected_entity_id = f"binary_sensor.sha_{self._room_id}_window_open"
        current_entity_id = self.entity_id
        if current_entity_id != expected_entity_id:
            _LOGGER.warning(
                "SHA binary_sensor entity_id mismatch — renaming %s → %s",
                current_entity_id, expected_entity_id,
            )
            registry.async_update_entity(current_entity_id, new_entity_id=expected_entity_id)
            current_entity_id = expected_entity_id
        if self._subentry_id:
            registry.async_update_entity(
                current_entity_id, config_subentry_id=self._subentry_id
            )


class SHAVacationBinarySensor(BinarySensorEntity):
    """SHA computed vacation state.

    Updated by coordinator when vacation state changes (Phase 4).
    Read by blueprint instead of person entity templates (Phase 5).
    Defaults to False until global vacation config is added (Phase 3).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_icon = "mdi:airplane"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        room_name: str,
        room_id: str,
        entry_id: str,
        subentry_id: str | None = None,
    ) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._is_on = False

        self._attr_name = "Vacation"
        self._attr_unique_id = f"sha_{room_id}_vacation"

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            "name": f"SHA — {self._room_name}",
            "manufacturer": "Smart Heating Advisor",
        }

    @property
    def is_on(self) -> bool:
        return self._is_on

    def set_vacation(self, on_vacation: bool) -> None:
        """Update the vacation state and push to HA."""
        if self._is_on != on_vacation:
            self._is_on = on_vacation
            if self.hass is not None:
                self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        expected_entity_id = f"binary_sensor.sha_{self._room_id}_vacation"
        current_entity_id = self.entity_id
        if current_entity_id != expected_entity_id:
            _LOGGER.warning(
                "SHA binary_sensor entity_id mismatch — renaming %s → %s",
                current_entity_id, expected_entity_id,
            )
            registry.async_update_entity(current_entity_id, new_entity_id=expected_entity_id)
            current_entity_id = expected_entity_id
        if self._subentry_id:
            registry.async_update_entity(
                current_entity_id, config_subentry_id=self._subentry_id
            )
