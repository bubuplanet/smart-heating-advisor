"""Sensor platform for Smart Heating Advisor."""
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartHeatingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Heating Advisor sensors.

    Sensors are created dynamically per discovered room.
    On first setup rooms may not yet exist — they are discovered
    at analysis time. We create a global set of sensors for the
    integration and update them when rooms are analysed.
    """
    coordinator: SmartHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Discover rooms now to create initial sensor set
    rooms = coordinator.discover_rooms()

    entities = []
    for room in rooms:
        entities.extend([
            RoomHeatingRateSensor(coordinator, entry, room.room_id, room.room_name),
            RoomLastAnalysisSensor(coordinator, entry, room.room_id, room.room_name),
            RoomConfidenceSensor(coordinator, entry, room.room_id, room.room_name),
            RoomWeeklyReportSensor(coordinator, entry, room.room_id, room.room_name),
        ])

    if not entities:
        _LOGGER.info(
            "No SHA rooms discovered yet — sensors will be created "
            "once blueprint automations are set up."
        )

    async_add_entities(entities)
    coordinator.register_entities(entities)


class SHABaseSensor(SensorEntity):
    """Base class for SHA sensors."""

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        room_id: str,
        room_name: str,
    ):
        self.coordinator = coordinator
        self.entry = entry
        self.room_id = room_id
        self.room_name = room_name
        self._attr_should_poll = False

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.entry.entry_id}_{self.room_id}")},
            "name": f"SHA — {self.room_name}",
            "manufacturer": "Smart Heating Advisor",
            "model": "AI Heating Optimizer",
            "sw_version": "0.0.1",
        }

    def _room_state(self) -> dict:
        return self.coordinator.room_states.get(self.room_id, {})


class RoomHeatingRateSensor(SHABaseSensor):
    """Current AI-calibrated heating rate for this room."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{self.room_id}_heating_rate"

    @property
    def name(self) -> str:
        return f"SHA {self.room_name} Heating Rate"

    @property
    def state(self) -> float:
        rate = self._room_state().get("heating_rate", 0.15)
        return round(float(rate), 3)

    @property
    def unit_of_measurement(self) -> str:
        return "°C/min"

    @property
    def icon(self) -> str:
        return "mdi:thermometer-auto"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "min_rate": 0.05,
            "max_rate": 0.30,
            "last_updated": self._room_state().get("last_analysis"),
            "room_id": self.room_id,
        }


class RoomLastAnalysisSensor(SHABaseSensor):
    """Timestamp of last AI analysis for this room."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{self.room_id}_last_analysis"

    @property
    def name(self) -> str:
        return f"SHA {self.room_name} Last Analysis"

    @property
    def state(self) -> str | None:
        return self._room_state().get("last_analysis")

    @property
    def icon(self) -> str:
        return "mdi:clock-check"

    @property
    def device_class(self) -> str:
        return "timestamp"


class RoomConfidenceSensor(SHABaseSensor):
    """AI confidence level for this room's heating rate."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{self.room_id}_confidence"

    @property
    def name(self) -> str:
        return f"SHA {self.room_name} Confidence"

    @property
    def state(self) -> str:
        return self._room_state().get("confidence", "unknown")

    @property
    def icon(self) -> str:
        confidence = self._room_state().get("confidence", "unknown")
        if confidence == "high":
            return "mdi:check-circle"
        elif confidence == "medium":
            return "mdi:alert-circle"
        return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict:
        return {"possible_values": ["high", "medium", "low", "unknown"]}


class RoomWeeklyReportSensor(SHABaseSensor):
    """Last weekly AI report for this room."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{self.room_id}_weekly_report"

    @property
    def name(self) -> str:
        return f"SHA {self.room_name} Weekly Report"

    @property
    def state(self) -> str:
        report = self._room_state().get("weekly_report", "No report yet.")
        if len(report) > 255:
            return report[:252] + "..."
        return report

    @property
    def icon(self) -> str:
        return "mdi:chart-line"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "full_report": self._room_state().get("weekly_report", "No report yet."),
            "last_analysis": self._room_state().get("last_analysis"),
            "confidence": self._room_state().get("confidence", "unknown"),
        }
