"""SHA switch entities — boolean state helpers and override switch."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Discover rooms and create all boolean switch and override switch entities."""
    from .coordinator import SmartHeatingCoordinator

    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    boolean_defs = [
        ("airing_mode",       "Airing Mode",       "mdi:window-open"),
        ("preheat_notified",  "Preheat Notified",  "mdi:bell"),
        ("target_notified",   "Target Notified",   "mdi:check-circle"),
        ("standby_notified",  "Standby Notified",  "mdi:sleep"),
        ("vacation_notified", "Vacation Notified", "mdi:beach"),
    ]

    async def _create_entities(_event=None) -> None:
        _LOGGER.debug("switch platform: starting entity setup")
        rooms = coordinator.discover_rooms()
        _LOGGER.info("switch platform: discovered %d room(s): %s", len(rooms), [r.room_name for r in rooms])

        entities: list = []
        for room in rooms:
            for purpose, label, icon in boolean_defs:
                e = SHABooleanSwitch(room.room_name, room.room_id, entry.entry_id, purpose, label, icon)
                entities.append(e)
                _LOGGER.debug("switch platform: created %s for room '%s'", e.entity_id, room.room_name)
            override = SHAOverrideSwitch(room.room_name, room.room_id, entry.entry_id)
            entities.append(override)
            coordinator._override_switches[room.room_id] = override
            _LOGGER.debug("switch platform: created %s for room '%s'", override.entity_id, room.room_name)

        async_add_entities(entities)
        _LOGGER.info("switch platform: registered %d switch entity(ies)", len(entities))

    if hass.state == CoreState.running:
        await _create_entities()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _create_entities)


class SHABooleanSwitch(SwitchEntity, RestoreEntity):
    """Persistent on/off helper — notification flags and airing-mode tracking."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        room_name: str,
        room_id: str,
        entry_id: str,
        purpose: str,
        purpose_label: str,
        icon: str,
    ) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._purpose = purpose
        self._is_on = False

        self._attr_name = purpose_label
        self._attr_unique_id = f"sha_{room_id}_{purpose}"
        self._attr_icon = icon

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

    async def async_turn_on(self, **kwargs) -> None:
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._is_on = last.state == "on"
            _LOGGER.debug("%s: restored state → %s", self.entity_id, last.state)


class SHAOverrideSwitch(SwitchEntity, RestoreEntity):
    """Override-active switch — turns on for a timed duration, then auto-turns off.

    Use ``async_start(duration_seconds)`` to begin a timed override.
    Fires ``sha_override_ended`` event when the timer expires.
    ``switch.turn_on`` / ``switch.turn_off`` work as manual on/off without duration.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, room_name: str, room_id: str, entry_id: str) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._is_on = False
        self._cancel_timer = None

        self._attr_name = "Override"
        self._attr_unique_id = f"sha_{room_id}_override"
        self._attr_icon = "mdi:hand-back-right"

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

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on indefinitely (no auto-expiry)."""
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        self._is_on = False
        self.async_write_ha_state()

    async def async_start(self, duration_seconds: int) -> None:
        """Start override for ``duration_seconds``, then auto-expire."""
        if self._cancel_timer:
            self._cancel_timer()
        self._is_on = True
        self.async_write_ha_state()
        self._cancel_timer = async_call_later(
            self.hass, duration_seconds, self._async_expired
        )
        _LOGGER.debug(
            "[%s] Override started — duration %d min (%d s)",
            self._room_name, duration_seconds // 60, duration_seconds,
        )

    async def _async_expired(self, _now) -> None:
        self._cancel_timer = None
        self._is_on = False
        self.async_write_ha_state()
        self.hass.bus.async_fire(
            "sha_override_ended",
            {"entity_id": self.entity_id, "room_id": self._room_id},
        )
        _LOGGER.debug("[%s] Override expired — firing sha_override_ended", self._room_name)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Override does not resume after HA restart — heating resumes immediately.
        self._is_on = False

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
