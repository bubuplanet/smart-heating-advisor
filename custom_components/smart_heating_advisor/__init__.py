"""Smart Heating Advisor — AI-powered multi-room heating optimization."""
import logging
import re
import shutil
import uuid
from pathlib import Path

import yaml

from homeassistant.components.persistent_notification import async_create as pn_async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import (
    DOMAIN,
    BLUEPRINT_FILENAME,
    BLUEPRINT_RELATIVE_PATH,
    CONF_DEBUG_LOGGING,
    CONF_ROOM_CONFIGS,
    DAILY_ANALYSIS_HOUR,
    DAILY_ANALYSIS_MINUTE,
    WEEKLY_ANALYSIS_WEEKDAY,
    WEEKLY_ANALYSIS_HOUR,
    WEEKLY_ANALYSIS_MINUTE,
)
from .coordinator import SmartHeatingCoordinator, _room_name_to_id
from .text_store import (
    async_load_messages,
    render_blueprint_status,
    render_setup_notification,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "number"]


def _apply_debug_logging(enabled: bool) -> None:
    """Set the SHA package log level based on the debug toggle."""
    _LOGGER.info("SHA debug logging %s", "enabled" if enabled else "disabled")
    level = logging.DEBUG if enabled else logging.NOTSET
    _LOGGER.setLevel(level)


BLUEPRINT_SOURCE = Path(__file__).parent / BLUEPRINT_RELATIVE_PATH / BLUEPRINT_FILENAME


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
    blueprint_dest_dir = (
        Path(hass.config.config_dir) / "blueprints" / "automation" / "smart_heating_advisor"
    )
    blueprint_dest = blueprint_dest_dir / BLUEPRINT_FILENAME
    result = await hass.async_add_executor_job(
        _do_blueprint_install, BLUEPRINT_SOURCE, blueprint_dest, blueprint_dest_dir
    )
    _LOGGER.info("SHA Blueprint: %s", result["message"])
    return result


# ──────────────────────────────────────────────────────────────────────
# Automation creation
# ──────────────────────────────────────────────────────────────────────

