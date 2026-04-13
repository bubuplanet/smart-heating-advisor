"""Analysis logic for Smart Heating Advisor."""
import logging
import re
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# Minimum thresholds for a valid pre-heat / heating session
_MIN_SESSION_RATE = 0.05          # °C/min — below this is background drift
_MIN_SESSION_GAIN = 1.5           # °C — minimum temperature rise per session
_MIN_SESSION_DURATION_MIN = 10    # minutes — drift events are hours-long, real heat is fast


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
    heating ramp — the closest proxy for temp at schedule start.
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


# ──────────────────────────────────────────────────────────────────────────────
# Session detection helpers
# ──────────────────────────────────────────────────────────────────────────────

def _avg_interval_min(readings: list[tuple]) -> float:
    """Return the average minutes between consecutive readings."""
    if len(readings) < 2:
        return 0.0
    span_min = (readings[-1][0] - readings[0][0]).total_seconds() / 60
    return span_min / (len(readings) - 1)


def _lookup_temp_at_time(
    readings: list[tuple],
    date_str: str,
    time_hhmm: str,
    window_minutes: int = 30,
) -> float | None:
    """Find the temperature reading closest to time_hhmm on date_str.

    Returns None when no reading falls within window_minutes of the target time.
    """
    try:
        h, m = map(int, time_hhmm.split(":"))
    except (ValueError, AttributeError):
        return None

    target_minutes = h * 60 + m
    best_temp = None
    best_diff = float("inf")

    for ts, temp in readings:
        if ts.strftime("%Y-%m-%d") != date_str:
            continue
        diff = abs(ts.hour * 60 + ts.minute - target_minutes)
        if diff < best_diff and diff <= window_minutes:
            best_diff = diff
            best_temp = temp

    return best_temp


def _detect_sessions_sparse(
    readings: list[tuple],
    primary_target: float,
    schedule_time_hhmm: str | None,
) -> list[dict]:
    """Detect heating sessions from sparse sensor data (avg interval > 30 min).

    With infrequent readings the consecutive-rise algorithm cannot accumulate
    enough data points during a short heating event.  This detector treats
    each consecutive reading-pair as a candidate session: a significant
    temperature jump between two readings within a 2-hour window is taken as
    evidence that the radiator ran between those readings.

    The reading-pair gap is used as an *upper bound* on session duration, so
    the reported rate is a conservative (lower-bound) estimate of the actual
    heating rate.
    """
    sessions: list[dict] = []
    seen_dates: set[str] = set()

    for i in range(1, len(readings)):
        prev_time, prev_temp = readings[i - 1]
        curr_time, curr_temp = readings[i]

        gap_min = (curr_time - prev_time).total_seconds() / 60
        # Skip pairs spanning more than 2 hours — too long to attribute to a
        # single heating event (heating would have finished and started cooling).
        if gap_min > 120:
            continue

        temp_rise = curr_temp - prev_temp
        if temp_rise < _MIN_SESSION_GAIN:
            continue

        # rate is lower-bound; actual rate during heating is higher
        rate = temp_rise / gap_min if gap_min > 0 else 0.0
        if rate < _MIN_SESSION_RATE:
            continue

        date_str = prev_time.strftime("%Y-%m-%d")
        if date_str in seen_dates:
            # Keep only the first (usually morning) session per day
            continue
        seen_dates.add(date_str)

        if schedule_time_hhmm:
            looked_up = _lookup_temp_at_time(readings, date_str, schedule_time_hhmm)
            temp_at_schedule_start = (
                round(looked_up, 1) if looked_up is not None else round(curr_temp, 1)
            )
        else:
            temp_at_schedule_start = round(curr_temp, 1)

        target_miss = round(primary_target - temp_at_schedule_start, 1)
        sessions.append(
            {
                "date": date_str,
                "start_time": prev_time.strftime("%H:%M"),
                "start_temp": round(prev_temp, 1),
                "end_temp": round(curr_temp, 1),
                "temp_at_schedule_start": temp_at_schedule_start,
                "target_temp": primary_target,
                "target_miss": target_miss,
                "duration_min": round(gap_min, 0),
                "rate": round(rate, 3),
                "target_reached": abs(target_miss) <= 0.5,
            }
        )

    return sessions


