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


def build_schedule_lines(
    schedules: list[dict],
    fallback_temp: float = 21.0,
) -> str:
    """Build the schedule summary block for Ollama prompts."""
    lines = ""
    for s in schedules:
        temp = extract_temp_from_schedule_name(s.get("name", ""), fallback_temp)
        lines += f"  - {s.get('name', 'Unknown')}: target {temp}°C\n"
    if not lines:
        lines = f"  - Default: target {fallback_temp}°C\n"
    return lines


def build_sessions_table(analysis: dict) -> str:
    """Build a tabular per-session accuracy breakdown for the daily prompt.

    Columns: date | start_temp | target_temp | temp_reached | miss | on_target
    ``temp_reached`` is the temperature the room achieved at the end of the
    heating ramp — the closest available proxy for temp at schedule start.
    """
    sessions = analysis.get("sessions", [])
    if not sessions:
        return "  No heating sessions detected.\n"

    header = (
        f"  {'Date':<12} {'Start':>7} {'Target':>7} "
        f"{'Reached':>8} {'Miss':>7}  OK\n"
        f"  {'-'*54}\n"
    )
    rows = ""
    for s in sessions:
        miss = s.get("target_miss", 0.0)
        ok = "✅" if s["target_reached"] else "❌"
        rows += (
            f"  {s['date']:<12} "
            f"{s['start_temp']:>5.1f}°C "
            f"{s['target_temp']:>5.1f}°C "
            f"{s.get('temp_at_schedule_start', s['end_temp']):>6.1f}°C "
            f"{miss:>+6.1f}°C  {ok}\n"
        )
    return header + rows


def build_sessions_text(analysis: dict, weekly: bool = False) -> str:
    """Build the compact heating-sessions block for the weekly prompt."""
    text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s["target_reached"] else "❌"
        if weekly:
            miss = s.get("target_miss", 0.0)
            text += (
                f"  - {s['date']} {reached}: "
                f"{s['start_temp']}°C → {s.get('temp_at_schedule_start', s['end_temp'])}°C "
                f"(target {s['target_temp']}°C, miss {miss:+.1f}°C), "
                f"rate: {s['rate']}°C/min\n"
            )
        else:
            text += (
                f"  - {s['date']} {reached}: started {s['start_time']} "
                f"at {s['start_temp']}°C, reached {s['end_temp']}°C "
                f"in {s['duration_min']:.0f} min "
                f"(rate: {s['rate']}°C/min, target: {s['target_temp']}°C)\n"
            )
    if not text:
        text = "  No heating sessions found.\n"
    return text


