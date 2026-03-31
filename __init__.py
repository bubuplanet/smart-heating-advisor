"""Smart Heating Advisor - AI powered heating optimization."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import (
    DOMAIN,
    DAILY_ANALYSIS_HOUR,
    DAILY_ANALYSIS_MINUTE,
    WEEKLY_ANALYSIS_WEEKDAY,
    WEEKLY_ANALYSIS_HOUR,
    WEEKLY_ANALYSIS_MINUTE,
)
from .coordinator import SmartHeatingCoordinator
from .config_flow import async_create_room_helpers

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Heating Advisor from a config entry."""
    _LOGGER.info("Setting up Smart Heating Advisor")

    # ── Ensure all helpers exist ──────────────────────────────────
    # Re-creates any helpers that may have been deleted since setup
    room_name = entry.data.get("room_name", "Bathroom")
    _LOGGER.info("Verifying SHA helpers for room: %s", room_name)
    await async_create_room_helpers(hass, room_name)

    # ── Create coordinator ────────────────────────────────────────
    coordinator = SmartHeatingCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ── Set up sensor platform ────────────────────────────────────
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Register manual trigger services ─────────────────────────
    async def handle_daily_analysis(call):
        """Handle manual daily analysis service call."""
        _LOGGER.info("Manual daily analysis triggered")
        await coordinator.async_run_daily_analysis()

    async def handle_weekly_analysis(call):
        """Handle manual weekly analysis service call."""
        _LOGGER.info("Manual weekly analysis triggered")
        await coordinator.async_run_weekly_analysis()

    hass.services.async_register(
        DOMAIN, "run_daily_analysis", handle_daily_analysis
    )
    hass.services.async_register(
        DOMAIN, "run_weekly_analysis", handle_weekly_analysis
    )

    # ── Schedule daily analysis at 02:00 AM ──────────────────────
    async def run_daily_analysis(now):
        """Run daily heating rate analysis."""
        _LOGGER.info("Running scheduled daily bathroom heating analysis")
        await coordinator.async_run_daily_analysis()

    async_track_time_change(
        hass,
        run_daily_analysis,
        hour=DAILY_ANALYSIS_HOUR,
        minute=DAILY_ANALYSIS_MINUTE,
        second=0,
    )

    # ── Schedule weekly analysis on Sunday at 01:00 AM ───────────
    async def run_weekly_analysis(now):
        """Run weekly heating analysis."""
        if now.weekday() == WEEKLY_ANALYSIS_WEEKDAY:
            _LOGGER.info("Running scheduled weekly bathroom heating analysis")
            await coordinator.async_run_weekly_analysis()

    async_track_time_change(
        hass,
        run_weekly_analysis,
        hour=WEEKLY_ANALYSIS_HOUR,
        minute=WEEKLY_ANALYSIS_MINUTE,
        second=0,
    )

    # ── Notify setup complete ─────────────────────────────────────
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": f"✅ Smart Heating Advisor — {room_name}",
            "message": (
                f"Smart Heating Advisor is configured for **{room_name}**.\n\n"
                f"All required helpers have been created automatically.\n\n"
                f"**Next steps:**\n"
                f"1. Import the SHA blueprint in Settings → Automations → Blueprints\n"
                f"2. Create an automation from the blueprint for this room\n"
                f"3. Add HA Schedule helpers named with target temp (e.g. `Morning Shower 26C`)\n\n"
                f"Daily AI analysis runs at 02:00 AM.\n"
                f"Weekly report runs every Sunday at 01:00 AM."
            ),
            "notification_id": f"sha_setup_{room_name.lower().replace(' ', '_')}",
        }
    )

    _LOGGER.info("Smart Heating Advisor setup complete for room: %s", room_name)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Smart Heating Advisor config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.services.async_remove(DOMAIN, "run_daily_analysis")
        hass.services.async_remove(DOMAIN, "run_weekly_analysis")
    return unload_ok