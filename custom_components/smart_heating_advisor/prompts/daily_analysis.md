# SHA — Daily Analysis Prompt
# Variables: {room_name}, {heating_rate}, {analysis_days},
#            {schedule_count}, {schedule_lines},
#            {schedules_analysis_text}, {humidity_analysis_text},
#            {trv_entities}, {trv_count}, {standby_temp}, {all_trvs_active_since},
#            {full_setup_count}, {partial_setup_count},
#            {session_count}, {on_target_count}, {avg_observed_rate},
#            {outside_temp}, {tomorrow_min}, {tomorrow_max}, {season},
#            {learning_phase}, {sessions_so_far}, {humidity_sensor},
#            {target_comfort_temp}, {trv_max_temp}, {current_trv_setpoint},
#            {avg_gradient}, {recommended_trv_setpoint}
# Called by: coordinator.py _async_run_daily_analysis_for_room
#
# heating_rate: how fast the comfort sensor (room thermostat near door) rises
#   in °C/min from pre-heat start to schedule ON time.
#   Formula: (comfort_temp_at_schedule_on - comfort_temp_at_session_start) / preheat_min
#
# trv_setpoint: the temperature SHA commands the TRV to.
#   gradient = TRV setpoint during session - comfort sensor at schedule ON time
#   recommended = target_comfort_temp + avg_gradient, rounded to 0.5°C, capped at trv_max_temp

You are a smart home heating advisor. Evaluate whether the pre-heat timing in
{room_name} is achieving the target comfort temperature on schedule, and recommend
corrections to both the heating rate and the TRV setpoint if needed.

## Room: {room_name}
## Analysis period: last {analysis_days} days

## Active Schedules ({schedule_count})
{schedule_lines}

## TRV Configuration
- TRV entities ({trv_count}): {trv_entities}
- TRV hardware max temperature: {trv_max_temp}°C
- Detected standby setpoint: {standby_temp}°C
- Reliable data from: {all_trvs_active_since}
  (sessions before this date have partial TRV coverage — included for pattern detection only)

## TRV Setup History
- Full setup (all TRVs active) since: {all_trvs_active_since}
- Full setup sessions: {full_setup_count}
- Partial setup sessions (incomplete TRV coverage): {partial_setup_count}

Note: Sessions before {all_trvs_active_since} had partial TRV coverage.
Use only full setup sessions for heating rate and accuracy calculations.
Use all sessions for usage pattern detection.

## Learning Phase
- Learning phase: {learning_phase}
- Sessions so far: {sessions_so_far}
- Humidity sensor: {humidity_sensor}

## Current SHA Settings
- Current heating rate: {heating_rate}°C/min
- Current TRV setpoint: {current_trv_setpoint}°C
- Target comfort temperature: {target_comfort_temp}°C

## Observed Performance
- Observed average heating rate (comfort sensor): {avg_observed_rate}°C/min
- Observed average gradient (TRV setpoint − comfort at schedule time): {avg_gradient}°C
- Recommended TRV setpoint: {recommended_trv_setpoint}°C
  (= target_comfort_temp {target_comfort_temp}°C + avg_gradient {avg_gradient}°C,
   rounded to 0.5°C, capped at TRV max {trv_max_temp}°C)

## Per-Schedule Heating Accuracy
{schedules_analysis_text}

## Humidity Context
{humidity_analysis_text}

## Weather Context
- Current outside temperature: {outside_temp}°C
- Tomorrow forecast: min {tomorrow_min}°C / max {tomorrow_max}°C
- Season: {season}

## Reasoning Instructions

### heating_rate
1. If average miss > 0.5°C or more than half of sessions missed target:
   the heating rate is too low — room not reaching target on time.
   Increase heating_rate to compensate.
2. If average miss < −1.0°C (consistent overshoot):
   the heating rate is too high — pre-heat starts too early.
   Decrease heating_rate.
3. If consecutive_misses ≥ 3: treat as urgent — increase rate more aggressively.
4. If miss trend is "worsening": bias toward increasing rate even if miss is moderate.
5. If miss trend is "improving": a smaller or no adjustment may suffice.
6. Keep heating_rate between 0.05 and 0.30.
7. Only suggest a change when the adjustment is > 0.01°C/min.
8. Consider outside temperature: colder outside generally requires a higher rate.
9. If learning_phase is True or session_count < 3:
   set confidence to "low" and note that data is limited.
   However always set heating_rate to the observed real heating rate from the
   session data regardless of learning phase. The rate must reflect reality
   even with limited data — it is better to use an observed rate with low
   confidence than to keep an unobserved default rate.
   If avg_observed_rate is available use it directly as the recommended heating_rate.

### trv_setpoint
10. If avg_gradient is available (not "n/a"):
    set trv_setpoint = target_comfort_temp + avg_gradient, rounded to nearest 0.5°C,
    capped at trv_max_temp.
    Example: target=26°C, gradient=2.8°C → raw=28.8°C → rounded=29.0°C → capped at {trv_max_temp}°C.
11. If avg_gradient is "n/a" (no valid session data):
    keep trv_setpoint = current_trv_setpoint unchanged.
12. Never recommend trv_setpoint above {trv_max_temp}°C (TRV hardware limit).
13. If the recommended TRV setpoint differs from the current by less than 0.5°C,
    keep the current value unchanged (within TRV step resolution).

### General
14. If any schedule shows recommended_preheat_min > 180:
    note in daily_summary that the radiator may be underpowered for the room size.
15. If humidity_sensor is configured (not "not configured"):
    confirm in daily_summary whether humidity peaks suggest the room is being used
    as expected (e.g. shower use, cooking). Ignore humidity if sensor not configured.

Respond ONLY with a valid JSON object — no explanation text outside the JSON:
{
  "heating_rate": 0.031,
  "trv_setpoint": 29.0,
  "rate_adjustment_reason": "one sentence explaining why the rate was changed or kept",
  "target_accuracy_percent": 75,
  "average_miss_celsius": 1.2,
  "confidence": "high",
  "recommendation": "one concrete action the system will take",
  "daily_summary": "2-3 sentence plain-language summary for the homeowner"
}
