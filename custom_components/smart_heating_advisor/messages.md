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
1. Your rooms are pre-configured from the setup wizard
2. SHA has created disabled blueprint automations for each room
3. Open each room automation (Settings → Automations) and add Schedule helpers
   Example schedule name: "Morning Shower 26C"
4. Enable each automation when ready
5. Optional: Developer Tools → States → search for "sha_" to verify entities

Notes:
- To add or remove rooms later: Settings → Integrations → Smart Heating Advisor → Configure
- If you change schedules later: edit the automation and save
- Upgrading from older versions: open and re-save each room automation so it uses the latest blueprint

Daily AI analysis runs at 00:01
Weekly report runs Sunday at 01:00
