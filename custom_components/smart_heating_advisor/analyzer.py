"""Analysis logic for Smart Heating Advisor."""
import logging
import re
from collections import Counter
from datetime import datetime, timedelta

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

    When sessions include a ``trv_target`` field (TRV-based detection), a wider
    table is produced:
        Date | TRV target | Room start | Room end | Rise | Rate | Miss | OK

    Otherwise the compact room-sensor table is used:
        Date | Start | Target | Reached | Miss | OK
    """
    sessions = analysis.get("sessions", [])
    if not sessions:
        return "  No heating sessions detected.\n"

    # TRV-based session format
    if sessions[0].get("trv_target") is not None:
        header = (
            f"  {'Date':<12} {'TRV':>6} {'RoomStart':>10} {'RoomEnd':>8} "
            f"{'Rise':>6} {'Rate':>8} {'Miss':>7}  OK\n"
            f"  {'-'*72}\n"
        )
        rows = ""
        for s in sessions:
            trv_t = s.get("trv_target", 0.0)
            start_t = s.get("start_temp")
            end_t = s.get("end_temp")
            rise = s.get("room_rise")
            rate = s.get("rate", 0.0)
            miss = s.get("target_miss")
            ok = "✅" if s.get("target_reached") else "❌"
            start_str = f"{start_t:.1f}°C" if start_t is not None else " n/a "
            end_str = f"{end_t:.1f}°C" if end_t is not None else " n/a "
            rise_str = f"{rise:+.1f}°C" if rise is not None else "  n/a"
            rate_str = f"{rate:.3f}" if rate else "  n/a "
            miss_str = f"{miss:+.1f}°C" if miss is not None else "  n/a "
            rows += (
                f"  {s['date']:<12} "
                f"{trv_t:>4.1f}°C "
                f"{start_str:>9} "
                f"{end_str:>7} "
                f"{rise_str:>6} "
                f"{rate_str:>7} "
                f"{miss_str:>7}  {ok}\n"
            )
        return header + rows

    # Room-sensor session format
    header = (
        f"  {'Date':<12} {'Start':>7} {'Target':>7} "
        f"{'Reached':>8} {'Miss':>7}  OK\n"
        f"  {'-'*54}\n"
    )
    rows = ""
    for s in sessions:
        miss = s.get("target_miss", 0.0)
        ok = "✅" if s["target_reached"] else "❌"
        reached = s.get("temp_at_schedule_start", s["end_temp"])
        start_t = s.get("start_temp", 0.0)
        rows += (
            f"  {s['date']:<12} "
            f"{start_t:>5.1f}°C "
            f"{s['target_temp']:>5.1f}°C "
            f"{reached:>6.1f}°C "
            f"{miss:>+6.1f}°C  {ok}\n"
        )
    return header + rows


def build_sessions_text(analysis: dict, weekly: bool = False) -> str:
    """Build the compact heating-sessions block for the weekly prompt."""
    text = ""
    for s in analysis.get("sessions", []):
        reached = "✅" if s.get("target_reached") else "❌"
        start_t = s.get("start_temp")
        end_t = s.get("end_temp")
        tass = s.get("temp_at_schedule_start", end_t)
        miss = s.get("target_miss")
        rate = s.get("rate", 0.0)
        trv_target = s.get("trv_target")
        if weekly:
            miss_str = f"{miss:+.1f}°C" if miss is not None else "n/a"
            start_str = f"{start_t}°C" if start_t is not None else "n/a"
            end_str = f"{tass}°C" if tass is not None else "n/a"
            trv_str = f", TRV set to {trv_target}°C" if trv_target is not None else ""
            text += (
                f"  - {s['date']} {reached}: "
                f"{start_str} → {end_str} "
                f"(target {s['target_temp']}°C, miss {miss_str})"
                f"{trv_str}, "
                f"rate: {rate}°C/min\n"
            )
        else:
            start_str = f"{start_t}°C" if start_t is not None else "n/a"
            end_str = f"{end_t}°C" if end_t is not None else "n/a"
            text += (
                f"  - {s['date']} {reached}: started {s['start_time']} "
                f"at {start_str}, reached {end_str} "
                f"in {s['duration_min']:.0f} min "
                f"(rate: {rate}°C/min, target: {s['target_temp']}°C)\n"
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
# TRV-based session detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_standby_temp(trv_readings: dict[str, list[tuple]]) -> float:
    """Return the standby setpoint temperature from TRV history.

    Looks at all TRV readings below 15 °C and returns the most common value.
    Falls back to 7.0 °C (typical radiator frost-protection setpoint) when no
    sub-15 °C readings are available.
    """
    low_values: list[float] = []
    for readings in trv_readings.values():
        for _, temp in readings:
            if temp < 15.0:
                low_values.append(temp)
    if not low_values:
        return 7.0
    most_common, _ = Counter(low_values).most_common(1)[0]
    return most_common


def detect_all_trvs_active_since(
    trv_readings: dict[str, list[tuple]],
    standby_threshold: float,
) -> datetime | None:
    """Return the latest first-active timestamp across all TRVs.

    "First active" means the earliest reading where a TRV's setpoint exceeded
    ``standby_threshold``.  Returns the *latest* of those timestamps so that
    sessions are only analysed from the point where ALL TRVs were known to be
    working correctly.

    Returns None when:
    - No TRV data is provided.
    - Any individual TRV never exceeded the threshold in the available data
      (i.e. it appears broken or always in standby).
    """
    if not trv_readings:
        return None

    latest: datetime | None = None
    for entity_id, readings in trv_readings.items():
        first_active: datetime | None = None
        for ts, temp in sorted(readings, key=lambda x: x[0]):
            if temp > standby_threshold:
                first_active = ts
                break
        if first_active is None:
            _LOGGER.debug(
                "TRV %s never exceeded threshold %.1f°C — treating all TRV data as unreliable",
                entity_id, standby_threshold,
            )
            return None
        if latest is None or first_active > latest:
            latest = first_active

    return latest


def _closest_temp_to_ts(
    readings: list[tuple],
    target_ts: datetime,
    window_minutes: int = 60,
) -> float | None:
    """Return the temperature reading closest in time to ``target_ts``.

    Returns None when no reading falls within ``window_minutes`` of the target.
    """
    best_temp: float | None = None
    best_diff = float("inf")
    for ts, temp in readings:
        diff = abs((ts - target_ts).total_seconds() / 60)
        if diff < best_diff and diff <= window_minutes:
            best_diff = diff
            best_temp = temp
    return best_temp


def detect_sessions_from_trvs(
    trv_readings: dict[str, list[tuple]],
    room_temp_readings: list[tuple],
    primary_target: float,
    standby_threshold: float,
    all_trvs_active_since: datetime | None,
    schedule_time_hhmm: str | None,
) -> list[dict]:
    """Detect heating sessions from TRV setpoint history.

    A session is defined as:
    - Start: any TRV setpoint rises above ``standby_threshold``.
    - End:   all TRV setpoints drop back below ``standby_threshold``.

    Room temperature readings are cross-referenced (±60 min) to measure the
    actual temperature rise during the session.  Sessions before
    ``all_trvs_active_since`` are excluded (broken TRV period).

    Returns a list of session dicts compatible with the existing session format,
    plus an extra ``trv_target`` field (max setpoint seen during the session).
    """
    if not trv_readings:
        return []

    # Build a sorted timeline of all TRV setpoint events
    events: list[tuple[datetime, str, float]] = []
    for entity_id, readings in trv_readings.items():
        for ts, temp in readings:
            events.append((ts, entity_id, temp))
    events.sort(key=lambda x: x[0])

    trv_ids = list(trv_readings.keys())
    # Current known setpoint for each TRV (None = not yet seen)
    current_setpoints: dict[str, float | None] = {eid: None for eid in trv_ids}

    session_active = False
    session_start: datetime | None = None
    session_max_trv: float = 0.0
    sessions: list[dict] = []

    def _any_active() -> bool:
        return any(
            v is not None and v > standby_threshold
            for v in current_setpoints.values()
        )

    def _all_inactive() -> bool:
        return all(
            v is not None and v <= standby_threshold
            for v in current_setpoints.values()
        )

    for ts, entity_id, setpoint in events:
        current_setpoints[entity_id] = setpoint

        # Wait until we have at least one reading for every TRV
        if any(v is None for v in current_setpoints.values()):
            continue

        if not session_active and _any_active():
            session_active = True
            session_start = ts
            session_max_trv = setpoint if setpoint > standby_threshold else 0.0

        elif session_active:
            # Track the highest active setpoint during the session
            if setpoint > standby_threshold:
                session_max_trv = max(session_max_trv, setpoint)

            if _all_inactive():
                # Session ended
                session_end = ts
                assert session_start is not None
                duration_min = (session_end - session_start).total_seconds() / 60

                if duration_min < _MIN_SESSION_DURATION_MIN:
                    _LOGGER.debug(
                        "TRV session on %s too short (%.0f min < %d min) — skipped",
                        session_start.strftime("%Y-%m-%d"), duration_min, _MIN_SESSION_DURATION_MIN,
                    )
                else:
                    full_setup = not (all_trvs_active_since and session_start < all_trvs_active_since)
                    if not full_setup:
                        _LOGGER.debug(
                            "TRV session on %s before all_trvs_active_since (%s) — included with full_setup=False",
                            session_start.strftime("%Y-%m-%d %H:%M"),
                            all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
                        )
                    date_str = session_start.strftime("%Y-%m-%d")
                    start_temp = _closest_temp_to_ts(room_temp_readings, session_start)
                    end_temp_raw = _closest_temp_to_ts(room_temp_readings, session_end)

                    # temp_at_schedule_start: room temp at scheduled comfort time
                    if schedule_time_hhmm:
                        looked_up = _lookup_temp_at_time(
                            room_temp_readings, date_str, schedule_time_hhmm
                        )
                        tass = (
                            round(looked_up, 1)
                            if looked_up is not None
                            else (round(end_temp_raw, 1) if end_temp_raw is not None else None)
                        )
                    else:
                        tass = round(end_temp_raw, 1) if end_temp_raw is not None else None

                    # Rate from room sensor where available; else None
                    if start_temp is not None and end_temp_raw is not None and duration_min > 0:
                        room_rise = end_temp_raw - start_temp
                        rate = round(room_rise / duration_min, 3) if room_rise > 0 else 0.0
                        # If room sensor barely moved, rate will be near zero — that's OK to log
                        if rate < _MIN_SESSION_RATE:
                            _LOGGER.debug(
                                "TRV session %s: room sensor rate %.3f °C/min below minimum "
                                "(room may be too large or sensor too far from radiator)",
                                date_str, rate,
                            )
                    else:
                        room_rise = None
                        rate = 0.0

                    target_miss = (
                        round(primary_target - tass, 1)
                        if tass is not None
                        else None
                    )
                    target_reached = (
                        abs(target_miss) <= 0.5
                        if target_miss is not None
                        else False
                    )

                    sessions.append(
                        {
                            "date": date_str,
                            "start_time": session_start.strftime("%H:%M"),
                            "start_temp": round(start_temp, 1) if start_temp is not None else None,
                            "end_temp": round(end_temp_raw, 1) if end_temp_raw is not None else None,
                            "temp_at_schedule_start": tass,
                            "target_temp": primary_target,
                            "trv_target": round(session_max_trv, 1),
                            "room_rise": round(room_rise, 1) if room_rise is not None else None,
                            "target_miss": target_miss,
                            "duration_min": round(duration_min, 0),
                            "rate": rate,
                            "target_reached": target_reached,
                            "full_setup": full_setup,
                        }
                    )

                session_active = False
                session_start = None
                session_max_trv = 0.0

    # Ignore an in-progress session at end of data (incomplete)
    return sessions


# ──────────────────────────────────────────────────────────────────────────────
# Main analysis entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyze_heating_sessions(
    readings: list[tuple],
    schedules: list[dict] | None = None,
    fallback_temp: float = 21.0,
    schedule_time_hhmm: str | None = None,
    trv_readings: dict[str, list[tuple]] | None = None,
    all_trvs_active_since: datetime | None = None,
    standby_temp: float | None = None,
    target_temp: float | None = None,
) -> dict:
    """Analyze temperature readings to extract heating session statistics.

    Args:
        readings: list of (datetime, float) room-temperature tuples
        schedules: list of dicts with keys: name, next_event, target_temp
        fallback_temp: default target temp if no schedules provided
        schedule_time_hhmm: HH:MM of the primary schedule (e.g. "07:00").
            When provided, ``temp_at_schedule_start`` is looked up from the
            readings at that time on each session date.
        trv_readings: dict mapping TRV entity_id → list of (datetime, setpoint)
            tuples.  When non-empty, TRV-based session detection is used in
            preference to the room-sensor detector.
        all_trvs_active_since: exclude TRV sessions before this timestamp
            (broken-TRV guard).  Passed through to detect_sessions_from_trvs.
        standby_temp: detected standby setpoint (used to derive threshold).
            Only needed when trv_readings is non-empty.
        target_temp: explicit schedule target temperature.  When provided
            overrides the target derived from schedule names.

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

    # Resolve primary target temperature
    schedule_targets: list[float] = []
    if target_temp is not None:
        schedule_targets = [target_temp]
    elif schedules:
        for s in schedules:
            temp = extract_temp_from_schedule_name(s.get("name", ""), fallback_temp)
            schedule_targets.append(temp)
    if not schedule_targets:
        schedule_targets = [fallback_temp]
    primary_target = max(schedule_targets)

    # ── TRV-based detection (preferred when TRV data is available) ────────────
    if trv_readings:
        _standby = standby_temp if standby_temp is not None else detect_standby_temp(trv_readings)
        _threshold = _standby + 5.0
        _LOGGER.debug(
            "Session detection: TRV-based — %d TRV(s), standby=%.1f°C, threshold=%.1f°C",
            len(trv_readings), _standby, _threshold,
        )
        sessions = detect_sessions_from_trvs(
            trv_readings,
            readings,
            primary_target,
            _threshold,
            all_trvs_active_since,
            schedule_time_hhmm,
        )
    else:
        # ── Room-sensor-based detection ──────────────────────────────────────
        if len(readings) < 4:
            return _EMPTY

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

    # Split by TRV setup completeness for accuracy calculations.
    # Room-sensor sessions (no full_setup field) default to True.
    full_sessions = [s for s in sessions if s.get("full_setup", True)]

    # sessions_total counts all sessions (for display / pattern detection).
    # Accuracy metrics use full-setup sessions only.
    sessions_total = len(sessions)
    rates = [s["rate"] for s in full_sessions if s.get("rate") is not None and s["rate"] > 0]
    avg_rate = sum(rates) / len(rates) if rates else None

    misses = [s["target_miss"] for s in full_sessions if s.get("target_miss") is not None]
    if misses:
        sessions_with_miss = sum(1 for m in misses if m > 0.5)
        sessions_on_target = len(misses) - sessions_with_miss
        average_miss = round(sum(misses) / len(misses), 1)
        success_rate = round((sessions_on_target / len(misses)) * 100, 1)
    else:
        sessions_with_miss = 0
        sessions_on_target = 0
        average_miss = 0.0
        success_rate = 0.0

    # Consecutive misses counting backward from the most recent full-setup session
    consecutive_misses = 0
    for s in reversed(full_sessions):
        miss = s.get("target_miss")
        if miss is not None and miss > 0.5:
            consecutive_misses += 1
        else:
            break

    # Miss trend: compare first-half vs second-half average miss (full-setup sessions)
    full_total = len(full_sessions)
    half = full_total // 2
    if half >= 2:
        first_misses = [s["target_miss"] for s in full_sessions[:half] if s.get("target_miss") is not None]
        second_misses = [s["target_miss"] for s in full_sessions[half:] if s.get("target_miss") is not None]
        if first_misses and second_misses:
            first_avg = sum(first_misses) / len(first_misses)
            second_avg = sum(second_misses) / len(second_misses)
            if second_avg < first_avg - 0.5:
                miss_trend = "improving"
            elif second_avg > first_avg + 0.5:
                miss_trend = "worsening"
            else:
                miss_trend = "stable"
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
        "avg_rate": round(avg_rate, 3) if avg_rate is not None else None,
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


