"""Analysis logic for Smart Heating Advisor."""
import logging
import re
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


def get_season(month: int) -> str:
    """Return season name based on month."""
    if month in (12, 1, 2):
        return "winter"
    elif month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    else:
        return "autumn"


def extract_temp_from_schedule_name(name: str, fallback: float = 21.0) -> float:
    """Extract target temperature from schedule helper name.

    Looks for a number followed by C at the end of the name.
    Examples:
        "Morning Shower 26C" → 26.0
        "Evening Bath 28C"   → 28.0
        "Morning"            → fallback
    """
    match = re.search(r"(\d+(?:\.\d+)?)C$", name, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return fallback


def analyze_heating_sessions(
    readings: list[tuple],
    schedules: list[dict] | None = None,
    fallback_temp: float = 21.0,
) -> dict:
    """Analyze temperature readings to extract heating session statistics.

    Args:
        readings: list of (datetime, float) temperature tuples
        schedules: list of dicts with keys: name, next_event, target_temp
        fallback_temp: default target temp if no schedules provided

    Returns dict with sessions, avg_rate, success_rate, avg_start_time.
    """
    if len(readings) < 4:
        return {
            "sessions": [],
            "avg_rate": None,
            "success_rate": 0,
            "avg_start_time": None,
            "days_analyzed": 0,
        }

    # Determine target temperatures from schedules if available
    # Build a list of (target_temp, target_time_str) per schedule
    schedule_targets = []
    if schedules:
        for s in schedules:
            temp = extract_temp_from_schedule_name(
                s.get("name", ""), fallback_temp
            )
            schedule_targets.append(temp)
    if not schedule_targets:
        schedule_targets = [fallback_temp]

    # Use the most common (or highest) target temp for session analysis
    primary_target = max(schedule_targets)

    sessions = []
    session_start = None
    session_start_temp = None
    consecutive_rises = 0

    for i in range(1, len(readings)):
        prev_time, prev_temp = readings[i - 1]
        curr_time, curr_temp = readings[i]

        # Skip large gaps — sparse data tolerance 60 min
        time_gap = (curr_time - prev_time).total_seconds() / 60
        if time_gap > 60:
            consecutive_rises = 0
            session_start = None
            continue

        temp_rise = curr_temp - prev_temp

        if temp_rise > 0.1:
            consecutive_rises += 1
            if consecutive_rises == 2:
                session_start = readings[i - 2][0]
                session_start_temp = readings[i - 2][1]
        else:
            if session_start is not None and consecutive_rises >= 4:
                session_end = curr_time
                session_end_temp = curr_temp
                duration_min = (
                    session_end - session_start
                ).total_seconds() / 60
                temp_gained = session_end_temp - session_start_temp

                if duration_min > 5 and temp_gained > 1.0:
                    rate = temp_gained / duration_min
                    target_reached = session_end_temp >= primary_target

                    sessions.append(
                        {
                            "date": session_start.strftime("%Y-%m-%d"),
                            "start_time": session_start.strftime("%H:%M"),
                            "start_temp": round(session_start_temp, 1),
                            "end_temp": round(session_end_temp, 1),
                            "target_temp": primary_target,
                            "duration_min": round(duration_min, 0),
                            "rate": round(rate, 3),
                            "target_reached": target_reached,
                        }
                    )

            consecutive_rises = 0
            session_start = None
            session_start_temp = None

    if not sessions:
        return {
            "sessions": [],
            "avg_rate": None,
            "success_rate": 0,
            "avg_start_time": None,
            "days_analyzed": 0,
        }

    rates = [s["rate"] for s in sessions]
    avg_rate = sum(rates) / len(rates)
    success_count = sum(1 for s in sessions if s["target_reached"])
    success_rate = (success_count / len(sessions)) * 100

    start_minutes = []
    for s in sessions:
        h, m = map(int, s["start_time"].split(":"))
        start_minutes.append(h * 60 + m)
    avg_start_min = sum(start_minutes) / len(start_minutes)
    avg_start_time = (
        f"{int(avg_start_min // 60):02d}:{int(avg_start_min % 60):02d}"
    )

    return {
        "sessions": sessions[-7:],
        "avg_rate": round(avg_rate, 3),
        "success_rate": round(success_rate, 1),
        "avg_start_time": avg_start_time,
        "days_analyzed": len(sessions),
    }


def build_daily_prompt(
    room_name: str,
    current_rate: float,
    analysis: dict,
    schedules: list[dict],
    outside_temp: float,
    tomorrow_min: float,
    tomorrow_max: float,
    season: str,
    fallback_temp: float = 21.0,
) -> str:
    """Build the daily analysis prompt for Ollama."""

    # Build schedule summary
    schedule_lines = ""
    for s in schedules:
        temp = extract_temp_from_schedule_name(s.get("name", ""), fallback_temp)
        schedule_lines += f"  - {s.get('name', 'Unknown')}: target {temp}°C\n"
    if not schedule_lines:
        schedule_lines = f"  - Default: target {fallback_temp}°C\n"

    # Build session summary
    sessions_text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s["target_reached"] else "❌"
        sessions_text += (
            f"  - {s['date']} {reached}: started {s['start_time']} "
            f"at {s['start_temp']}°C, reached {s['end_temp']}°C "
            f"in {s['duration_min']:.0f} min "
            f"(rate: {s['rate']}°C/min, target: {s['target_temp']}°C)\n"
        )
    if not sessions_text:
        sessions_text = "  No heating sessions found in last 7 days.\n"

    prompt = f"""You are a smart home heating advisor. Analyze heating data for the {room_name} and suggest an optimal heating rate.

## Room: {room_name}
## Current heating_rate: {current_rate} °C/min

## Active Schedules
{schedule_lines}

## Last 7 Days Heating Sessions
- Average heating rate observed: {analysis.get('avg_rate', 'unknown')} °C/min
- Success rate (target reached): {analysis.get('success_rate', 0)}%
- Average pre-heat start time: {analysis.get('avg_start_time', 'unknown')}
- Sessions analyzed: {analysis.get('days_analyzed', 0)}

Daily breakdown:
{sessions_text}

## Weather Context
- Current outside temperature: {outside_temp}°C
- Tomorrow forecast: min {tomorrow_min}°C / max {tomorrow_max}°C
- Season: {season}

## Your Task
1. If success rate is below 80% → increase heating_rate (heats up faster)
2. If heating consistently starts more than 45 min before schedule → decrease heating_rate (too conservative)
3. Consider outside temperature: colder outside = may need higher rate
4. Keep heating_rate between 0.05 and 0.30
5. Only suggest meaningful changes (>0.01 difference)

Respond ONLY with a valid JSON object, no other text:
{{
  "heating_rate": 0.13,
  "reasoning": "brief explanation under 100 words",
  "confidence": "high"
}}"""

    return prompt


def build_weekly_prompt(
    room_name: str,
    current_rate: float,
    analysis: dict,
    schedules: list[dict],
    avg_outside_temp: float,
    season: str,
    fallback_temp: float = 21.0,
) -> str:
    """Build the weekly deep analysis prompt for Ollama."""

    schedule_lines = ""
    for s in schedules:
        temp = extract_temp_from_schedule_name(s.get("name", ""), fallback_temp)
        schedule_lines += f"  - {s.get('name', 'Unknown')}: target {temp}°C\n"
    if not schedule_lines:
        schedule_lines = f"  - Default: target {fallback_temp}°C\n"

    sessions_text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s["target_reached"] else "❌"
        sessions_text += (
            f"  - {s['date']} {reached}: {s['start_temp']}°C → "
            f"{s['end_temp']}°C, rate: {s['rate']}°C/min\n"
        )

    prompt = f"""You are a smart home heating advisor doing a weekly deep analysis for the {room_name}.

## Room: {room_name}
## Current heating_rate: {current_rate} °C/min

## Active Schedules
{schedule_lines}

## Last 30 Days Performance
- Sessions analyzed: {analysis.get('days_analyzed', 0)}
- Average heating rate observed: {analysis.get('avg_rate', 'unknown')} °C/min
- Overall success rate: {analysis.get('success_rate', 0)}%
- Average pre-heat start time: {analysis.get('avg_start_time', 'unknown')}
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
{{
  "heating_rate": 0.13,
  "reasoning": "detailed explanation under 200 words",
  "confidence": "high",
  "weekly_report": "2-3 sentence summary for the homeowner"
}}"""

    return prompt
