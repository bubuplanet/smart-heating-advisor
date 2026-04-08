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