def build_weekly_accuracy_summary(analysis: dict) -> str:
    """Build a plain-language weekly accuracy paragraph for the weekly prompt."""
    total = analysis.get("sessions_total", 0)
    on_target = analysis.get("sessions_on_target", 0)
    avg_miss = analysis.get("average_miss", 0.0)
    trend = analysis.get("miss_trend", "stable")
    consecutive = analysis.get("consecutive_misses", 0)

    if total == 0:
        return "  No sessions available to summarise.\n"

    pct = round(on_target / total * 100) if total > 0 else 0
    miss_dir = "underheating" if avg_miss > 0 else "overheating"
    trend_desc = {
        "improving": "Performance is improving week-on-week.",
        "worsening": "Performance has been getting worse.",
        "stable": "Performance is stable.",
    }.get(trend, "")

    lines = f"  {on_target} of {total} sessions reached target (within 0.5°C) = {pct}%.\n"
    if abs(avg_miss) >= 0.1:
        lines += f"  Average miss: {avg_miss:+.1f}°C ({miss_dir} on average).\n"
    if consecutive >= 2:
        lines += f"  Warning: {consecutive} consecutive sessions missed the target.\n"
    lines += f"  {trend_desc}\n"
    return lines


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

    Returns dict with sessions, avg_rate, success_rate, avg_start_time,
    and target accuracy fields: sessions_total, sessions_with_miss,
    sessions_on_target, average_miss, consecutive_misses, miss_trend.

    Note on temp_at_schedule_start:
        Historical schedule event times are not available in InfluxDB sensor
        data. ``temp_at_schedule_start`` is the temperature reached at the
        end of the detected heating ramp — the closest proxy for "was the
        room warm enough when the schedule activated?". A miss of 0 means
        pre-heat worked perfectly; positive miss means room was too cold.
    """
    if len(readings) < 4:
        return {
            "sessions": [],
            "avg_rate": None,
            "success_rate": 0,
            "avg_start_time": None,
            "days_analyzed": 0,
            "sessions_total": 0,
            "sessions_with_miss": 0,
            "sessions_on_target": 0,
            "average_miss": 0.0,
            "consecutive_misses": 0,
            "miss_trend": "stable",
        }

    schedule_targets = []
    if schedules:
        for s in schedules:
            temp = extract_temp_from_schedule_name(
                s.get("name", ""), fallback_temp
            )
            schedule_targets.append(temp)
    if not schedule_targets:
        schedule_targets = [fallback_temp]

    primary_target = max(schedule_targets)

    sessions = []
    session_start = None
    session_start_temp = None
    consecutive_rises = 0

    for i in range(1, len(readings)):
        prev_time, prev_temp = readings[i - 1]
        curr_time, curr_temp = readings[i]

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
                    # temp_at_schedule_start: best available proxy from InfluxDB —
                    # the temperature reached at the end of the heating ramp.
                    temp_at_schedule_start = round(session_end_temp, 1)
                    target_miss = round(primary_target - temp_at_schedule_start, 1)
                    # on-target = within 0.5°C of the comfort temperature
                    target_reached = abs(target_miss) <= 0.5

                    sessions.append(
                        {
                            "date": session_start.strftime("%Y-%m-%d"),
                            "start_time": session_start.strftime("%H:%M"),
                            "start_temp": round(session_start_temp, 1),
                            "end_temp": round(session_end_temp, 1),
                            "temp_at_schedule_start": temp_at_schedule_start,
                            "target_temp": primary_target,
                            "target_miss": target_miss,
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
            "sessions_total": 0,
            "sessions_with_miss": 0,
            "sessions_on_target": 0,
            "average_miss": 0.0,
            "consecutive_misses": 0,
            "miss_trend": "stable",
        }

    # Aggregate over all detected sessions (before trimming to last 7)
    rates = [s["rate"] for s in sessions]
    avg_rate = sum(rates) / len(rates)

    misses = [s["target_miss"] for s in sessions]
    sessions_total = len(sessions)
    sessions_with_miss = sum(1 for m in misses if m > 0.5)
    sessions_on_target = sessions_total - sessions_with_miss
    average_miss = round(sum(misses) / sessions_total, 1)
    success_rate = round((sessions_on_target / sessions_total) * 100, 1)

    # Consecutive misses counting backward from the most recent session
    consecutive_misses = 0
    for s in reversed(sessions):
        if s["target_miss"] > 0.5:
            consecutive_misses += 1
        else:
            break

    # Miss trend: compare first-half average miss vs second-half average miss
    half = sessions_total // 2
    if half >= 2:
        first_avg = sum(s["target_miss"] for s in sessions[:half]) / half
        second_avg = sum(s["target_miss"] for s in sessions[half:]) / (sessions_total - half)
        if second_avg < first_avg - 0.5:
            miss_trend = "improving"
        elif second_avg > first_avg + 0.5:
            miss_trend = "worsening"
        else:
            miss_trend = "stable"
    else:
        miss_trend = "stable"

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
        "success_rate": success_rate,
        "avg_start_time": avg_start_time,
        "days_analyzed": sessions_total,
        "sessions_total": sessions_total,
        "sessions_with_miss": sessions_with_miss,
        "sessions_on_target": sessions_on_target,
        "average_miss": average_miss,
        "consecutive_misses": consecutive_misses,
        "miss_trend": miss_trend,
    }
