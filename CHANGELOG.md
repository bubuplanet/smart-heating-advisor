# Changelog

All notable changes to this project will be documented in this file.

## v0.0.2

### Added
- Per-room daily and weekly persistent analysis reports in Home Assistant.
- Per-room toggles for daily/weekly report notifications.
- Per-room runtime notification enable switches (preheat, target, standby, window, override).
- Externalized setup notification text templates via JSON (`messages.json`) with optional user overrides.
- Verbose debug logging controls and expanded diagnostics.

### Changed
- Moved room discovery to a persistent room registry model.
- Updated blueprint onboarding flow: create automation, run once to register room, then reload integration.
- Improved setup notification content with clearer step-by-step guidance.
- Improved switch labels and explanatory attributes for better UX.

### Fixed
- Fixed room discovery for section-based blueprint inputs.
- Fixed discovery reliability across startup/loading timing scenarios.
- Fixed debug logger scope for integration modules.
- Fixed override event filtering and number entity restore behavior.
- Applied Home Assistant developer compliance updates across manifest, config flow, sensors, services, and translations.
- Miscellaneous README and documentation corrections.