# ──────────────────────────────────────────────────────────────────────────────
# hvac_action_str-based session detection (confirmed InfluxDB schema)
# ──────────────────────────────────────────────────────────────────────────────

def merge_heating_periods(
    periods: list[tuple],
) -> list[tuple]:
    """Merge overlapping or adjacent (start, end) datetime periods.

    Returns a sorted list of non-overlapping periods.
    """
    if not periods:
        return []
    sorted_periods = sorted(periods, key=lambda p: p[0])
    merged = [list(sorted_periods[0])]
    for start, end in sorted_periods[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def build_schedule_on_periods(
    state_readings: list[tuple],
) -> list[dict]:
    """Build ON periods from schedule state history.

    ``state_readings`` is a list of (timestamp, state_string) tuples where
    state_string is ``"on"`` or ``"off"``.  Returns a list of dicts with
    ``start`` and ``end`` keys (datetime objects).  An open-ended ON period
    at the end of the data is not included.
    """
    periods: list[dict] = []
    on_start: datetime | None = None

    for ts, state in sorted(state_readings, key=lambda x: x[0]):
        state_lower = str(state).lower()
        if state_lower == "on" and on_start is None:
            on_start = ts
        elif state_lower != "on" and on_start is not None:
            periods.append({"start": on_start, "end": ts})
            on_start = None

    return periods


def _get_trv_setpoint_at_session_start(
    trv_data: dict,
    session_start: datetime,
    lookback_hours: int = 24,
) -> float | None:
    """Return the TRV commanded setpoint (``temperature`` field) active at session start.

    Uses the most recent reading at or before ``session_start`` within the lookback
    window.  Reads ``temperature`` (what SHA commanded) — NOT ``current_temperature``
    (radiator surface/body temperature, which can reach 30-33 °C near a hot radiator).
    """
    best_ts: datetime | None = None
    best_val: float | None = None
    cutoff = session_start - timedelta(hours=lookback_hours)
    for fields in trv_data.values():
        for ts, val in fields.get("temperature", []):  # setpoint, NOT current_temperature
            if not isinstance(val, (int, float)):
                continue
            if cutoff <= ts <= session_start:
                if best_ts is None or ts > best_ts:
                    best_ts = ts
                    best_val = val
    return best_val


def _get_current_temp_nearest(
    trv_data: dict,
    target_ts: datetime,
    window_min: int = 30,
) -> float | None:
    """Return the closest ``current_temperature`` reading across all TRVs.

    Returns None when no reading falls within ``window_min`` minutes.
    """
    best_val: float | None = None
    best_diff = float("inf")
    for fields in trv_data.values():
        for ts, val in fields.get("current_temperature", []):
            if not isinstance(val, (int, float)):
                continue
            diff = abs((ts - target_ts).total_seconds() / 60)
            if diff < best_diff and diff <= window_min:
                best_diff = diff
                best_val = val
    return best_val


def detect_heating_sessions_from_hvac(
    trv_data: dict,
    all_trvs_active_since: datetime | None = None,
    min_duration_min: int = 10,
) -> list[dict]:
    """Detect raw heating sessions from ``hvac_action_str`` readings.

    For each TRV, consecutive ``"heating"`` readings define a heating period.
    Periods from multiple TRVs are merged (union).  Sessions shorter than
    ``min_duration_min`` are discarded.  Sessions starting before
    ``all_trvs_active_since`` are excluded.

    Returns a list of raw session dicts: ``{start, end, duration_min, date, start_time}``.
    """
    all_periods: list[tuple] = []

    for entity_id, fields in trv_data.items():
        hvac_readings = sorted(
            [(ts, val) for ts, val in fields.get("hvac_action_str", [])],
            key=lambda x: x[0],
        )
        if not hvac_readings:
            continue

        period_start: datetime | None = None
        prev_ts: datetime | None = None

        for ts, state in hvac_readings:
            is_heating = str(state).lower() == "heating"

            if is_heating and period_start is None:
                period_start = ts
            elif not is_heating and period_start is not None:
                all_periods.append((period_start, ts))
                period_start = None
            prev_ts = ts

        # Close any open period at end of data
        if period_start is not None and prev_ts is not None and prev_ts > period_start:
            all_periods.append((period_start, prev_ts))

    merged = merge_heating_periods(all_periods)

    sessions: list[dict] = []
    for start, end in merged:
        duration_min = (end - start).total_seconds() / 60
        if duration_min < min_duration_min:
            _LOGGER.debug(
                "hvac session %s too short (%.0f min < %d min) — skipped",
                start.strftime("%Y-%m-%d %H:%M"), duration_min, min_duration_min,
            )
            continue
        if duration_min > 240:
            _LOGGER.debug(
                "Spurious session excluded: %s %s-%s (%.0f min) exceeds 240 min maximum"
                " — TRV may have missed an idle transition",
                start.strftime("%Y-%m-%d"),
                start.strftime("%H:%M"),
                end.strftime("%H:%M"),
                duration_min,
            )
            continue
        full_setup = not (all_trvs_active_since and start < all_trvs_active_since)
        if not full_setup:
            _LOGGER.debug(
                "hvac session %s before all_trvs_active_since (%s) — included with full_setup=False",
                start.strftime("%Y-%m-%d %H:%M"),
                all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
            )
        sessions.append({
            "start": start,
            "end": end,
            "duration_min": round(duration_min, 1),
            "date": start.strftime("%Y-%m-%d"),
            "start_time": start.strftime("%H:%M"),
            "full_setup": full_setup,
        })

    return sessions


def _build_matched_session(
    session: dict,
    sched_info: dict,
    on_period: dict,
    room_temp_readings: list[tuple],
    trv_data: dict,
) -> dict:
    """Enrich a raw session with schedule and room-temp context.

    Args:
        session:            Raw session dict (start, end, duration_min, date, start_time).
        sched_info:         Dict with entity_id, name, target_temp, schedule_time.
        on_period:          Schedule ON period dict with start/end datetimes.
        room_temp_readings: (datetime, float) list for room sensor.
        trv_data:           Full TRV data dict for current_temperature fallback.
    """
    session_start: datetime = session["start"]
    session_end: datetime = session["end"]
    schedule_on_ts: datetime = on_period["start"]
    target_temp: float = sched_info["target_temp"]

    preheat_min = (schedule_on_ts - session_start).total_seconds() / 60
    duration_min = session["duration_min"]

    # Room temp at session start
    room_start = _closest_temp_to_ts(room_temp_readings, session_start, window_minutes=30)
    if room_start is None:
        room_start = _get_current_temp_nearest(trv_data, session_start, window_min=30)

    # Exclude sessions with no start temperature — can't compute rate or miss
    if room_start is None:
        return None

    # Room temp at session end (for rate calculation)
    room_end = _closest_temp_to_ts(room_temp_readings, session_end, window_minutes=30)
    if room_end is None:
        room_end = _get_current_temp_nearest(trv_data, session_end, window_min=30)

    # Room temp when schedule turned ON (= comfort check time)
    room_at_on = _closest_temp_to_ts(room_temp_readings, schedule_on_ts, window_minutes=30)
    if room_at_on is None:
        room_at_on = _get_current_temp_nearest(trv_data, schedule_on_ts, window_min=30)

    # TRV commanded setpoint at session start (temperature field = what SHA set)
    max_setpoint = _get_trv_setpoint_at_session_start(trv_data, session_start)

    # room_rise for display — full session span (start → end)
    room_rise: float | None = None
    if room_end is not None:
        room_rise = room_end - room_start

    # Observed rate: rise at comfort sensor from session start to schedule ON time
    # divided by the preheat duration.  This directly measures how fast the comfort
    # sensor (room thermostat near the door) climbs during the pre-heat window —
    # which is exactly what SHA uses for timing.
    # Falls back to full session duration when schedule ON time is unavailable.
    observed_rate: float | None = None
    if room_at_on is not None and preheat_min > 0:
        comfort_rise = room_at_on - room_start
        if comfort_rise > 0:
            observed_rate = round(comfort_rise / preheat_min, 3)
    elif room_rise is not None and room_rise > 0 and duration_min > 0:
        observed_rate = round(room_rise / duration_min, 3)

    # Peak comfort temperature: maximum reading in [schedule_on_ts, schedule_on_ts + 30 min].
    # Room may peak 5-15 min after schedule ON time; measuring exactly at ON time causes false
    # misses when the target was actually reached shortly after.
    # Falls back to room_at_on when no readings fall in the window.
    peak_window_end = schedule_on_ts + timedelta(minutes=30)
    peak_comfort: float | None = None
    for ts, temp in room_temp_readings:
        if schedule_on_ts <= ts <= peak_window_end and isinstance(temp, (int, float)):
            if peak_comfort is None or temp > peak_comfort:
                peak_comfort = temp
    if peak_comfort is None:
        peak_comfort = room_at_on

    # Miss = target − peak comfort temperature in the 30 min window after schedule ON
    target_miss: float | None = None
    target_reached = False
    if peak_comfort is not None:
        target_miss = round(target_temp - peak_comfort, 1)
        target_reached = target_miss <= 0.5

    return {
        "date": session["date"],
        "start_time": session["start_time"],
        "start": session_start,
        "end": session_end,
        "duration_min": duration_min,
        "preheat_min": round(preheat_min, 1),
        "schedule_entity_id": sched_info["entity_id"],
        "schedule_name": sched_info["name"],
        "schedule_time": sched_info.get("schedule_time", "n/a"),
        "target_temp": target_temp,
        "trv_target": round(max_setpoint, 1) if max_setpoint is not None else None,
        # Room-temp fields — canonical names + compat aliases
        "room_temp_at_start": round(room_start, 1),
        "room_temp_at_schedule_start": round(room_at_on, 1) if room_at_on is not None else None,
        "room_temp_at_schedule_on": round(room_at_on, 1) if room_at_on is not None else None,
        "start_temp": round(room_start, 1),
        "end_temp": round(room_at_on, 1) if room_at_on is not None else None,
        "temp_at_schedule_start": round(room_at_on, 1) if room_at_on is not None else None,
        "room_rise": round(room_rise, 1) if room_rise is not None else None,
        # Rate fields — both canonical and compat
        "observed_rate": observed_rate,
        "rate": observed_rate,
        "target_miss": target_miss,
        "target_reached": target_reached,
        "full_setup": session.get("full_setup", True),
    }


def _match_one_session(
    session: dict,
    schedule_on_periods: dict,
    schedules_info: list[dict],
    room_temp_readings: list[tuple],
    trv_data: dict,
) -> list[dict]:
    """Match one raw session to zero or more schedule ON periods.

    A schedule ON period matches if it started within
    [session_start − 240 min, session_end].

    When no InfluxDB schedule history exists for a schedule (schedule entity
    not yet recorded in InfluxDB), falls back to matching using the configured
    schedule start time from the automation config.  The session must start
    within 240 minutes before the configured time on the same day.

    Returns a list of enriched session dicts (one per matched schedule).
    Unmatched sessions (manual overrides, no schedule match, or no room temp data)
    are excluded — returns an empty list.
    """
    session_start: datetime = session["start"]
    session_end: datetime = session["end"]
    window_open = session_start - timedelta(minutes=240)

    matched: list[dict] = []

    for sched_info in schedules_info:
        entity_id = sched_info["entity_id"]
        on_periods = schedule_on_periods.get(entity_id, [])

        if on_periods:
            for on_period in on_periods:
                period_start: datetime = on_period["start"]
                if window_open <= period_start <= session_end:
                    enriched = _build_matched_session(
                        session, sched_info, on_period, room_temp_readings, trv_data
                    )
                    if enriched is not None:
                        matched.append(enriched)
        else:
            # Fallback: no InfluxDB schedule history — match using configured
            # schedule time so pre-history sessions are not silently excluded.
            schedule_time = sched_info.get("schedule_time")
            if not schedule_time or schedule_time == "n/a":
                continue
            try:
                sh, sm = map(int, schedule_time.split(":"))
            except (ValueError, AttributeError):
                continue
            # Compute the schedule ON datetime on the session's date.
            scheduled_on = session_start.replace(
                hour=sh, minute=sm, second=0, microsecond=0
            )
            # If the configured time is before session start, push to next day.
            if scheduled_on < session_start:
                scheduled_on += timedelta(days=1)
            gap_min = (scheduled_on - session_start).total_seconds() / 60
            if 0 <= gap_min <= 240:
                virtual_on_period = {
                    "start": scheduled_on,
                    "end": scheduled_on + timedelta(minutes=60),
                }
                enriched = _build_matched_session(
                    session, sched_info, virtual_on_period, room_temp_readings, trv_data
                )
                if enriched is not None:
                    _LOGGER.debug(
                        "Session %s %s matched to '%s' via config time %s "
                        "(no InfluxDB schedule history available).",
                        session["date"], session["start_time"],
                        sched_info["name"], schedule_time,
                    )
                    matched.append(enriched)

    # Unmatched sessions (no schedule match, or all room_start were None) are excluded
    return matched


def _deduplicate_matched_sessions(sessions: list[dict]) -> list[dict]:
    """Remove duplicate sessions that match the same schedule on the same calendar day.

    TRV cycling (heating → idle → heating) can produce two raw sessions for a
    single morning warm-up, both of which match the same schedule.  This function
    collapses them in two passes:

    Pass 1 — merge clusters (≤ 30 min gap between starts):
      Sessions whose starts are within 30 minutes are treated as one heating event
      split by a brief idle period.  They are merged into a single session:
        start        = earliest start
        end          = latest end
        duration_min = sum of individual durations
        other fields = from the longest session in the cluster (best data quality)

    Pass 2 — discard cross-cluster duplicates (> 30 min gap, same day/schedule):
      If multiple clusters survive for the same (date, schedule), only the one
      with the longest total duration is kept; shorter ones are logged and dropped.
    """
    from collections import defaultdict

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in sessions:
        key = (s["date"], s.get("schedule_entity_id", ""))
        groups[key].append(s)

    result: list[dict] = []
    for (date, eid), group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        # Pass 1: cluster by start proximity (≤ 30 min gap to previous in cluster)
        group_sorted = sorted(group, key=lambda s: s["start"])
        clusters: list[list[dict]] = [[group_sorted[0]]]
        for s in group_sorted[1:]:
            gap_min = (s["start"] - clusters[-1][-1]["start"]).total_seconds() / 60
            if gap_min <= 30:
                clusters[-1].append(s)
            else:
                clusters.append([s])

        # Merge each cluster into a single representative session
        merged_candidates: list[dict] = []
        for cluster in clusters:
            if len(cluster) == 1:
                merged_candidates.append(cluster[0])
                continue
            # Merge: start from earliest, end from latest, duration = sum
            earliest = cluster[0]  # already sorted by start
            latest_end = max(s["end"] for s in cluster)
            total_duration = sum(s["duration_min"] for s in cluster)
            best = max(cluster, key=lambda s: s["duration_min"])
            merged = {**best}
            merged["start"] = earliest["start"]
            merged["start_time"] = earliest["start_time"]
            merged["end"] = latest_end
            merged["duration_min"] = round(total_duration, 1)
            # Use start temp from the earliest session (actual cold-start reading)
            for field in ("room_temp_at_start", "start_temp"):
                if earliest.get(field) is not None:
                    merged[field] = earliest[field]
            _LOGGER.debug(
                "Merged %d sessions for %s / %s — start %s end %s total %.0f min",
                len(cluster), date, eid,
                earliest["start_time"],
                latest_end.strftime("%H:%M"),
                total_duration,
            )
            merged_candidates.append(merged)

        # Pass 2: if multiple clusters remain, keep only the longest
        if len(merged_candidates) == 1:
            result.append(merged_candidates[0])
        else:
            kept = max(merged_candidates, key=lambda s: s["duration_min"])
            for s in merged_candidates:
                if s is not kept:
                    _LOGGER.debug(
                        "Duplicate session removed: %s %s (%.0f min)"
                        " — kept longer session %s (%.0f min)",
                        date, s.get("start_time"), s["duration_min"],
                        kept.get("start_time"), kept["duration_min"],
                    )
            result.append(kept)

    result.sort(key=lambda s: s["date"])
    return result


def match_sessions_to_schedules(
    sessions: list[dict],
    schedule_on_periods: dict,
    schedules_info: list[dict],
    room_temp_readings: list[tuple],
    trv_data: dict,
) -> list[dict]:
    """Match all raw heating sessions to schedules.

    Returns a flat list of enriched session dicts sorted by date,
    deduplicated so at most one session per (day, schedule) remains.
    """
    result: list[dict] = []
    for session in sessions:
        result.extend(
            _match_one_session(
                session, schedule_on_periods, schedules_info, room_temp_readings, trv_data
            )
        )
    result.sort(key=lambda s: s["date"])
    return _deduplicate_matched_sessions(result)


def analyze_sessions_per_schedule(
    sessions: list[dict],
    schedules_info: list[dict],
) -> dict:
    """Compute per-schedule accuracy statistics.

    Returns a dict mapping schedule entity_id → stats dict.
    """
    per_sched: dict[str, list[dict]] = {}
    for s in sessions:
        eid = s.get("schedule_entity_id")
        if eid:
            per_sched.setdefault(eid, []).append(s)

    result: dict = {}

    for sched_info in schedules_info:
        eid = sched_info["entity_id"]
        sched_sessions = per_sched.get(eid, [])

        # Split by TRV setup completeness.
        # full_setup=True  → all TRVs were active (reliable data)
        # full_setup=False → at least one TRV was inactive (partial coverage)
        full_sessions = [s for s in sched_sessions if s.get("full_setup", True)]
        partial_sessions = [s for s in sched_sessions if not s.get("full_setup", True)]
        total = len(sched_sessions)          # all sessions — for display
        full_count = len(full_sessions)      # for accuracy threshold and calculations
        partial_count = len(partial_sessions)

        # Accuracy metrics computed from full-setup sessions only
        misses = [
            s["target_miss"] for s in full_sessions if s.get("target_miss") is not None
        ]
        rates = [
            s["observed_rate"] for s in full_sessions
            if s.get("observed_rate") is not None and s["observed_rate"] > 0
        ]
        preheat_mins = [
            s["preheat_min"] for s in full_sessions if s.get("preheat_min") is not None
        ]
        room_temps_at_start = [
            s["room_temp_at_start"] for s in full_sessions
            if s.get("room_temp_at_start") is not None
        ]
        room_temps_at_schedule_start = [
            s["room_temp_at_schedule_start"] for s in full_sessions
            if s.get("room_temp_at_schedule_start") is not None
        ]

        on_target = sum(1 for m in misses if abs(m) <= 0.5)
        avg_miss = round(sum(misses) / len(misses), 1) if misses else None
        avg_rate = round(sum(rates) / len(rates), 3) if rates else None
        avg_preheat = round(sum(preheat_mins) / len(preheat_mins), 1) if preheat_mins else None
        avg_room_temp_at_start = round(sum(room_temps_at_start) / len(room_temps_at_start), 1) if room_temps_at_start else None
        avg_room_temp_at_schedule_start = round(sum(room_temps_at_schedule_start) / len(room_temps_at_schedule_start), 1) if room_temps_at_schedule_start else None

        # Recommended preheat: (target_temp − avg_room_temp_at_start) / avg_rate (full sessions)
        recommended_preheat: float | None = None
        target_temp_val = sched_info.get("target_temp")
        if avg_rate and avg_rate > 0 and avg_room_temp_at_start is not None and target_temp_val is not None:
            temp_gap = target_temp_val - avg_room_temp_at_start
            if temp_gap > 0:
                recommended_preheat = round(temp_gap / avg_rate, 0)

        # Miss trend: compare first half vs second half (full-setup sessions only)
        sched_misses_sorted = [
            s["target_miss"] for s in sorted(full_sessions, key=lambda x: x["date"])
            if s.get("target_miss") is not None
        ]
        miss_trend_sched = "stable"
        half = len(sched_misses_sorted) // 2
        if half >= 2:
            first_half = sched_misses_sorted[:half]
            second_half = sched_misses_sorted[half:]
            fa = sum(first_half) / len(first_half)
            sa = sum(second_half) / len(second_half)
            if sa < fa - 0.5:
                miss_trend_sched = "improving"
            elif sa > fa + 0.5:
                miss_trend_sched = "worsening"

        result[eid] = {
            "entity_id": eid,
            "name": sched_info["name"],
            "target_temp": target_temp_val,
            "schedule_time": sched_info.get("schedule_time", "n/a"),
            "sessions_total": total,          # all sessions (for display)
            "full_setup_count": full_count,   # sessions with complete TRV coverage
            "partial_setup_count": partial_count,  # sessions before all TRVs were active
            "sessions_on_target": on_target,  # from full-setup sessions only
            "sessions_with_miss": len([m for m in misses if m > 0.5]),
            "avg_miss": avg_miss,
            "avg_rate": avg_rate,
            "avg_preheat_min": avg_preheat,
            "recommended_preheat_min": recommended_preheat,
            "avg_room_temp_at_start": avg_room_temp_at_start,
            "avg_room_temp_at_schedule_start": avg_room_temp_at_schedule_start,
            "miss_trend": miss_trend_sched,
            "sessions": sched_sessions[-7:],
        }

    return result


def _format_preheat_start(schedule_time: str, preheat_min: float) -> str:
    """Return HH:MM for preheat start given schedule time and minutes before.

    Example: schedule_time="07:30", preheat_min=25.0 → "07:05"
    """
    try:
        h, m = map(int, schedule_time.split(":"))
        total = h * 60 + m - int(preheat_min)
        total = total % (24 * 60)
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, AttributeError):
        return "n/a"


