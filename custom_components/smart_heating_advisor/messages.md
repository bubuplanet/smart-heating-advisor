# persistent_notification

## title
✅ Smart Heating Advisor — Ready

## bp_installed
✅ Blueprint v{source_ver} installed automatically.

## bp_updated
🔄 Blueprint updated from v{dest_ver} to v{source_ver}.
Backup saved as {backup_name}.
Existing automations continue working — re-save to use new features.

## bp_skipped
✅ Blueprint v{source_ver} already up to date.

## bp_error
⚠️ Blueprint could not be installed automatically.
Import it manually using the magic link in the README.

## setup_message_template
Smart Heating Advisor is configured and ready to control your heating.

**How SHA works:**
SHA uses AI to learn how your home heats. Every day SHA analyses your heating sessions and automatically adjusts your radiator settings to reach the right temperature at the right time. Every week SHA sends you a plain language report on how each room is performing and flags any issues it has detected.

Each room you configure gets its own automation named SHA — {{room_name}}. SHA uses this automation to control your radiator thermostats and pre-heat rooms before your schedules start.

**Setup steps:**
1. Add your rooms using the + Add Room button on the integration card
2. For each room select your temperature sensor, radiator thermostats and schedules
3. SHA creates and enables the automation SHA — {{room_name}} automatically — no further setup needed

**To manage your rooms:**
- Add a room: Integration card → + Add Room
- Edit a room: Integration card → room name → Reconfigure
- Change schedules: Integration card → room name → Reconfigure → Step 2

Daily AI analysis runs at 00:01
Weekly report runs Sunday at 01:00
