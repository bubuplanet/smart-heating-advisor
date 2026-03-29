"""Analysis logic for Smart Heating Advisor."""
import logging
from datetime import datetime, timedelta

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


def analyze_heating_sessions(readings: list[tuple]) -> dict:
    """
    Analyze temperature readings to extract heating session statistics.
    Returns dict with sessions, avg_rate, success_rate, avg_start_time.
    """
    if len(readings) < 4:
        return {
            "sessions": [],
            "avg_rate": None,
            "success_rate": 0,
            "avg_start_time": None,
            "days_analyzed": 0
        }

    sessions = []
    session_start = None
    session_start_temp = None
    consecutive_rises = 0

    for i in range(1, len(readings)):
        prev_time, prev_temp = readings[i - 1]
        curr_time, curr_temp = readings[i]

        # Skip large gaps (data missing)
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
                duration_min = (session_end - session_start).total_seconds() / 60
                temp_gained = session_end_temp - session_start_temp

                if duration_min > 5 and temp_gained > 1.0:
                    rate = temp_gained / duration_min
                    # Check if target (26°C) was reached
                    target_reached = session_end_temp >= 26
                    # Check if reached before 6AM
                    reached_before_6am = (
                        session_end.hour < 6 or
                        (session_end.hour == 6 and session_end.minute == 0)
                    )

                    sessions.append({
                        "date": session_start.strftime("%Y-%m-%d"),
                        "start_time": session_start.strftime("%H:%M"),
                        "start_temp": round(session_start_temp, 1),
                        "end_temp": round(session_end_temp, 1),
                        "duration_min": round(duration_min, 0),
                        "rate": round(rate, 3),
                        "target_reached": target_reached,
                        "reached_before_6am": reached_before_6am
                    })

            consecutive_rises = 0
            session_start = None
            session_start_temp = None

    # Calculate statistics
    if not sessions:
        return {
            "sessions": [],
            "avg_rate": None,
            "success_rate": 0,
            "avg_start_time": None,
            "days_analyzed": 0
        }

    rates = [s["rate"] for s in sessions]
    avg_rate = sum(rates) / len(rates)
    success_count = sum(1 for s in sessions if s["reached_before_6am"])
    success_rate = (success_count / len(sessions)) * 100

    # Average start time in minutes from midnight
    start_minutes = []
    for s in sessions:
        h, m = map(int, s["start_time"].split(":"))
        start_minutes.append(h * 60 + m)
    avg_start_min = sum(start_minutes) / len(start_minutes)
    avg_start_time = f"{int(avg_start_min // 60):02d}:{int(avg_start_min % 60):02d}"

    return {
        "sessions": sessions[-7:],  # Last 7 sessions for context
        "avg_rate": round(avg_rate, 3),
        "success_rate": round(success_rate, 1),
        "avg_start_time": avg_start_time,
        "days_analyzed": len(sessions)
    }


def build_daily_prompt(
    current_rate: float,
    analysis: dict,
    outside_temp: float,
    tomorrow_min: float,
    tomorrow_max: float,
    season: str
) -> str:
    """Build the daily analysis prompt for Ollama."""

    sessions_text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s["reached_before_6am"] else "❌"
        sessions_text += (
            f"  - {s['date']} {reached}: started {s['start_time']} "
            f"at {s['start_temp']}°C, reached {s['end_temp']}°C "
            f"in {s['duration_min']:.0f} min "
            f"(rate: {s['rate']}°C/min)\n"
        )

    if not sessions_text:
        sessions_text = "  No heating sessions found in last 7 days."

    prompt = f"""You are a smart home heating advisor. Analyze bathroom heating data and suggest an optimal heating rate.

## Current Settings
- Current heating_rate: {current_rate} °C/min
- Room target: 26°C
- Target time: 06:00 AM
- Season: {season}

## Last 7 Days Heating Sessions
- Average heating rate: {analysis.get('avg_rate', 'unknown')} °C/min
- Success rate (target reached before 6AM): {analysis.get('success_rate', 0)}%
- Average pre-heat start time: {analysis.get('avg_start_time', 'unknown')}
- Sessions analyzed: {analysis.get('days_analyzed', 0)}

Daily breakdown:
{sessions_text}

## Weather Context
- Current outside temperature: {outside_temp}°C
- Tomorrow forecast: min {tomorrow_min}°C / max {tomorrow_max}°C
- Season: {season}

## Your Task
1. If success rate is below 80% → increase heating_rate (heat up faster)
2. If heating starts more than 45 min before 6AM consistently → decrease heating_rate (too conservative)
3. Consider outside temperature: colder outside = slower heating = may need higher rate
4. Keep heating_rate between 0.05 and 0.30

Respond ONLY with a valid JSON object, no other text:
{{
  "heating_rate": 0.13,
  "reasoning": "brief explanation under 100 words",
  "confidence": "high"
}}"""

    return prompt


def build_weekly_prompt(
    current_rate: float,
    analysis: dict,
    avg_outside_temp: float,
    season: str
) -> str:
    """Build the weekly deep analysis prompt for Ollama."""

    sessions_text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s["reached_before_6am"] else "❌"
        sessions_text += (
            f"  - {s['date']} {reached}: {s['start_temp']}°C → "
            f"{s['end_temp']}°C, rate: {s['rate']}°C/min\n"
        )

    prompt = f"""You are a smart home heating advisor doing a weekly deep analysis.

## Current Settings
- heating_rate: {current_rate} °C/min
- Target: 26°C by 06:00 AM
- Season: {season}

## Last 30 Days Performance
- Sessions analyzed: {analysis.get('days_analyzed', 0)}
- Average heating rate observed: {analysis.get('avg_rate', 'unknown')} °C/min
- Overall success rate: {analysis.get('success_rate', 0)}%
- Average pre-heat start time: {analysis.get('avg_start_time', 'unknown')}
- Average outside temperature: {avg_outside_temp}°C

Recent sessions:
{sessions_text}

## Your Task
Provide a comprehensive weekly assessment:
1. Was the heating rate appropriate this week?
2. Any patterns noticed (e.g. worse on very cold days)?
3. Recommended heating_rate adjustment
4. Confidence level

Respond ONLY with a valid JSON object:
{{
  "heating_rate": 0.13,
  "reasoning": "detailed explanation under 200 words",
  "confidence": "high",
  "weekly_report": "2-3 sentence summary for the homeowner"
}}"""

    return prompt