# SHA — Weekly Analysis Prompt
# Variables: {room_name}, {target_comfort_temp}, {target_ready_time},
#            {sessions_total}, {sessions_on_target},
#            {avg_comfort_at_ready}, {avg_miss},
#            {consistent_miss}, {root_cause},
#            {outside_temp_now}, {season},
#            {session_detail_table},
#            {schedule_count}, {schedule_lines},
#            {avg_heating_rate}, {avg_gradient}, {success_rate_pct},
#            {schedules_analysis_text}, {humidity_analysis_text},
#            {humidity_sensor}
# Called by: coordinator.py _async_run_weekly_analysis_for_room

## SHA Weekly Performance Report
## Room: {room_name}
## Analysis: last 30 days

Target: {target_comfort_temp}°C by {target_ready_time}
Sessions analysed: {sessions_total}
Target reached: {sessions_on_target} of {sessions_total} ({success_rate_pct}%)
Average temperature at ready time: {avg_comfort_at_ready}°C
Average miss: {avg_miss}°C
Consistent miss: {consistent_miss}
Root cause identified: {root_cause}

Outside temperature this period: {outside_temp_now}°C
Season: {season}

## Active Schedules ({schedule_count})
{schedule_lines}

## Per-Schedule Accuracy
{schedules_analysis_text}

## Humidity Context
{humidity_analysis_text}

## Session detail (last 5 sessions)
{session_detail_table}

## Your task

Write a plain language weekly report for the homeowner covering this room.
Maximum 200 words.

The report must:
1. State clearly whether the room is performing well or not in simple terms
   ("Your bathroom reached target temperature on X of Y mornings this month")
2. If performing well — confirm and note any trend (improving or stable)
3. If not performing well — explain the most likely reason in simple language.
   NO technical jargon.
   ("The bathroom heater appears to be too small to heat the room to 26°C within
   a reasonable time" not "heating_rate is below threshold")
4. Make exactly ONE concrete suggestion if target is not being reached
5. Note if SHA is already self-correcting
   (daily analysis has been adjusting settings)

If root_cause is HARDWARE_INSUFFICIENT:
  Be direct — tell the user the heater cannot physically reach the target
  temperature in time. Suggest lowering the target or adding heating.

If root_cause is PREHEAT_TOO_SHORT:
  Tell the user SHA has already identified the issue and is automatically
  adjusting the pre-heat start time each day. The user does not need to do
  anything. SHA will continue improving accuracy automatically as it learns
  the room. Do NOT suggest any manual action.

If root_cause is TRV_SETPOINT_TOO_LOW:
  Tell the user SHA has identified the radiator needs to run hotter and has
  adjusted its settings.

If root_cause is HEAT_LOSS_HIGH:
  Suggest checking windows and insulation.

If root_cause is RECENT_DEGRADATION:
  Note something may have changed recently and ask the user to check the room.

If root_cause is none and consistent_miss is No:
  Focus on confirming performance is good and note any positive trend.

Respond ONLY with a valid JSON object — no explanation text outside the JSON:
{
  "performance": "good",
  "root_cause": "none",
  "confidence": "high",
  "report": "plain language report max 200 words"
}

performance must be one of: "good", "poor", "improving"
root_cause must match the root_cause above, or "none" if not applicable.
confidence must be one of: "low", "medium", "high"