def _detect_sessions_dense(
    readings: list[tuple],
    primary_target: float,
    schedule_time_hhmm: str | None,
) -> list[dict]:
    """Detect heating sessions from dense sensor data (avg interval ≤ 30 min).

    Accumulates consecutive temperature rises and finalises a session when the
    temperature plateaus or falls.  A gap > 60 min finalises any in-progress
    session before resetting state so that sessions are not lost when the room
    stabilises and recordings become infrequent.
    """
    sessions: list[dict] = []
    session_start = None
    session_start_temp = None
    consecutive_rises = 0

    def _maybe_save(end_time, end_temp):
        nonlocal session_start, session_start_temp, consecutive_rises
        if session_start is not None and consecutive_rises >= 4:
            duration_min = (end_time - session_start).total_seconds() / 60
            temp_gained = end_temp - session_start_temp
            if (
                duration_min >= _MIN_SESSION_DURATION_MIN
                and temp_gained >= _MIN_SESSION_GAIN
            ):
                rate = temp_gained / duration_min
                if rate >= _MIN_SESSION_RATE:
                    date_str = session_start.strftime("%Y-%m-%d")
                    if schedule_time_hhmm:
                        looked_up = _lookup_temp_at_time(
                            readings, date_str, schedule_time_hhmm
                        )
                        tass = (
                            round(looked_up, 1)
                            if looked_up is not None
                            else round(end_temp, 1)
                        )
                    else:
                        tass = round(end_temp, 1)
                    target_miss = round(primary_target - tass, 1)
                    sessions.append(
                        {
                            "date": date_str,
                            "start_time": session_start.strftime("%H:%M"),
                            "start_temp": round(session_start_temp, 1),
                            "end_temp": round(end_temp, 1),
                            "temp_at_schedule_start": tass,
                            "target_temp": primary_target,
                            "target_miss": target_miss,
                            "duration_min": round(duration_min, 0),
                            "rate": round(rate, 3),
                            "target_reached": abs(target_miss) <= 0.5,
                        }
                    )
        consecutive_rises = 0
        session_start = None
        session_start_temp = None

    for i in range(1, len(readings)):
        prev_time, prev_temp = readings[i - 1]
        curr_time, curr_temp = readings[i]

        time_gap = (curr_time - prev_time).total_seconds() / 60
        if time_gap > 60:
            _maybe_save(prev_time, prev_temp)
            continue

        temp_rise = curr_temp - prev_temp

        if temp_rise > 0.1:
            consecutive_rises += 1
            if consecutive_rises == 2:
                session_start = readings[i - 2][0]
                session_start_temp = readings[i - 2][1]
        else:
            _maybe_save(curr_time, curr_temp)

    return sessions


# ──────────────────────────────────────────────────────────────────────────────
# Main analysis entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyze_heating_sessions(
    readings: list[tuple],
    schedules: list[dict] | None = None,
    fallback_temp: float = 21.0,
    schedule_time_hhmm: str | None = None,
) -> dict:
    """Analyze temperature readings to extract heating session statistics.

    Args:
        readings: list of (datetime, float) temperature tuples
        schedules: list of dicts with keys: name, next_event, target_temp
        fallback_temp: default target temp if no schedules provided
        schedule_time_hhmm: HH:MM of the primary schedule (e.g. "07:00").
            When provided, ``temp_at_schedule_start`` is looked up from the
            readings at that time on each session date.  Falls back to the
            end-of-ramp temperature when no reading is found within ±30 min.

    Returns dict with sessions, avg_rate, success_rate, avg_start_time,
    and target accuracy fields: sessions_total, sessions_with_miss,
    sessions_on_target, average_miss, consecutive_misses, miss_trend.
    """
    _EMPTY = {
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

    if len(readings) < 4:
        return _EMPTY

    schedule_targets = []
    if schedules:
        for s in schedules:
            temp = extract_temp_from_schedule_name(s.get("name", ""), fallback_temp)
            schedule_targets.append(temp)
    if not schedule_targets:
        schedule_targets = [fallback_temp]

    primary_target = max(schedule_targets)

    # Dispatch to the right detector based on data density.
    # Sparse (avg interval > 30 min): the consecutive-rise detector cannot
    # accumulate enough points during a 20-30 min heating event.
    avg_interval = _avg_interval_min(readings)
    _LOGGER.debug(
        "Session detection: %d readings, avg interval %.1f min → %s detector",
        len(readings), avg_interval, "sparse" if avg_interval > 30 else "dense",
    )

    if avg_interval > 30:
        sessions = _detect_sessions_sparse(readings, primary_target, schedule_time_hhmm)
    else:
        sessions = _detect_sessions_dense(readings, primary_target, schedule_time_hhmm)

    if not sessions:
        return _EMPTY

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
