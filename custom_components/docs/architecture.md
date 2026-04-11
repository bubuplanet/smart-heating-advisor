```markdown
# SHA — Architecture Decisions

Read this before making significant changes to the integration.
Each decision records what was chosen, why, and what was rejected.

---

## Room discovery — SubEntry pattern

Date: 2026-04
Decision: Rooms are HA SubEntries under the single SHA config entry.
Reason: Blueprint-driven registration was a chicken-and-egg problem.
Entities did not exist until the blueprint ran but the blueprint
needed entities to already exist. SubEntries create entities at
integration setup time before any automation runs.
Rejected: Scanning automation configs — fragile, version dependent.

---

## Blueprint defaults vs messages.md

Date: 2026-04
Decision: Notification template defaults stay in blueprint YAML.
messages.md is only for coordinator Python-side notifications.
Reason: HA blueprints are static YAML. Default values are injected
at automation creation time not at runtime. Dynamic generation of
blueprint YAML from messages.md would be fragile, break blueprint
versioning and confuse users who edit blueprints manually.

---

## Heating rate — number entity not input_number

Date: 2026-04
Decision: SHA creates number.sha_ROOM_heating_rate as a custom
number entity. Does not use HA input_number helpers.
Reason: input_number.create service does not exist in HA.
Custom number entities via async_add_entities is the correct
HA pattern for integrations that need writable helpers.

---

## No input_number or input_boolean helpers in blueprint

Date: 2026-04
Decision: The blueprint reads ALL user-configurable values
directly from !input blueprint inputs. It never reads from
input_number.sha_* or input_boolean.sha_* helpers at runtime.
Reason: The old architecture created input_number helpers as
intermediaries between the automation UI and the blueprint
variables section. This caused silent failures when helpers
did not exist or returned unknown. The new architecture is:

  User sets value in automation UI
  → Blueprint reads !input value directly
  → Climate or notify action uses the value

The only SHA-created entities the blueprint reads at runtime are:
  number.sha_ROOM_heating_rate — read for pre-heat calculation
  switch.sha_ROOM_* — read for notification dedup logic
  sensor.sha_ROOM_* — written by coordinator, read by dashboard

Any variable in the blueprint variables section that reads from
input_number.sha_* is a leftover from the old architecture and
must be removed.
Rejected: input_number helpers as intermediaries — caused silent
zero-value failures when entity was missing or unavailable.

---

## Blueprint variable shadowing — never redefine input names

Date: 2026-04
Decision: Blueprint variables must never use the same name as
a blueprint input. If a variable in the variables section has
the same name as a !input it overwrites the input value for
the rest of the automation run.
Reason: Discovered in production — fixed_radiator_temperature
was defined both as a !input (value: 35) and as a variable
that read from a non-existent input_number helper (value: 0).
The variable overwrote the input causing the towel rail to be
commanded to 0°C silently.
Rule: if a blueprint input value needs transformation define
the variable with a different name e.g. fixed_radiator_temp_f
and use | float(fallback) for safe conversion.

---

## Fixed vs main radiator thermostat separation

Date: 2026-04
Decision: The blueprint separates TRVs into two groups with
independent control logic:
  radiator_thermostats — main radiators that follow the
    schedule comfort temperature (e.g. 26°C)
  fixed_radiator_thermostats — devices always heated to a
    fixed temperature (e.g. towel rails at 35°C)

Both groups are commanded in all heating phases:
  pre-heat: main TRVs at preheat_temp, fixed TRVs at fixed_temp
  comfort: main TRVs at comfort_temp, fixed TRVs at fixed_temp
  window close resume: both groups commanded on resume
  window open / schedule end: both groups commanded off

Reason: Towel rails must maintain a fixed temperature for
comfort regardless of the room schedule target. Mixing them
with main radiators would command them to the schedule
temperature which is incorrect.
Rule: users must not add fixed-temperature devices to
radiator_thermostats — they belong in fixed_radiator_thermostats.

---

## Notification dedup — independent switches per notification type

Date: 2026-04
Decision: Each notification type has its own dedicated switch:
  switch.sha_ROOM_preheat_notified
  switch.sha_ROOM_schedule_notified  (Starting Comfort Phase)
  switch.sha_ROOM_target_notified    (Target Reached)
  switch.sha_ROOM_standby_notified
  switch.sha_ROOM_window_timeout_notified
  switch.sha_ROOM_vacation_notified

Each switch is turned ON as an independent action OUTSIDE the
notification if/then block so it fires regardless of whether
the notification was suppressed by an enabled toggle.
Each switch is turned OFF only when the corresponding event ends
(schedule OFF resets schedule_notified and target_notified).
Reason: Original design nested the turn_on inside the
notification block causing dedup to fail when the notification
was suppressed. Discovered in dry-run and confirmed in production.
Rejected: Shared switch across multiple notification types —
caused Target Reached to be permanently blocked by Schedule
Started (Bug B1 in dry-run).

---

## Window detection — explicit TRV commands not variable-based

Date: 2026-04
Decision: The window_airing_start sequence explicitly commands
all TRVs off immediately — it does not rely on the target_mode
variable or the control_loop default branch to apply the off
command on the next 5-minute tick.
Reason: Blueprint variables are evaluated once at automation
start before any actions run. When window_airing_start fires
and turns on airing_mode the target_mode variable is already
stale (evaluated before the switch changed). Relying on the
next control loop run means up to 5 minutes of unwanted heating
after the window opens.
Rule: any branch that changes heating state must include
explicit climate actions — never rely on stale variables or
the next control loop tick.

---

## Weekly analysis — report only, no auto rate change

Date: 2026-04
Decision: Weekly analysis produces a persistent notification
but does NOT automatically update the heating rate.
Reason: Automatic weekly changes combined with daily automatic
changes could cause instability. User reviews the suggestion
and applies manually if agreed.

---

## room_id derivation — must be identical in Python and blueprint

Date: 2026-04
Decision: room_id derived by:
  lowercase → remove apostrophes → spaces/hyphens to underscores
  → strip all non [a-z0-9_] characters

This logic must be identical in:
  coordinator.py: _room_name_to_id()
  blueprint: room_id variable with regex_replace filter

Any divergence causes entity ID mismatches where the blueprint
writes to entities that SHA never created.

---

## InfluxDB query — no aggregateWindow

Date: 2026-04
Decision: Flux query does not use aggregateWindow.
Reason: Temperature readings are sparse (every 30-60 min).
aggregateWindow creates empty buckets and distorts session
detection. Raw sorted readings with 60 min gap tolerance
gives more accurate heating session analysis.

---

## Blueprint Jinja2 — no strftime filter

Date: 2026-04
Decision: Never use | strftime in blueprint templates.
HA Jinja2 does not support strftime as a filter.
Use .strftime() as a method on datetime objects instead:

  Correct:   {{ (as_datetime(nev) | as_local).strftime('%H:%M') }}
  Incorrect: {{ as_datetime(nev) | as_local | strftime('%H:%M') }}

Reason: Discovered in production — strftime filter caused a
TemplateRuntimeError that aborted the entire schedule_changed
ON branch silently, preventing Starting Comfort Phase
notification and schedule_notified switch from being set.

---

## Entity naming — friendly name drives entity ID

Date: 2026-04
Decision: The friendly name defined in switch.py boolean_defs
directly determines the HA entity ID via HA slug generation.
The entity ID must match exactly what the blueprint references.

Example of the failure mode:
  Friendly name: "Airing Mode (Window Pause)"
  Generated ID:  switch.sha_ROOM_airing_mode_window_pause
  Blueprint expects: switch.sha_ROOM_airing_mode
  Result: all window detection silently fails

Rule: before adding a new entity verify the expected entity ID
by checking what HA slug generation will produce from the
friendly name. Keep friendly names short and without
parenthetical suffixes to avoid unexpected slug expansion.
```