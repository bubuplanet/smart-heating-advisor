"""Text resource loading for Smart Heating Advisor."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DEFAULT_MESSAGES_FILE = Path(__file__).with_name("messages.json")
USER_MESSAGES_FILENAME = "smart_heating_advisor_messages.json"


def _load_json_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _safe_format(template: str, **kwargs: Any) -> str:
    try:
        return template.format(**kwargs)
    except Exception as err:
        _LOGGER.warning("Invalid text template '%s': %s", template, err)
        return template


async def async_load_messages(hass: HomeAssistant) -> dict[str, Any]:
    """Load bundled messages plus optional user overrides from /config."""
    default_messages = await hass.async_add_executor_job(_load_json_file, DEFAULT_MESSAGES_FILE)

    user_file = Path(hass.config.config_dir) / USER_MESSAGES_FILENAME
    if not user_file.exists():
        return default_messages

    try:
        user_messages = await hass.async_add_executor_job(_load_json_file, user_file)
    except Exception as err:
        _LOGGER.warning("Failed to load user messages from %s: %s", user_file, err)
        return default_messages

    _LOGGER.info("Loaded custom SHA messages from %s", user_file)
    return _deep_merge(default_messages, user_messages)


def render_blueprint_status(
    texts: dict[str, Any],
    action: str,
    source_ver: str,
    dest_ver: str,
    backup_name: str,
) -> str:
    """Render blueprint status block for setup notification."""
    pn = texts.get("persistent_notification", {}) if isinstance(texts, dict) else {}

    if action == "installed":
        template = pn.get("bp_installed", "✅ Blueprint v{source_ver} installed automatically.\n\n")
    elif action == "updated":
        template = pn.get(
            "bp_updated",
            "🔄 Blueprint updated from v{dest_ver} to v{source_ver}.\n"
            "Backup saved as {backup_name}.\n"
            "Existing automations continue working — re-save to use new features.\n\n",
        )
    elif action == "skipped":
        template = pn.get("bp_skipped", "✅ Blueprint v{source_ver} already up to date.\n\n")
    else:
        template = pn.get(
            "bp_error",
            "⚠️ Blueprint could not be installed automatically.\n"
            "Import it manually using the magic link in the README.\n\n",
        )

    return _safe_format(
        str(template),
        source_ver=source_ver,
        dest_ver=dest_ver,
        backup_name=backup_name,
    )


def render_setup_notification(texts: dict[str, Any], bp_msg: str) -> tuple[str, str]:
    """Render title and body for setup persistent notification."""
    pn = texts.get("persistent_notification", {}) if isinstance(texts, dict) else {}

    title = str(pn.get("title", "✅ Smart Heating Advisor — Ready"))
    template = str(
        pn.get(
            "setup_message_template",
            "Smart Heating Advisor is configured and ready.\n\n{bp_msg}",
        )
    )

    return title, _safe_format(template, bp_msg=bp_msg)