def _do_create_room_automation(
    config_dir: str,
    room_name: str,
    temp_sensor: str,
    trvs: list[str],
) -> bool:
    """Write a disabled SHA blueprint automation to automations.yaml.

    Runs in executor (blocking I/O).
    Returns True if the automation was created, False if it already existed or
    automations.yaml was not found / not writable.
    """
    alias = f"SHA — {room_name}"
    automations_file = Path(config_dir) / "automations.yaml"

    if not automations_file.exists():
        _LOGGER.warning(
            "automations.yaml not found at %s — cannot create automation for '%s'. "
            "Create the automation manually from Settings → Automations → Blueprints.",
            automations_file,
            room_name,
        )
        return False

    try:
        content = automations_file.read_text(encoding="utf-8")
        loaded = yaml.safe_load(content)
        automations: list = loaded if isinstance(loaded, list) else []
    except Exception as exc:
        _LOGGER.error("Failed to read automations.yaml: %s", exc)
        return False

    # Idempotency check
    for existing in automations:
        if isinstance(existing, dict) and existing.get("alias") == alias:
            _LOGGER.debug("Automation '%s' already exists — skipping creation", alias)
            return False

    new_automation = {
        "id": str(uuid.uuid4()),
        "alias": alias,
        "description": (
            f"Smart Heating Advisor automation for {room_name}. "
            "Add your Schedule helpers (e.g. 'Morning Shower 26C') then enable."
        ),
        "use_blueprint": {
            "path": "smart_heating_advisor/smart_heating_advisor.yaml",
            "input": {
                "room_name": room_name,
                "temperature_sensor": temp_sensor if temp_sensor else "",
                "radiator_thermostats": trvs if trvs else [],
                "schedules": [],
            },
        },
        "mode": "queued",
        "max": 10,
        "enabled": False,
    }
    automations.append(new_automation)

    try:
        automations_file.write_text(
            yaml.dump(
                automations,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        _LOGGER.info("Created disabled SHA automation for room '%s'", room_name)
        return True
    except Exception as exc:
        _LOGGER.error("Failed to write automations.yaml for room '%s': %s", room_name, exc)
        return False


async def _async_ensure_room_automations(
    hass: HomeAssistant,
    room_configs: list[dict],
) -> list[dict]:
    """Create blueprint automations for any room that doesn't already have one.

    Returns list of room_configs for which a new automation was created.
    """
    if not room_configs:
        return []

    config_dir = hass.config.config_dir
    newly_created: list[dict] = []

    for room_config in room_configs:
        room_name = room_config.get("room_name", "")
        if not room_name:
            continue
        temp_sensor = room_config.get("temp_sensor", "")
        trvs = room_config.get("trvs", [])

        created = await hass.async_add_executor_job(
            _do_create_room_automation,
            config_dir,
            room_name,
            temp_sensor,
            trvs,
        )
        if created:
            newly_created.append(room_config)

    if newly_created:
        try:
            await hass.services.async_call("automation", "reload")
            _LOGGER.info(
                "SHA: automation.reload called after creating %d automation(s)",
                len(newly_created),
            )
        except Exception as exc:
            _LOGGER.warning("SHA: automation.reload failed: %s", exc)

    return newly_created


# ──────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Heating Advisor from a config entry."""
    _LOGGER.info("Setting up Smart Heating Advisor")
    _LOGGER.debug(
        "SHA setup entry context: entry_id=%s hass_state=%s options=%s",
        entry.entry_id,
        hass.state,
        dict(entry.options),
    )

    # Apply debug logging preference immediately
    _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))

    # Install / upgrade blueprint
    blueprint_result = await async_install_blueprint(hass)

    # Create coordinator and load the persistent room registry
    coordinator = SmartHeatingCoordinator(hass, entry)
    await coordinator.async_load_room_registry()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ── Populate room registry from config data ──────────────────────
    # Rooms selected in the config / options flow are stored in entry.data.
    # Register each one that is not already in the persistent store so that
    # entity platforms can immediately discover them on setup.
    room_configs: list[dict] = entry.data.get(CONF_ROOM_CONFIGS, [])
    for room_config in room_configs:
        room_name = room_config.get("room_name", "").strip()
        temp_sensor = room_config.get("temp_sensor", "").strip()
        if not room_name or not temp_sensor:
            continue
        room_id = _room_name_to_id(room_name)
        if room_id not in coordinator._room_registry:
            # Room not yet in persistent store — register it now.
            # Pass schedules=[] so we don't overwrite schedules that may have
            # been added by the user via the blueprint previously.
            await coordinator.async_register_room(
                room_name=room_name,
                temp_sensor=temp_sensor,
                schedules=[],
                daily_report_enabled=True,
                weekly_report_enabled=True,
            )
            _LOGGER.info("SHA: registered new room '%s' from config data", room_name)
        else:
            _LOGGER.debug(
                "SHA: room '%s' already in registry — skipping initial registration",
                room_name,
            )

    # Set up sensor / switch / number platforms
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
        """sha.start_override — starts the override switch for a room with a duration."""
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
                "(reload the integration after adding new rooms)", room_name
            )

    async def handle_register_room(call):
        """sha.register_room — backwards-compat service (deprecated)."""
        room_name = str(call.data.get("room_name", "")).strip()
        temp_sensor = str(call.data.get("temperature_sensor", "")).strip()
        schedules = call.data.get("schedules", [])
        daily_report_enabled = bool(call.data.get("daily_report_enabled", True))
        weekly_report_enabled = bool(call.data.get("weekly_report_enabled", True))

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("sha.register_room payload: %s", dict(call.data))

        updated = await coordinator.async_register_room(
            room_name,
            temp_sensor,
            schedules,
            daily_report_enabled=daily_report_enabled,
            weekly_report_enabled=weekly_report_enabled,
        )
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "sha.register_room called: room='%s', sensor='%s', schedules=%s, updated=%s",
                room_name, temp_sensor, schedules, updated,
            )

    async def handle_unregister_room(call):
        """sha.unregister_room — remove a room from SHA's registry."""
        room_name = str(call.data.get("room_name", "")).strip()
        if not room_name:
            _LOGGER.warning("sha.unregister_room called without room_name")
            return
        removed = await coordinator.async_unregister_room(room_name)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("sha.unregister_room: room='%s', removed=%s", room_name, removed)

    hass.services.async_register(DOMAIN, "run_daily_analysis", handle_daily_analysis)
    hass.services.async_register(DOMAIN, "run_weekly_analysis", handle_weekly_analysis)
    hass.services.async_register(DOMAIN, "start_override", handle_start_override)
    hass.services.async_register(DOMAIN, "register_room", handle_register_room)
    hass.services.async_register(DOMAIN, "unregister_room", handle_unregister_room)

    # ── Scheduled analysis ───────────────────────────────────────────

    async def run_daily_analysis(now):
        _LOGGER.info("Running scheduled daily analysis")
        await coordinator.async_run_daily_analysis()

    async def run_weekly_analysis(now):
        if now.weekday() == WEEKLY_ANALYSIS_WEEKDAY:
            _LOGGER.info("Running scheduled weekly analysis")
            await coordinator.async_run_weekly_analysis()
            from datetime import datetime as _dt, timezone as _tz
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "last_weekly_analysis": _dt.now(_tz.utc).isoformat()},
            )

    async_track_time_change(
        hass, run_daily_analysis,
        hour=DAILY_ANALYSIS_HOUR, minute=DAILY_ANALYSIS_MINUTE, second=0,
    )
    async_track_time_change(
        hass, run_weekly_analysis,
        hour=WEEKLY_ANALYSIS_HOUR, minute=WEEKLY_ANALYSIS_MINUTE, second=0,
    )

    # ── Weekly catch-up on startup ───────────────────────────────────
    from datetime import datetime as _dt, timezone as _tz
    last_weekly_raw = entry.data.get("last_weekly_analysis")
    if last_weekly_raw:
        try:
            last_weekly_ts = _dt.fromisoformat(last_weekly_raw)
            elapsed_days = (_dt.now(_tz.utc) - last_weekly_ts).days
            if elapsed_days >= 7:
                _LOGGER.info(
                    "Weekly analysis catch-up: last run was %d days ago — running now",
                    elapsed_days,
                )
                hass.async_create_task(coordinator.async_run_weekly_analysis())
        except (ValueError, TypeError):
            _LOGGER.debug("Weekly catch-up: could not parse last_weekly_analysis timestamp")

    # React to options changes (e.g. debug toggle) without requiring a reload
    async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))

    entry.async_on_unload(entry.add_update_listener(_options_updated))

    # ── Create blueprint automations for new rooms ───────────────────
    newly_created_rooms = await _async_ensure_room_automations(hass, room_configs)

    # ── Setup / room notification ────────────────────────────────────
    action = blueprint_result["action"]

    if not entry.data.get("setup_notification_sent"):
        # First-time setup notification (blueprint install status + checklist)
        texts = await async_load_messages(hass)
        source_ver = blueprint_result["source_version"]
        dest_ver = blueprint_result["dest_version"]
        backup = blueprint_result.get("backup_path")
        backup_name = Path(backup).name if backup else "none"
        bp_msg = render_blueprint_status(texts, action, source_ver, dest_ver, backup_name)
        notification_title, notification_message = render_setup_notification(texts, bp_msg)

        pn_async_create(
            hass,
            notification_message,
            title=notification_title,
            notification_id="sha_setup_complete",
        )

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "setup_notification_sent": True}
        )

    if newly_created_rooms:
        # Send a follow-up notification listing the newly created automations
        rooms_list = "\n".join(
            f"- **{r['room_name']}**: Settings → Automations → SHA — {r['room_name']}"
            for r in newly_created_rooms
        )
        pn_async_create(
            hass,
            (
                f"SHA has created {len(newly_created_rooms)} room automation(s).\n\n"
                "**Next steps for each room:**\n"
                "1. Open the room's automation (links below)\n"
                "2. Add your Schedule helpers (e.g. 'Morning Shower 26C')\n"
                "3. Configure window sensors, vacation mode etc.\n"
                "4. Enable the automation\n\n"
                f"**Room automations created:**\n{rooms_list}\n\n"
                "SHA will start AI analysis after the first schedule runs."
            ),
            title="✅ Smart Heating Advisor — Room Automations Created",
            notification_id="sha_rooms_created",
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
        hass.services.async_remove(DOMAIN, "register_room")
        hass.services.async_remove(DOMAIN, "unregister_room")
    return unload_ok
