"""Smart Heating Advisor — AI-powered multi-room heating optimization."""
import logging
import re
import shutil
import uuid
from pathlib import Path

import yaml

from homeassistant.components.persistent_notification import async_create as pn_async_create
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
    DEFAULT_DEFAULT_TEMP,
    DEFAULT_AIRING_DURATION,
    DEFAULT_HUMIDITY_THRESHOLD,
)
from .coordinator import SmartHeatingCoordinator, _room_name_to_id
from .text_store import (
    async_load_messages,
    render_setup_notification,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "number", "binary_sensor"]


def _apply_debug_logging(enabled: bool) -> None:
    """Set the SHA package log level based on the debug toggle."""
    _LOGGER.info("SHA debug logging %s", "enabled" if enabled else "disabled")
    level = logging.DEBUG if enabled else logging.NOTSET
    _LOGGER.setLevel(level)


BLUEPRINT_SOURCE = Path(__file__).parent / BLUEPRINT_RELATIVE_PATH / BLUEPRINT_FILENAME
PROMPTS_SOURCE_DIR = Path(__file__).parent / "prompts"




# ──────────────────────────────────────────────────────────────────────
# Prompt installation
# ──────────────────────────────────────────────────────────────────────

def _do_copy_prompts(source_dir: Path, dest_dir: Path) -> list[str]:
    """Copy bundled prompt files to the user-editable location.

    Always overwrites existing files so that prompt changes made in the
    integration are picked up on the next HA restart. To customise a prompt
    permanently, add the modified file to a git-ignored location and restore
    it after each update.

    Runs in executor (blocking I/O).
    Returns the list of filenames that were copied or updated.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in source_dir.glob("*.md"):
        dst = dest_dir / src.name
        action = "updated" if dst.exists() else "installed"
        shutil.copy2(src, dst)
        copied.append(src.name)
        _LOGGER.info("SHA prompts: %s %s → %s", action, src.name, dst)
    return copied


async def async_install_prompts(hass: HomeAssistant) -> list[str]:
    """Copy bundled prompts to /config/smart_heating_advisor/prompts/."""
    dest_dir = Path(hass.config.config_dir) / "smart_heating_advisor" / "prompts"
    return await hass.async_add_executor_job(_do_copy_prompts, PROMPTS_SOURCE_DIR, dest_dir)


# ──────────────────────────────────────────────────────────────────────
# Automation creation
# ──────────────────────────────────────────────────────────────────────

def _do_create_room_automation(
    config_dir: str,
    room_name: str,
    temp_sensor: str,
    trvs: list[str],
) -> bool:
    """Write an expanded inline SHA automation to automations.yaml.

    Reads the bundled blueprint YAML, substitutes the room_id input, strips
    the blueprint metadata, and writes a fully self-contained automation.
    The blueprint file stays in custom_components and is never copied to
    /config/blueprints — users do not see it in the Blueprints UI.

    Runs in executor (blocking I/O).
    Returns True if the automation was created, False if it already existed or
    automations.yaml was not found / not writable.
    """
    import re as _re

    alias = f"SHA — {room_name}"
    automations_file = Path(config_dir) / "automations.yaml"

    if not automations_file.exists():
        _LOGGER.warning(
            "automations.yaml not found at %s — cannot create automation for '%s'.",
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

    # Idempotency: skip inline automations; recreate legacy use_blueprint ones
    for existing in automations:
        if not (isinstance(existing, dict) and existing.get("alias") == alias):
            continue
        if "use_blueprint" not in existing:
            _LOGGER.debug("Automation '%s' already inline — skipping", alias)
            return False
        _LOGGER.info(
            "Automation '%s' uses legacy use_blueprint format — removing and recreating inline",
            alias,
        )
        automations.remove(existing)
        break

    room_id = room_name.lower()
    room_id = room_id.replace("'", "")
    room_id = _re.sub(r"[\s\-]+", "_", room_id)
    room_id = _re.sub(r"[^a-z0-9_]", "", room_id)

    # Read blueprint, substitute !input room_id, strip blueprint metadata
    if not BLUEPRINT_SOURCE.exists():
        _LOGGER.error("Blueprint source not found at %s — cannot create automation", BLUEPRINT_SOURCE)
        return False
    blueprint_text = BLUEPRINT_SOURCE.read_text(encoding="utf-8")
    expanded_text = blueprint_text.replace("!input room_id", room_id)
    try:
        blueprint_parsed = yaml.safe_load(expanded_text)
    except Exception as exc:
        _LOGGER.error("Failed to parse blueprint YAML: %s", exc)
        return False

    blueprint_parsed.pop("blueprint", None)
    new_automation = {
        "id": str(uuid.uuid4()),
        "alias": alias,
        "description": (
            f"Smart Heating Advisor automation for {room_name}. Managed by SHA — do not edit manually."
        ),
        **blueprint_parsed,
        "max": 10,
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
        _LOGGER.info("SHA: created inline automation for room '%s' (room_id=%s)", room_name, room_id)
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
        # Remove stale entity registry entries before reload so recreated
        # automations get a clean entity_id instead of a suffixed one.
        entity_registry = er.async_get(hass)
        for room_config in newly_created:
            room_id = _room_name_to_id(room_config.get("room_name", ""))
            stale_entity_id = f"automation.sha_{room_id}"
            stale_entry = entity_registry.async_get(stale_entity_id)
            if stale_entry:
                entity_registry.async_remove(stale_entity_id)
                _LOGGER.info(
                    "SHA: removed stale automation entity %s from registry",
                    stale_entity_id,
                )

        try:
            await hass.services.async_call("automation", "reload", blocking=True)
            _LOGGER.info(
                "SHA: automation.reload called after creating %d automation(s)",
                len(newly_created),
            )
        except Exception as exc:
            _LOGGER.warning("SHA: automation.reload failed: %s", exc)

    return newly_created


# ──────────────────────────────────────────────────────────────────────
# Room removal helpers
# ──────────────────────────────────────────────────────────────────────

async def _async_remove_room_entities(
    hass: HomeAssistant, entry_id: str, room_id: str
) -> None:
    """Remove all SHA entity registry entries and the room device for a room.

    Covers both unique_id prefixes used by SHA:
      - sha_{room_id}_*   (switches, number)
      - {entry_id}_{room_id}_*  (sensors)

    Filtered by config_entry_id so we never touch unrelated integrations.
    After all entities are removed the room device is deleted if it has no
    remaining entities (device identifier: (DOMAIN, "{entry_id}_{room_id}")).
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Collect matching entities and their device IDs before removal
    to_remove: list[str] = []
    device_ids: set[str] = set()
    for entry in ent_reg.entities.values():
        if entry.config_entry_id == entry_id and (
            entry.unique_id.startswith(f"sha_{room_id}_")
            or entry.unique_id.startswith(f"{entry_id}_{room_id}_")
        ):
            to_remove.append(entry.entity_id)
            if entry.device_id:
                device_ids.add(entry.device_id)

    for entity_id in to_remove:
        ent_reg.async_remove(entity_id)
        _LOGGER.debug("SHA: removed entity '%s' for room_id='%s'", entity_id, room_id)

    if to_remove:
        _LOGGER.info(
            "SHA: removed %d entity/entities for room_id='%s'", len(to_remove), room_id
        )
    else:
        _LOGGER.info(
            "SHA: no entities found for room_id='%s' — nothing to remove", room_id
        )

    # Remove the room device if it has no remaining entities
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        remaining = [e for e in ent_reg.entities.values() if e.device_id == device_id]
        if not remaining:
            dev_reg.async_remove_device(device_id)
            _LOGGER.info(
                "SHA: removed device '%s' (id=%s) for room_id='%s'",
                device.name, device_id, room_id,
            )