def build_schedules_analysis_text(
    per_schedule: dict,
    all_trvs_active_since: datetime | None,
) -> str:
    """Build the per-schedule accuracy block for prompts.

    Shows for each schedule: stats summary, session detail table.
    If fewer than 3 sessions: shows "Insufficient data" message.
    """
    if not per_schedule:
        return "  No schedule analysis available.\n"

    lines = ""
    for stats in per_schedule.values():
        name = stats["name"]
        total = stats["sessions_total"]
        full_count = stats.get("full_setup_count", total)
        partial_count = stats.get("partial_setup_count", 0)
        on_target = stats["sessions_on_target"]
        target_temp = stats.get("target_temp")
        sched_time = stats.get("schedule_time", "n/a")
        avg_miss = stats.get("avg_miss")
        avg_rate = stats.get("avg_rate")
        rec_preheat = stats.get("recommended_preheat_min")
        avg_room_temp_at_schedule_start = stats.get("avg_room_temp_at_schedule_start")
        miss_trend = stats.get("miss_trend", "stable")

        lines += f"\nSchedule: {name}\n"
        lines += f"Target: {target_temp}°C at {sched_time}\n" if target_temp else f"Schedule time: {sched_time}\n"
        if partial_count > 0:
            lines += f"Sessions: {total} total ({full_count} full-setup, {partial_count} partial-setup)\n"
            lines += "(Accuracy stats from full-setup sessions only)\n"
        else:
            lines += f"Sessions: {total}\n"

        if full_count < 3:
            lines += "Insufficient full-setup data (fewer than 3 full-setup sessions) — no reliable statistics yet.\n"
            if partial_count > 0:
                lines += f"  Note: {partial_count} partial-setup session(s) available for pattern detection only.\n"
            continue

        lines += f"Target reached: {on_target} of {full_count} (full-setup sessions)\n"
        if avg_room_temp_at_schedule_start is not None:
            lines += f"Average room temp at schedule start: {avg_room_temp_at_schedule_start:.1f}°C\n"
        if avg_miss is not None:
            lines += f"Average miss: {avg_miss:+.1f}°C\n"
        if avg_rate is not None:
            lines += f"Real heating rate: {avg_rate:.3f}°C/min\n"
        if rec_preheat is not None:
            lines += f"Recommended pre-heat: {rec_preheat:.0f} min\n"
            if sched_time != "n/a":
                start_str = _format_preheat_start(sched_time, rec_preheat)
                lines += f"Recommended pre-heat start: {start_str}\n"
        lines += f"Miss trend: {miss_trend}\n"

        # Per-session detail (all sessions, flag shows setup completeness)
        sessions = stats.get("sessions", [])
        if sessions:
            lines += "\nSession detail:\n"
            for s in sessions:
                date = s.get("date", "?")
                room_at_start = s.get("room_temp_at_schedule_start")
                miss = s.get("target_miss")
                rate = s.get("observed_rate")
                reached = "reached" if s.get("target_reached") else "missed"
                setup_flag = "" if s.get("full_setup", True) else " [partial]"
                temp_str = f"{room_at_start:.1f}°C" if room_at_start is not None else "n/a"
                miss_str = f"{miss:+.1f}°C" if miss is not None else "n/a"
                rate_str = f"{rate:.3f}°C/min" if rate is not None else "n/a"
                lines += f"  {date}{setup_flag} | at schedule start {temp_str} | miss {miss_str} | rate {rate_str} | {reached}\n"

    if all_trvs_active_since:
        lines += (
            f"\n(Sessions before {all_trvs_active_since.strftime('%Y-%m-%d')} "
            f"have partial TRV coverage — flagged as partial-setup, excluded from accuracy calculations)\n"
        )

    return lines


