"""SHA switch entities — airing mode and manual override."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import _room_name_to_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Discover rooms and create airing mode and override switch entities."""
    from .coordinator import SmartHeatingCoordinator

    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    async def _create_entities(_event=None) -> None:
        _LOGGER.debug(
            "switch platform: starting entity setup (hass_state=%s, event=%s)",
            hass.state,
            type(_event).__name__ if _event is not None else "direct",
        )
        rooms = coordinator.discover_rooms()
        _LOGGER.info("switch platform: discovered %d room(s): %s", len(rooms), [r.room_name for r in rooms] if _LOGGER.isEnabledFor(logging.INFO) else "")

        room_id_to_subentry: dict[str, str] = {
            _room_name_to_id(s.data["room_name"]): s.subentry_id
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }

        entities: list = []
        for room in rooms:
            subentry_id = room_id_to_subentry.get(room.room_id)

            airing = SHABooleanSwitch(
                room.room_name, room.room_id, entry.entry_id,
                "airing_mode", "Airing Mode", "mdi:window-open",
                default_on=False, subentry_id=subentry_id,
            )
            entities.append(airing)
            _LOGGER.debug(
                "switch platform: prepared entity unique_id=%s expected_entity_id=switch.sha_%s_airing_mode room='%s'",
                airing.unique_id, room.room_id, room.room_name,
            )

            override = SHAOverrideSwitch(room.room_name, room.room_id, entry.entry_id, subentry_id=subentry_id)
            entities.append(override)
            coordinator._override_switches[room.room_id] = override
            _LOGGER.debug(
                "switch platform: prepared entity unique_id=%s expected_entity_id=switch.sha_%s_override room='%s'",
                override.unique_id, room.room_id, room.room_name,
            )

        async_add_entities(entities)
        _LOGGER.info("switch platform: registered %d switch entity(ies)", len(entities))

    if hass.state == CoreState.running:
        await _create_entities()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _create_entities)


class SHABooleanSwitch(SwitchEntity, RestoreEntity):
    """Persistent on/off helper — airing-mode tracking."""

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
        default_on: bool = False,
        subentry_id: str | None = None,
    ) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._purpose = purpose
        self._is_on = default_on
        self._default_on = default_on
        self._subentry_id = subentry_id

        self._attr_name = purpose_label
        self._attr_unique_id = f"sha_{room_id}_{purpose}"
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        if self._subentry_id:
            return DeviceInfo(
                identifiers={(DOMAIN, self._subentry_id)},
                name=f"SHA — {self._room_name}",
                manufacturer="Smart Heating Advisor",
                model="Room",
                via_device=(DOMAIN, self._entry_id),
            )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            name=f"SHA — {self._room_name}",
            manufacturer="Smart Heating Advisor",
        )

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "meaning": "When on, heating is paused because a window/door is considered open.",
            "manual_use": "Advanced use only. Normally managed by the blueprint window logic.",
        }

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
        else:
            _LOGGER.debug("%s: no previous state, using default=%s", self.entity_id, self._default_on)
        registry = er.async_get(self.hass)
        expected_entity_id = f"switch.sha_{self._room_id}_{self._purpose}"
        current_entity_id = self.entity_id
        if current_entity_id != expected_entity_id:
            _LOGGER.warning(
                "SHA switch entity_id mismatch — renaming %s → %s",
                current_entity_id, expected_entity_id,
            )
            registry.async_update_entity(current_entity_id, new_entity_id=expected_entity_id)
            current_entity_id = expected_entity_id
        if self._subentry_id:
            registry.async_update_entity(
                current_entity_id, config_subentry_id=self._subentry_id
            )


class SHAOverrideSwitch(SwitchEntity, RestoreEntity):
    """Override-active switch — turns on for a timed duration, then auto-turns off.

    Use ``async_start(duration_seconds)`` to begin a timed override.
    Fires ``sha_override_ended`` event when the timer expires.
    ``switch.turn_on`` / ``switch.turn_off`` work as manual on/off without duration.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, room_name: str, room_id: str, entry_id: str, subentry_id: str | None = None) -> None:
        self._room_name = room_name
        self._room_id = room_id
        self._entry_id = entry_id
        self._is_on = False
        self._cancel_timer = None
        self._subentry_id = subentry_id

        self._attr_name = "Manual Override (Pause Automation)"
        self._attr_unique_id = f"sha_{room_id}_override"
        self._attr_icon = "mdi:hand-back-right"

    @property
    def device_info(self) -> DeviceInfo:
        if self._subentry_id:
            return DeviceInfo(
                identifiers={(DOMAIN, self._subentry_id)},
                name=f"SHA — {self._room_name}",
                manufacturer="Smart Heating Advisor",
                model="Room",
                via_device=(DOMAIN, self._entry_id),
            )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._room_id}")},
            name=f"SHA — {self._room_name}",
            manufacturer="Smart Heating Advisor",
        )

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "meaning": "When on, automatic heating control is paused for this room.",
            "manual_use": "Turning on manually keeps override active until turned off. Timed overrides should use the blueprint/service.",
        }

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
        registry = er.async_get(self.hass)
        expected_entity_id = f"switch.sha_{self._room_id}_override"
        current_entity_id = self.entity_id
        if current_entity_id != expected_entity_id:
            _LOGGER.warning(
                "SHA switch entity_id mismatch — renaming %s → %s",
                current_entity_id, expected_entity_id,
            )
            registry.async_update_entity(current_entity_id, new_entity_id=expected_entity_id)
            current_entity_id = expected_entity_id
        if self._subentry_id:
            registry.async_update_entity(
                current_entity_id, config_subentry_id=self._subentry_id
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
