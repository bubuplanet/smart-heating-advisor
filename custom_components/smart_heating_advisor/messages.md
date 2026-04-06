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
Smart Heating Advisor is configured and ready.

{bp_msg}

Setup checklist:
1. Go to Settings -> Automations -> Blueprints
2. Open Smart Heating Advisor blueprint
3. Create one automation per room
4. Use schedule names that end with temperature (example: Morning Shower 26C)
5. Run each new room automation once (this registers the room in SHA)
6. Reload Smart Heating Advisor integration to create room entities
7. Optional check: Developer Tools -> States and search for sha_
   You should see entities like number.sha_room_heating_rate and switch.sha_room_override

Notes:
- If you change schedules later: save automation, run once, then reload SHA
- Upgrading from older versions: open and re-save each room automation so it uses the latest blueprint

Daily AI analysis runs at 00:01
Weekly report runs Sunday at 01:00
