# SHA — Weekly Analysis Prompt
# Variables: {room_name}, {current_rate}, {schedule_lines}, {days_analyzed}, {avg_rate},
#            {success_rate}, {avg_start_time}, {avg_outside_temp}, {season}, {sessions_text}
# Called by: coordinator.py _async_run_weekly_analysis_for_room

You are a smart home heating advisor doing a weekly deep analysis for the {room_name}.

## Room: {room_name}
## Current heating_rate: {current_rate} °C/min

## Active Schedules
{schedule_lines}

## Last 30 Days Performance
- Sessions analyzed: {days_analyzed}
- Average heating rate observed: {avg_rate} °C/min
- Overall success rate: {success_rate}%
- Average pre-heat start time: {avg_start_time}
- Average outside temperature: {avg_outside_temp}°C
- Season: {season}

Recent sessions:
{sessions_text}

## Your Task
Provide a comprehensive weekly assessment:
1. Was the heating rate appropriate this week?
2. Any patterns noticed (e.g. worse on very cold days)?
3. Recommended heating_rate adjustment
4. Confidence level
5. A 2-3 sentence summary for the homeowner

Respond ONLY with a valid JSON object:
{
  "heating_rate": 0.13,
  "reasoning": "detailed explanation under 200 words",
  "confidence": "high",
  "weekly_report": "2-3 sentence summary for the homeowner"
}
