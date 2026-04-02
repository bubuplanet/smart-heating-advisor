"""Smart Heating Advisor — AI-powered multi-room heating optimization."""
import logging
import re
import shutil
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import (
    DOMAIN,
    BLUEPRINT_FILENAME,
    BLUEPRINT_RELATIVE_PATH,
    CONF_DEBUG_LOGGING,
    DAILY_ANALYSIS_HOUR,
    DAILY_ANALYSIS_MINUTE,
    WEEKLY_ANALYSIS_WEEKDAY,
    WEEKLY_ANALYSIS_HOUR,
    WEEKLY_ANALYSIS_MINUTE,
)
from .coordinator import SmartHeatingCoordinator, _room_name_to_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "number"]

_SHA_LOGGER = logging.getLogger(__name__.rsplit(".", 1)[0])  # custom_components.smart_heating_advisor


def _apply_debug_logging(enabled: bool) -> None:
    """Set the SHA package log level based on the debug toggle."""
    level = logging.DEBUG if enabled else logging.NOTSET
    _SHA_LOGGER.setLevel(level)
    _LOGGER.info("SHA debug logging %s", "enabled" if enabled else "disabled")

BLUEPRINT_SOURCE = Path(__file__).parent / BLUEPRINT_RELATIVE_PATH / BLUEPRINT_FILENAME
BLUEPRINT_DEST_DIR = Path("/config/blueprints/automation/smart_heating_advisor")
BLUEPRINT_DEST = BLUEPRINT_DEST_DIR / BLUEPRINT_FILENAME


# ──────────────────────────────────────────────────────────────────────
# Blueprint versioning
# ──────────────────────────────────────────────────────────────────────

def _get_blueprint_version(content: str) -> tuple[int, int, int]:
    """Extract version tuple from blueprint description.
    Looks for: **version: 1.0.0**
    """
    match = re.search(r"\*\*version:\s*(\d+)\.(\d+)\.(\d+)\*\*", content)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (0, 0, 0)


def _version_str(v: tuple[int, int, int]) -> str:
    return ".".join(str(x) for x in v)


def _do_blueprint_install(
    source: Path, dest: Path, dest_dir: Path
) -> dict:
    """Synchronous blueprint install — runs in executor."""
    result = {
        "action": "error",
        "source_version": "0.0.0",
        "dest_version": "0.0.0",
        "message": "",
        "backup_path": None,
    }

    if not source.exists():
        result["message"] = f"Blueprint source not found: {source}"
        return result

    source_content = source.read_text(encoding="utf-8")
    source_version = _get_blueprint_version(source_content)
    result["source_version"] = _version_str(source_version)

    dest_dir.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        dest_content = dest.read_text(encoding="utf-8")
        dest_version = _get_blueprint_version(dest_content)
        result["dest_version"] = _version_str(dest_version)

        if source_version == dest_version and source_content == dest_content:
            result["action"] = "skipped"
            result["message"] = (
                f"Blueprint v{result['source_version']} already up to date"
            )
            return result

        if dest_version > source_version:
            result["action"] = "skipped"
            result["message"] = (
                f"Installed blueprint v{result['dest_version']} is newer "
                f"than bundled v{result['source_version']} — skipping"
            )
            return result

        backup_path = dest.with_suffix(f".v{result['dest_version']}.yaml.bak")
        shutil.copy2(dest, backup_path)
        result["backup_path"] = str(backup_path)
        _LOGGER.info("Backed up blueprint v%s to %s", result["dest_version"], backup_path)

        shutil.copy2(source, dest)
        result["action"] = "updated"
        result["message"] = (
            f"Blueprint updated from v{result['dest_version']} "
            f"to v{result['source_version']}"
        )
        return result

    shutil.copy2(source, dest)
    result["action"] = "installed"
    result["dest_version"] = result["source_version"]
    result["message"] = f"Blueprint v{result['source_version']} installed"
    return result


async def async_install_blueprint(hass: HomeAssistant) -> dict:
    """Install or upgrade the SHA blueprint."""
    result = await hass.async_add_executor_job(
        _do_blueprint_install, BLUEPRINT_SOURCE, BLUEPRINT_DEST, BLUEPRINT_DEST_DIR
    )
    _LOGGER.info("SHA Blueprint: %s", result["message"])
    return result


