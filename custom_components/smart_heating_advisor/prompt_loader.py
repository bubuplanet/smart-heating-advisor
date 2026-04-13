"""Prompt file loader for Smart Heating Advisor.

Loads Ollama prompt templates from markdown files and substitutes
{variable_name} placeholders with actual values.

Search order for each prompt file:
  1. /config/smart_heating_advisor/prompts/<filename>  (user override)
  2. <integration_dir>/prompts/<filename>              (bundled fallback)

User-editable copies are installed to /config/smart_heating_advisor/prompts/
on first setup. Edit those files to customise prompts without touching the
integration source.

Placeholder syntax: {variable_name}
Only identifiers that match a key in the supplied variables dict are
substituted. Literal { and } (e.g. in JSON examples inside the prompt)
are left unchanged because they do not follow the {word} pattern.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Bundled prompts — shipped inside the integration package
_BUNDLED_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Sub-path under /config where user-editable copies live
_USER_PROMPTS_SUBDIR = "smart_heating_advisor/prompts"

# Pattern that matches {identifier} placeholders only
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_]\w*)\}")


def load_prompt(
    filename: str,
    variables: dict[str, object],
    config_dir: str | None = None,
) -> str:
    """Load a prompt file and substitute {variable} placeholders.

    Args:
        filename:   Prompt filename, e.g. ``"daily_analysis.md"``.
        variables:  Mapping of variable names to their string values.
        config_dir: HA config directory path used to locate user overrides.
                    Pass ``hass.config.config_dir``. May be ``None`` in tests.

    Returns:
        The completed prompt string, ready to send to Ollama.

    Raises:
        FileNotFoundError: If the file is not found in either location.
    """
    # 1. User override — only used when a config dir is provided and the file exists
    if config_dir:
        user_path = Path(config_dir) / _USER_PROMPTS_SUBDIR / filename
        if user_path.exists():
            _LOGGER.debug("Prompt loader: using user override — %s", user_path)
            template = user_path.read_text(encoding="utf-8")
            return _substitute(template, variables, user_path.name)

    # 2. Bundled fallback
    bundled_path = _BUNDLED_PROMPTS_DIR / filename
    if bundled_path.exists():
        _LOGGER.debug("Prompt loader: loaded bundled prompt — %s", filename)
        template = bundled_path.read_text(encoding="utf-8")
        return _substitute(template, variables, filename)

    checked = []
    if config_dir:
        checked.append(str(Path(config_dir) / _USER_PROMPTS_SUBDIR / filename))
    checked.append(str(bundled_path))
    raise FileNotFoundError(
        f"SHA prompt file '{filename}' not found. Checked: {', '.join(checked)}"
    )


def _substitute(template: str, variables: dict[str, object], source_name: str) -> str:
    """Replace {identifier} placeholders with values from *variables*.

    Placeholders whose key is absent from *variables* are left unchanged and
    logged as a warning so prompt authors notice missing data.
    """
    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        _LOGGER.warning(
            "Prompt loader [%s]: unknown placeholder '{%s}' — leaving unchanged",
            source_name,
            key,
        )
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template)
