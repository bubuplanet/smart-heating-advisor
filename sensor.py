"""Sensor platform for Smart Heating Advisor."""
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SENSOR_HEATING_RATE,
    SENSOR_LAST_ANALYSIS,
    SENSOR_CONFIDENCE,
    SENSOR_WEEKLY_REPORT,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Heating Advisor sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        HeatingRateSensor(coordinator, entry),
        LastAnalysisSensor(coordinator, entry),
        ConfidenceSensor(coordinator, entry),
        WeeklyReportSensor(coordinator, entry),
    ]

    async_add_entities(entities)

    # Register entities with coordinator for updates
    coordinator.register_entities(entities)


class SmartHeatingBaseSensor(SensorEntity):
    """Base class for Smart Heating Advisor sensors."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize base sensor."""
        self.coordinator = coordinator
        self.entry = entry
        self._attr_should_poll = False

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Smart Heating Advisor",
            "manufacturer": "Custom",
            "model": "AI Heating Optimizer",
            "sw_version": "1.0.0",
        }


class HeatingRateSensor(SmartHeatingBaseSensor):
    """Sensor showing current AI-calculated heating rate."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{SENSOR_HEATING_RATE}"

    @property
    def name(self) -> str:
        return "SHA Bathroom Heating Rate"

    @property
    def state(self) -> float:
        return round(self.coordinator.heating_rate, 3)

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
            "last_updated": self.coordinator.last_analysis,
        }


class LastAnalysisSensor(SmartHeatingBaseSensor):
    """Sensor showing timestamp of last analysis."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{SENSOR_LAST_ANALYSIS}"

    @property
    def name(self) -> str:
        return "SHA Bathroom Last Analysis"

    @property
    def state(self) -> str | None:
        return self.coordinator.last_analysis

    @property
    def icon(self) -> str:
        return "mdi:clock-check"

    @property
    def device_class(self) -> str:
        return "timestamp"


class ConfidenceSensor(SmartHeatingBaseSensor):
    """Sensor showing AI confidence level."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{SENSOR_CONFIDENCE}"

    @property
    def name(self) -> str:
        return "SHA Bathroom Confidence"

    @property
    def state(self) -> str:
        return self.coordinator.confidence

    @property
    def icon(self) -> str:
        confidence = self.coordinator.confidence
        if confidence == "high":
            return "mdi:check-circle"
        elif confidence == "medium":
            return "mdi:alert-circle"
        else:
            return "mdi:help-circle"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "possible_values": ["high", "medium", "low", "unknown"]
        }


class WeeklyReportSensor(SmartHeatingBaseSensor):
    """Sensor showing last weekly report text."""

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_{SENSOR_WEEKLY_REPORT}"

    @property
    def name(self) -> str:
        return "SHA Bathroom Weekly Report"

    @property
    def state(self) -> str:
        report = self.coordinator.weekly_report
        if len(report) > 255:
            return report[:252] + "..."
        return report

    @property
    def icon(self) -> str:
        return "mdi:chart-line"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "full_report": self.coordinator.weekly_report,
            "last_analysis": self.coordinator.last_analysis,
            "confidence": self.coordinator.confidence,
        }