# ──────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Heating Advisor from a config entry."""
    _LOGGER.info("Setting up Smart Heating Advisor")

    # Apply debug logging preference immediately
    _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))

    # Install / upgrade blueprint
    blueprint_result = await async_install_blueprint(hass)

    # Create coordinator
    coordinator = SmartHeatingCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Register services ────────────────────────────────────────────

    async def handle_daily_analysis(call):
        """sha.run_daily_analysis — manual trigger."""
        _LOGGER.info("Manual daily analysis triggered")
        await coordinator.async_run_daily_analysis()

    async def handle_weekly_analysis(call):
        """sha.run_weekly_analysis — manual trigger."""
        _LOGGER.info("Manual weekly analysis triggered")
        await coordinator.async_run_weekly_analysis()

    async def handle_start_override(call):
        """sha.start_override — starts the override switch for a room with a duration.

        Called by the blueprint instead of timer.start.
        Data:
          room_name (str): the room name matching the blueprint input.
          duration_minutes (int, optional): override duration in minutes (default 120).
        """
        room_name = call.data.get("room_name", "")
        duration_minutes = int(call.data.get("duration_minutes", 120))
        _LOGGER.debug("sha.start_override called: room='%s', duration=%d min", room_name, duration_minutes)
        if not room_name:
            _LOGGER.warning("sha.start_override called without room_name")
            return
        room_id = _room_name_to_id(room_name)
        override_switch = coordinator._override_switches.get(room_id)
        _LOGGER.debug("sha.start_override: resolved room_id='%s', switch found=%s", room_id, override_switch is not None)
        if override_switch:
            await override_switch.async_start(duration_minutes * 60)
        else:
            _LOGGER.warning(
                "sha.start_override: no override switch for room '%s' "
                "(reload the integration after adding new blueprint automations)", room_name
            )

    hass.services.async_register(DOMAIN, "run_daily_analysis", handle_daily_analysis)
    hass.services.async_register(DOMAIN, "run_weekly_analysis", handle_weekly_analysis)
    hass.services.async_register(DOMAIN, "start_override", handle_start_override)

    # ── Scheduled analysis ───────────────────────────────────────────

    async def run_daily_analysis(now):
        _LOGGER.info("Running scheduled daily analysis")
        await coordinator.async_run_daily_analysis()

    async def run_weekly_analysis(now):
        if now.weekday() == WEEKLY_ANALYSIS_WEEKDAY:
            _LOGGER.info("Running scheduled weekly analysis")
            await coordinator.async_run_weekly_analysis()

    async_track_time_change(
        hass, run_daily_analysis,
        hour=DAILY_ANALYSIS_HOUR, minute=DAILY_ANALYSIS_MINUTE, second=0,
    )
    async_track_time_change(
        hass, run_weekly_analysis,
        hour=WEEKLY_ANALYSIS_HOUR, minute=WEEKLY_ANALYSIS_MINUTE, second=0,
    )

    # React to options changes (e.g. debug toggle) without requiring a reload
    async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))

    entry.async_on_unload(entry.add_update_listener(_options_updated))

    # ── Setup notification ───────────────────────────────────────────
    action = blueprint_result["action"]
    source_ver = blueprint_result["source_version"]
    dest_ver = blueprint_result["dest_version"]
    backup = blueprint_result.get("backup_path")

    if action == "installed":
        bp_msg = f"✅ Blueprint v{source_ver} installed automatically.\n\n"
    elif action == "updated":
        bp_msg = (
            f"🔄 Blueprint updated from v{dest_ver} to v{source_ver}.\n"
            f"Backup saved as `{Path(backup).name}`.\n"
            f"Existing automations continue working — re-save to use new features.\n\n"
        )
    elif action == "skipped":
        bp_msg = f"✅ Blueprint v{source_ver} already up to date.\n\n"
    else:
        bp_msg = (
            "⚠️ Blueprint could not be installed automatically.\n"
            "Import it manually using the magic link in the README.\n\n"
        )

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "✅ Smart Heating Advisor — Ready",
            "message": (
                f"Smart Heating Advisor is configured.\n\n"
                f"{bp_msg}"
                f"**Next steps:**\n"
                f"1. Go to **Settings → Automations → Blueprints**\n"
                f"2. Find **Smart Heating Advisor** blueprint\n"
                f"3. Create an automation per room\n"
                f"4. Name each Schedule helper with target temp at the end\n"
                f"   e.g. `Morning Shower 26C`, `Evening Bath 28C`\n\n"
                f"Switch/Number helper entities are created automatically "
                f"when the integration loads (reload after adding new rooms).\n\n"
                f"⚠️ **Upgrading from v1?** Re-open and re-save each room automation "
                f"so it uses the updated blueprint (v2).\n\n"
                f"Daily AI analysis: **02:00 AM**\n"
                f"Weekly report: **Sunday 01:00 AM**"
            ),
            "notification_id": "sha_setup_complete",
        },
    )

    _LOGGER.info("Smart Heating Advisor setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Smart Heating Advisor config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.services.async_remove(DOMAIN, "run_daily_analysis")
        hass.services.async_remove(DOMAIN, "run_weekly_analysis")
        hass.services.async_remove(DOMAIN, "start_override")
    return unload_ok