def build_humidity_analysis_text(
    humidity_readings: list[tuple],
    sessions: list[dict],
) -> str:
    """Build a brief humidity summary for prompts.

    Shows baseline humidity and per-session peak humidity in the window
    (session_start, session_end + 120 min).
    """
    if not humidity_readings:
        return "  No humidity data available.\n"

    all_vals = [v for _, v in humidity_readings if isinstance(v, (int, float))]
    if not all_vals:
        return "  No humidity readings available.\n"

    avg_baseline = round(sum(all_vals) / len(all_vals), 1)

    # Peak humidity per session in (session_start, session_end + 120 min)
    session_peaks: list[str] = []
    for s in sessions:
        start = s.get("start")
        end = s.get("end")
        if start is None or end is None:
            continue
        window_end = end + timedelta(minutes=120)
        peak_val: float | None = None
        peak_ts: datetime | None = None
        for ts, val in humidity_readings:
            if isinstance(val, (int, float)) and start <= ts <= window_end:
                if peak_val is None or val > peak_val:
                    peak_val = val
                    peak_ts = ts
        if peak_val is not None and peak_ts is not None:
            session_peaks.append(
                f"    {s.get('date', '?')}: peak {peak_val:.0f}% at {peak_ts.strftime('%H:%M')}"
            )

    if session_peaks:
        peaks_text = "\n".join(session_peaks)
        return (
            f"  Baseline average humidity: {avg_baseline}%\n"
            f"  Peak humidity per session (during + 120 min after heating):\n"
            f"{peaks_text}\n"
        )
    return f"  Average humidity: {avg_baseline}% (no session overlap found).\n"
