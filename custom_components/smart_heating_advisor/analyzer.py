"""Analysis logic for Smart Heating Advisor."""
import logging
import re
from collections import Counter
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
                elif all_trvs_active_since and session_start < all_trvs_active_since:
                    _LOGGER.debug(
                        "TRV session on %s before all_trvs_active_since (%s) — excluded",
                        session_start.strftime("%Y-%m-%d %H:%M"),
                        all_trvs_active_since.strftime("%Y-%m-%d %H:%M"),
                    )
                else:
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

    # Aggregate over all detected sessions (before trimming to last 7).
    # target_miss and rate may be None for TRV sessions without room-sensor data.
    sessions_total = len(sessions)
    rates = [s["rate"] for s in sessions if s.get("rate") is not None and s["rate"] > 0]
    avg_rate = sum(rates) / len(rates) if rates else None

    misses = [s["target_miss"] for s in sessions if s.get("target_miss") is not None]
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

    # Consecutive misses counting backward from the most recent session
    consecutive_misses = 0
    for s in reversed(sessions):
        miss = s.get("target_miss")
        if miss is not None and miss > 0.5:
            consecutive_misses += 1
        else:
            break

    # Miss trend: compare first-half average miss vs second-half average miss
    half = sessions_total // 2
    if half >= 2:
        first_misses = [s["target_miss"] for s in sessions[:half] if s.get("target_miss") is not None]
        second_misses = [s["target_miss"] for s in sessions[half:] if s.get("target_miss") is not None]
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