def _do_delete_room_automation(config_dir: str, room_name: str) -> bool:
    """Remove the SHA automation for a room from automations.yaml.

    Runs in executor (blocking I/O).
    Returns True if an entry was removed, False if not found or on error.
    """
    alias = f"SHA — {room_name}"
    automations_file = Path(config_dir) / "automations.yaml"

    if not automations_file.exists():
        _LOGGER.info(
            "SHA: automations.yaml not found — automation '%s' may not exist", alias
        )
        return False

    try:
        content = automations_file.read_text(encoding="utf-8")
        loaded = yaml.safe_load(content)
        automations: list = loaded if isinstance(loaded, list) else []
    except Exception as exc:
        _LOGGER.error("SHA: failed to read automations.yaml: %s", exc)
        return False

    filtered = [a for a in automations if not (isinstance(a, dict) and a.get("alias") == alias)]
    if len(filtered) == len(automations):
        _LOGGER.info("SHA: automation '%s' not found in automations.yaml — already deleted?", alias)
        return False

    try:
        automations_file.write_text(
            yaml.dump(filtered, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        _LOGGER.info("SHA: deleted automation '%s' from automations.yaml", alias)
        return True
    except Exception as exc:
        _LOGGER.error("SHA: failed to write automations.yaml when deleting '%s': %s", alias, exc)
        return False


async def _async_delete_room_automation(hass: HomeAssistant, room_name: str) -> None:
    """Delete the SHA blueprint automation for a room from automations.yaml and reload."""
    deleted = await hass.async_add_executor_job(
        _do_delete_room_automation, hass.config.config_dir, room_name
    )
    if deleted:
        try:
            await hass.services.async_call("automation", "reload", blocking=True)
            _LOGGER.info("SHA: automation.reload called after deleting '%s'", f"SHA — {room_name}")
        except Exception as exc:
            _LOGGER.warning("SHA: automation.reload failed after deleting room automation: %s", exc)


# ──────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Heating Advisor from a config entry.

    Room lifecycle:
      - Rooms are stored as SubEntries on the config entry (HA 2024.11+).
      - On first startup, rooms from CONF_ROOM_CONFIGS (legacy wizard flow) are
        migrated to SubEntries. After migration the flag 'sha_migration_v2_complete'
        is set and CONF_ROOM_CONFIGS is no longer used as authoritative source.
      - SubEntries are the single source of truth for which rooms SHA manages.
        Any room in the coordinator registry that is NOT in entry.subentries
        is treated as deleted and cleaned up automatically.
      - When a SubEntry is added or removed, HA fires the update_listeners.
        The listener registered at the end of this function reloads the config
        entry so platforms pick up the change.
    """
    _LOGGER.info("Setting up Smart Heating Advisor")
    _LOGGER.debug(
        "SHA setup entry context: entry_id=%s hass_state=%s options=%s",
        entry.entry_id,
        hass.state,
        dict(entry.options),
    )

    # Apply debug logging preference immediately
    _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))

    # Copy bundled prompt files to /config/smart_heating_advisor/prompts/
    # (only if not already present — preserves user edits)
    await async_install_prompts(hass)

    # Create coordinator and load the persistent room registry
    coordinator = SmartHeatingCoordinator(hass, entry)
    await coordinator.async_load_room_registry()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ── Phase 1: One-time migration — CONF_ROOM_CONFIGS → SubEntries ──
    # Runs exactly once (guarded by the 'sha_migration_v2_complete' flag).
    # Registers legacy rooms in coordinator, then creates a SubEntry for
    # each room that does not yet have one.
    # The update_listener is NOT registered yet, so async_add_subentry /
    # async_update_entry calls here do NOT trigger a reload loop.
    if not entry.data.get("sha_migration_v2_complete"):
        _LOGGER.info("SHA: starting v2 migration — moving rooms to SubEntries")

        # Register legacy rooms in coordinator
        for room_config in entry.data.get(CONF_ROOM_CONFIGS, []):
            room_name = room_config.get("room_name", "").strip()
            if not room_name:
                continue
            room_id = _room_name_to_id(room_name)
            if room_id not in coordinator._room_registry:
                await coordinator.async_register_room(
                    room_name=room_name,
                    temp_sensor=room_config.get("temp_sensor", ""),
                    schedules=[],
                    daily_report_enabled=True,
                    weekly_report_enabled=True,
                )
                _LOGGER.info("SHA: migration — registered room '%s' in coordinator", room_name)

        # Create SubEntries for all coordinator rooms that don't have one yet
        existing_subentry_rooms: set[str] = {
            s.data.get("room_name")
            for s in entry.subentries.values()
            if s.data.get("room_name")
        }
        for room in coordinator.discover_rooms():
            if room.room_name not in existing_subentry_rooms:
                try:
                    hass.config_entries.async_add_subentry(
                        entry,
                        ConfigSubentry(
                            data={
                                "room_name": room.room_name,
                                "area_id": None,
                                "temp_sensor": room.temp_sensor,
                                "trvs": [],
                            },
                            subentry_type="room",
                            title=room.room_name,
                            unique_id=f"sha_{room.room_id}",
                        ),
                    )
                    _LOGGER.info(
                        "SHA: migration — created SubEntry for room '%s'", room.room_name
                    )
                except Exception as exc:
                    _LOGGER.warning(
                        "SHA: migration — could not create SubEntry for room '%s': %s",
                        room.room_name, exc,
                    )

        # Mark migration complete (no listener registered yet → no reload triggered)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "sha_migration_v2_complete": True}
        )
        _LOGGER.info("SHA: v2 migration complete")

    # ── Phase 1b: TRV backfill migration ─────────────────────────────────
    # The Phase 1 migration created subentries with trvs: [] (empty).
    # Rooms that were set up via the initial config flow had their TRVs stored
    # in entry.data[CONF_ROOM_CONFIGS] but those were never ported to the
    # subentry data.  This one-time migration reads TRVs from the legacy config
    # and writes them to any subentry that currently has trvs: [].
    if not entry.data.get("sha_trv_migration_complete"):
        legacy_trvs_by_id: dict[str, list] = {
            _room_name_to_id(r.get("room_name", "")): r.get("trvs", [])
            for r in entry.data.get(CONF_ROOM_CONFIGS, [])
            if r.get("room_name")
        }
        for subentry in entry.subentries.values():
            sub_room = subentry.data.get("room_name", "")
            if not sub_room:
                continue
            if subentry.data.get("trvs"):
                continue  # Already has TRVs — skip
            room_id = _room_name_to_id(sub_room)
            legacy_trvs = legacy_trvs_by_id.get(room_id, [])
            if legacy_trvs:
                hass.config_entries.async_update_subentry(
                    entry,
                    subentry,
                    data={**dict(subentry.data), "trvs": legacy_trvs},
                )
                _LOGGER.info(
                    "SHA: TRV migration — backfilled %d TRV(s) for room '%s': %s",
                    len(legacy_trvs), sub_room, legacy_trvs,
                )
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "sha_trv_migration_complete": True}
        )
        _LOGGER.info("SHA: TRV backfill migration complete")

    # ── Phase 3: Subentry schema migration — add new room wizard fields ──
    # New fields introduced in Phase 3 config flow redesign.
    # Safe defaults preserve all existing behaviour for rooms configured
    # before the wizard was added.
    if not entry.data.get("sha_phase3_migration_complete"):
        _LOGGER.info("SHA: starting Phase 3 subentry migration")
        for subentry in list(entry.subentries.values()):
            sub_room = subentry.data.get("room_name", "")
            if not sub_room:
                continue
            existing = dict(subentry.data)
            needs_update = False
            defaults: dict = {
                "thermostat_sensor": "",
                "fixed_trvs": [],
                "fixed_trv_temp": DEFAULT_DEFAULT_TEMP,
                "window_sensors": [],
                "airing_mode_enabled": True,
                "airing_duration_minutes": DEFAULT_AIRING_DURATION,
                "default_temp_enabled": True,
                "default_temp": DEFAULT_DEFAULT_TEMP,
                "humidity_enabled": False,
                "humidity_sensor": "",
                "humidity_threshold": DEFAULT_HUMIDITY_THRESHOLD,
            }
            for key, default_val in defaults.items():
                if key not in existing:
                    existing[key] = default_val
                    needs_update = True
            if needs_update:
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=existing
                )
                _LOGGER.info(
                    "SHA: Phase 3 migration — updated subentry for room '%s'", sub_room
                )
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "sha_phase3_migration_complete": True}
        )
        _LOGGER.info("SHA: Phase 3 subentry migration complete")

    # ── Phase 3b: Field rename + new fields migration ─────────────────
    # Introduced with the Phase 3b config flow redesign:
    #   - temp_sensor renamed to thermostat_sensor
    #   - humidity_threshold removed (SHA calculates automatically)
    #   - airing_duration_minutes converted to airing_duration (HH:MM:SS)
    #     and airing_duration_seconds (int)
    #   - override_enabled, override_duration_minutes added
    #   - schedules added
    if not entry.data.get("sha_phase3b_migration_complete"):
        _LOGGER.info("SHA: starting Phase 3b subentry migration")
        for subentry in list(entry.subentries.values()):
            sub_room = subentry.data.get("room_name", "")
            if not sub_room:
                continue
            existing = dict(subentry.data)
            needs_update = False

            # Rename temp_sensor → thermostat_sensor (old value takes precedence)
            if "temp_sensor" in existing:
                existing["thermostat_sensor"] = existing.pop("temp_sensor")
                needs_update = True

            # Remove humidity_threshold (SHA calculates it automatically from data)
            if "humidity_threshold" in existing:
                existing.pop("humidity_threshold")
                needs_update = True

            # Add override fields
            if "override_enabled" not in existing:
                existing["override_enabled"] = False
                needs_update = True
            if "override_duration_minutes" not in existing:
                existing["override_duration_minutes"] = 60
                needs_update = True

            # Convert airing_duration_minutes → airing_duration + airing_duration_seconds
            if "airing_duration_minutes" in existing and "airing_duration" not in existing:
                minutes = int(existing.pop("airing_duration_minutes", DEFAULT_AIRING_DURATION))
                seconds = minutes * 60
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                existing["airing_duration"] = f"{h:02d}:{m:02d}:{s:02d}"
                existing["airing_duration_seconds"] = seconds
                needs_update = True
            elif "airing_duration" in existing and "airing_duration_seconds" not in existing:
                try:
                    parts = existing["airing_duration"].split(":")
                    existing["airing_duration_seconds"] = (
                        int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    )
                except Exception:
                    existing["airing_duration_seconds"] = 120
                needs_update = True

            # Add schedules list if missing
            if "schedules" not in existing:
                existing["schedules"] = []
                needs_update = True

            if needs_update:
                hass.config_entries.async_update_subentry(entry, subentry, data=existing)
                _LOGGER.info(
                    "SHA: Phase 3b migration — updated subentry for room '%s'", sub_room
                )

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "sha_phase3b_migration_complete": True}
        )
        _LOGGER.info("SHA: Phase 3b subentry migration complete")

    # ── Phase 2: Sync coordinator with SubEntries (authoritative source) ──
    # SubEntries are the single source of truth after migration.
    # • New subentry (room added via "+ Add Room") → register in coordinator.
    # • Orphaned coordinator room (subentry deleted) → full cleanup.
    subentry_room_names: set[str] = {
        s.data.get("room_name")
        for s in entry.subentries.values()
        if s.data.get("room_name")
    }
    subentry_data_by_name: dict[str, dict] = {
        s.data.get("room_name"): dict(s.data)
        for s in entry.subentries.values()
        if s.data.get("room_name")
    }

    # Register rooms that are in subentries but not yet in coordinator
    for room_name in subentry_room_names:
        room_id = _room_name_to_id(room_name)
        if room_id not in coordinator._room_registry:
            sub_data = subentry_data_by_name[room_name]
            await coordinator.async_register_room(
                room_name=room_name,
                temp_sensor=(
                    sub_data.get("thermostat_sensor") or sub_data.get("temp_sensor", "")
                ),
                schedules=sub_data.get("schedules", []),
                daily_report_enabled=True,
                weekly_report_enabled=True,
            )
            _LOGGER.info("SHA: registered room '%s' from SubEntry", room_name)

    # Detect and clean up rooms that are in coordinator but not in any subentry
    # (these are rooms whose SubEntry was deleted by the user via ⋮ → Delete).
    orphaned: list[tuple[str, str]] = [
        (room_id, coordinator._room_registry[room_id].get("room_name", room_id))
        for room_id in list(coordinator._room_registry.keys())
        if coordinator._room_registry[room_id].get("room_name") not in subentry_room_names
    ]
    for room_id, room_name in orphaned:
        _LOGGER.info(
            "SHA: room '%s' (id=%s) is no longer a SubEntry — cleaning up",
            room_name, room_id,
        )
        await coordinator.async_unregister_room(room_name)
        # Entity registry cleanup is automatic: HA removed all entries linked
        # to the deleted subentry via config_subentry_id before this reload.

        # Explicit device cleanup — HA does not automatically remove the device
        # when subentry entities are deleted. Look up by the SHA device identifier
        # and remove if no entities remain on it.
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{entry.entry_id}_{room_id}")}
        )
        if device:
            remaining = [e for e in ent_reg.entities.values() if e.device_id == device.id]
            if not remaining:
                dev_reg.async_remove_device(device.id)
                _LOGGER.info(
                    "SHA: removed device '%s' for room_id='%s'", device.name, room_id
                )
            else:
                _LOGGER.warning(
                    "SHA: device '%s' still has %d entity/entities — not removing",
                    device.name, len(remaining),
                )

        await _async_delete_room_automation(hass, room_name)
        pn_async_create(
            hass,
            (
                f"Room **{room_name}** has been removed from Smart Heating Advisor.\n\n"
                f"The automation **SHA — {room_name}** has been deleted.\n\n"
                f"All SHA entities for this room have been removed."
            ),
            title=f"🗑️ SHA — {room_name} Removed",
            notification_id=f"sha_removed_{room_id}",
        )

    # Set up sensor / switch / number platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # All platform entities are now registered — start runtime state listeners
    await coordinator.async_start_listeners()

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
        _LOGGER.debug(
            "sha.start_override called: room='%s', duration=%d min", room_name, duration_minutes
        )
        if not room_name:
            _LOGGER.warning("sha.start_override called without room_name")
            return
        room_id = _room_name_to_id(room_name)
        override_switch = coordinator._override_switches.get(room_id)
        _LOGGER.debug(
            "sha.start_override: resolved room_id='%s', switch found=%s",
            room_id, override_switch is not None,
        )
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
        """sha.unregister_room — backwards-compat service (deprecated).

        Prefer using the ⋮ → Delete button on the integration card.
        This service only removes the room from the coordinator registry;
        the SubEntry must be removed separately for full UI cleanup.
        """
        room_name = str(call.data.get("room_name", "")).strip()
        if not room_name:
            _LOGGER.warning("sha.unregister_room called without room_name")
            return

        room_id = await coordinator.async_unregister_room(room_name)
        if not room_id:
            _LOGGER.warning(
                "sha.unregister_room: room '%s' not found in registry", room_name
            )
            return

        await _async_remove_room_entities(hass, entry.entry_id, room_id)
        await _async_delete_room_automation(hass, room_name)

        pn_async_create(
            hass,
            (
                f"Room **{room_name}** has been removed from Smart Heating Advisor.\n\n"
                f"The automation **SHA — {room_name}** has been deleted.\n\n"
                f"All SHA entities for this room have been removed."
            ),
            title=f"🗑️ SHA — {room_name} Removed",
            notification_id=f"sha_removed_{room_id}",
        )
        _LOGGER.info("sha.unregister_room: room '%s' removed successfully", room_name)

    async def handle_get_room_config(call):
        """sha.get_room_config — returns room config for blueprint use."""
        room_id = str(call.data.get("room_id", "")).strip()
        if not room_id:
            _LOGGER.warning("sha.get_room_config called without room_id")
            return {}
        return await coordinator.async_get_room_config(room_id)

    hass.services.async_register(DOMAIN, "run_daily_analysis", handle_daily_analysis)
    hass.services.async_register(DOMAIN, "run_weekly_analysis", handle_weekly_analysis)
    hass.services.async_register(DOMAIN, "start_override", handle_start_override)
    hass.services.async_register(DOMAIN, "register_room", handle_register_room)
    hass.services.async_register(DOMAIN, "unregister_room", handle_unregister_room)
    hass.services.async_register(
        DOMAIN, "get_room_config", handle_get_room_config,
        supports_response=SupportsResponse.ONLY,
    )

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

    # ── Create blueprint automations for all coordinator rooms ───────
    # Build room config dicts from coordinator + SubEntry data (for TRV list).
    rooms_for_automation = [
        {
            "room_name": r.room_name,
            "temp_sensor": r.temp_sensor,
            "trvs": subentry_data_by_name.get(r.room_name, {}).get("trvs", []),
        }
        for r in coordinator.discover_rooms()
    ]
    newly_created_rooms = await _async_ensure_room_automations(hass, rooms_for_automation)

    # ── Setup / room notification ────────────────────────────────────
    if not entry.data.get("setup_notification_sent"):
        texts = await async_load_messages(hass)
        bp_msg = "✅ Automations are created and managed inline by SHA."
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
        rooms_list = "\n".join(
            f"- **{r['room_name']}**: Settings → Automations → SHA — {r['room_name']}"
            for r in newly_created_rooms
        )
        pn_async_create(
            hass,
            (
                f"SHA has created {len(newly_created_rooms)} room automation(s). "
                "Each automation is enabled and will start controlling heating immediately.\n\n"
                "**Optional next steps:**\n"
                "1. Add Schedule helpers to each room via the integration card\n"
                "   (e.g. 'Morning Shower 26C', 'Day Heating 20C')\n"
                "2. Configure window sensors and vacation mode if needed\n\n"
                f"**Room automations created:**\n{rooms_list}\n\n"
                "SHA will start AI analysis after the first schedule runs."
            ),
            title="✅ Smart Heating Advisor — Room Automations Created",
            notification_id="sha_rooms_created",
        )

    # ── Update listener — reload on SubEntry changes ─────────────────
    # Registered LAST so that migration calls above (async_add_subentry,
    # async_update_entry) do not accidentally trigger this listener.
    #
    # The listener is called whenever entry.options OR entry.subentries change.
    # • SubEntry added/removed → subentry IDs differ from snapshot → reload.
    # • Options-only change (e.g. debug toggle) → apply live, no reload.
    # • Data-only change (e.g. weekly analysis timestamp, connection settings)
    #   → no subentry change → no reload from this listener.
    #   Connection-settings changes already schedule an explicit reload in
    #   the options flow handler.
    _known_subentry_ids = frozenset(entry.subentries.keys())

    async def _async_update_listener(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        _apply_debug_logging(entry.options.get(CONF_DEBUG_LOGGING, False))
        current_ids = frozenset(entry.subentries.keys())
        if current_ids != _known_subentry_ids:
            _LOGGER.info(
                "SHA: SubEntries changed (%d → %d) — reloading",
                len(_known_subentry_ids),
                len(current_ids),
            )
            await hass.config_entries.async_reload(entry.entry_id)
            return
        # Options changed without subentry change — restart listeners so
        # vacation config (enabled/calendar) picks up the new values.
        coordinator.async_stop_listeners()
        await coordinator.async_start_listeners()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Smart Heating Advisor setup complete")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Smart Heating Advisor config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        coordinator.async_stop_listeners()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.services.async_remove(DOMAIN, "run_daily_analysis")
        hass.services.async_remove(DOMAIN, "run_weekly_analysis")
        hass.services.async_remove(DOMAIN, "start_override")
        hass.services.async_remove(DOMAIN, "register_room")
        hass.services.async_remove(DOMAIN, "unregister_room")
        hass.services.async_remove(DOMAIN, "get_room_config")
    return unload_ok
