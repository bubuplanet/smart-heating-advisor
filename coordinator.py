"""Coordinator for Smart Heating Advisor."""
import logging
from datetime import datetime, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_OLLAMA_URL,
    CONF_OLLAMA_MODEL,
    CONF_INFLUXDB_URL,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_BUCKET,
    CONF_TEMP_SENSOR,
    CONF_HEATING_RATE_HELPER,
    CONF_WEATHER_ENTITY,
    DEFAULT_HEATING_RATE,
    MIN_HEATING_RATE,
    MAX_HEATING_RATE,
    OLLAMA_TIMEOUT,
)
from .ollama import OllamaClient
from .analyzer import (
    analyze_heating_sessions,
    build_daily_prompt,
    build_weekly_prompt,
    get_season,
)

_LOGGER = logging.getLogger(__name__)


class SmartHeatingCoordinator:
    """Coordinates data fetching, analysis and HA state updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize coordinator."""
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self._entities = []

        # Current state exposed to sensors
        self.heating_rate = DEFAULT_HEATING_RATE
        self.last_analysis = None
        self.confidence = "unknown"
        self.weekly_report = "No report yet."

        # Ollama client
        self.ollama = OllamaClient(
            url=self.config[CONF_OLLAMA_URL],
            model=self.config[CONF_OLLAMA_MODEL],
            timeout=OLLAMA_TIMEOUT,
        )

    def register_entities(self, entities: list):
        """Register sensor entities for state updates."""
        self._entities = entities

    async def async_update_sensors(self):
        """Push updated state to all registered sensor entities."""
        for entity in self._entities:
            entity.async_write_ha_state()

    # ------------------------------------------------------------------
    # InfluxDB
    # ------------------------------------------------------------------

    async def async_query_influxdb(self, days: int) -> list[tuple]:
        """Query InfluxDB for temperature readings over last N days."""
        import aiohttp

        token = self.config[CONF_INFLUXDB_TOKEN]
        url = self.config[CONF_INFLUXDB_URL]
        org = self.config[CONF_INFLUXDB_ORG]
        bucket = self.config[CONF_INFLUXDB_BUCKET]
        sensor = self.config.get(
            CONF_TEMP_SENSOR, "sensor.bathroom_thermostat_temperature"
        )

        entity_id = sensor.replace("sensor.", "")

        flux_query = f"""
from(bucket: "{bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["entity_id"] == "{entity_id}")
  |> filter(fn: (r) => r["_field"] == "value")
  |> filter(fn: (r) => r["_measurement"] == "°C")
  |> sort(columns: ["_time"])
  |> yield(name: "data")
"""

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{url}/api/v2/query",
                    params={"org": org},
                    headers=headers,
                    data=flux_query,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "InfluxDB query failed with status %s", response.status
                        )
                        return []
                    csv_text = await response.text()
                    return self._parse_influxdb_csv(csv_text)

        except Exception as e:
            _LOGGER.error("InfluxDB query error: %s", e)
            return []

    def _parse_influxdb_csv(self, csv_text: str) -> list[tuple]:
        """Parse InfluxDB CSV response into (datetime, float) tuples."""
        readings = []
        lines = csv_text.strip().split("\n")
        time_idx = None
        value_idx = None

        for line in lines:
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if "_time" in parts and "_value" in parts:
                time_idx = parts.index("_time")
                value_idx = parts.index("_value")
                continue
            if time_idx is None or value_idx is None:
                continue
            try:
                ts = parts[time_idx].strip().replace("Z", "+00:00")
                val = float(parts[value_idx].strip())
                readings.append((datetime.fromisoformat(ts), val))
            except (ValueError, IndexError):
                continue

        return sorted(readings, key=lambda x: x[0])

    # ------------------------------------------------------------------
    # Weather
    # ------------------------------------------------------------------

    def _get_weather_data(self) -> dict:
        """Get current and forecast weather from HA weather entity."""
        weather_entity = self.config.get(
            CONF_WEATHER_ENTITY, "weather.forecast_home"
        )
        state = self.hass.states.get(weather_entity)

        if not state:
            _LOGGER.warning("Weather entity %s not found", weather_entity)
            return {
                "outside_temp": 10.0,
                "tomorrow_min": 5.0,
                "tomorrow_max": 15.0,
            }

        outside_temp = state.attributes.get("temperature", 10.0)
        forecast = state.attributes.get("forecast", [])
        tomorrow_min = 5.0
        tomorrow_max = 15.0

        if len(forecast) > 1:
            tomorrow = forecast[1]
            tomorrow_min = tomorrow.get("templow", tomorrow.get("temperature", 5.0))
            tomorrow_max = tomorrow.get("temperature", 15.0)

        return {
            "outside_temp": float(outside_temp),
            "tomorrow_min": float(tomorrow_min),
            "tomorrow_max": float(tomorrow_max),
        }

    # ------------------------------------------------------------------
    # Apply results to HA
    # ------------------------------------------------------------------

    async def _async_apply_heating_rate(self, rate: float, reasoning: str):
        """Update input_number helper and notify."""
        rate = max(MIN_HEATING_RATE, min(MAX_HEATING_RATE, rate))
        helper = self.config.get(
            CONF_HEATING_RATE_HELPER, "input_number.bathroom_heating_rate"
        )

        await self.hass.services.async_call(
            "input_number",
            "set_value",
            {"entity_id": helper, "value": round(rate, 3)},
        )

        self.heating_rate = rate
        self.last_analysis = datetime.now(timezone.utc).isoformat()

        # Push updated state to sensors
        await self.async_update_sensors()

        _LOGGER.info(
            "Heating rate updated to %.3f°C/min. Reason: %s", rate, reasoning
        )

    async def _async_notify(self, title: str, message: str):
        """Send HA mobile notification."""
        await self.hass.services.async_call(
            "notify",
            "notify",
            {"title": title, "message": message},
        )

    async def _async_persistent_notification(
        self, title: str, message: str, notification_id: str
    ):
        """Create or update a persistent notification in HA UI."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": title,
                "message": message,
                "notification_id": notification_id,
            },
        )

    # ------------------------------------------------------------------
    # Daily analysis
    # ------------------------------------------------------------------

    async def async_run_daily_analysis(self):
        """Run daily heating rate analysis."""
        _LOGGER.info("Starting daily heating analysis")

        # 1. Test Ollama connection
        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                "Daily analysis skipped — cannot connect to Ollama."
            )
            return

        # 2. Fetch last 7 days from InfluxDB
        readings = await self.async_query_influxdb(days=7)
        if len(readings) < 5:
            _LOGGER.warning(
                "Not enough InfluxDB data for daily analysis (%d readings)",
                len(readings)
            )
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                f"Daily analysis skipped — only {len(readings)} data points found."
            )
            return

        # 3. Analyse heating sessions
        analysis = analyze_heating_sessions(readings)
        if not analysis["sessions"]:
            _LOGGER.warning("No heating sessions detected in last 7 days")
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                f"Daily analysis skipped — no heating sessions detected. "
                f"({len(readings)} data points found)"
            )
            return

        # 4. Get weather
        weather = self._get_weather_data()
        season = get_season(datetime.now().month)

        # 5. Get current heating rate
        helper = self.config.get(
            CONF_HEATING_RATE_HELPER, "input_number.bathroom_heating_rate"
        )
        state = self.hass.states.get(helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE

        # 6. Build prompt and call Ollama
        prompt = build_daily_prompt(
            current_rate=current_rate,
            analysis=analysis,
            outside_temp=weather["outside_temp"],
            tomorrow_min=weather["tomorrow_min"],
            tomorrow_max=weather["tomorrow_max"],
            season=season,
        )

        _LOGGER.debug("Sending daily prompt to Ollama")
        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result or "heating_rate" not in result:
            _LOGGER.error("Invalid Ollama response: %s", response)
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                "Daily analysis failed — invalid response from Ollama."
            )
            return

        # 7. Apply new rate
        new_rate = float(result["heating_rate"])
        reasoning = result.get("reasoning", "No reasoning provided")
        confidence = result.get("confidence", "unknown")
        self.confidence = confidence

        await self._async_apply_heating_rate(new_rate, reasoning)

        # 8. Send mobile notification
        await self._async_notify(
            "🌡️ Bathroom Heating Rate Updated",
            f"New rate: {new_rate:.3f}°C/min (was {current_rate:.3f}°C/min)\n"
            f"Confidence: {confidence}\n"
            f"Success rate last 7 days: {analysis['success_rate']}%\n"
            f"Reason: {reasoning}"
        )

        _LOGGER.info("Daily analysis complete. New rate: %.3f", new_rate)

    # ------------------------------------------------------------------
    # Weekly analysis — report only, no rate adjustment
    # ------------------------------------------------------------------

    async def async_run_weekly_analysis(self):
        """Run weekly deep heating analysis — report only."""
        _LOGGER.info("Starting weekly heating analysis (report only)")

        # 1. Test Ollama
        if not await self.ollama.async_test_connection():
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                "Weekly report skipped — cannot connect to Ollama."
            )
            return

        # 2. Fetch last 30 days
        readings = await self.async_query_influxdb(days=30)
        if len(readings) < 5:
            _LOGGER.warning("Not enough data for weekly analysis")
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                f"Weekly report skipped — only {len(readings)} data points found."
            )
            return

        # 3. Analyse
        analysis = analyze_heating_sessions(readings)
        weather = self._get_weather_data()
        season = get_season(datetime.now().month)
        avg_outside_temp = weather["outside_temp"]

        # 4. Get current rate
        helper = self.config.get(
            CONF_HEATING_RATE_HELPER, "input_number.bathroom_heating_rate"
        )
        state = self.hass.states.get(helper)
        current_rate = float(state.state) if state else DEFAULT_HEATING_RATE

        # 5. Build prompt and call Ollama
        prompt = build_weekly_prompt(
            current_rate=current_rate,
            analysis=analysis,
            avg_outside_temp=avg_outside_temp,
            season=season,
        )

        _LOGGER.debug("Sending weekly prompt to Ollama")
        response = await self.ollama.async_generate(prompt)
        result = await self.ollama.async_parse_json_response(response)

        if not result:
            _LOGGER.error("Invalid weekly Ollama response: %s", response)
            await self._async_notify(
                "⚠️ Smart Heating Advisor",
                "Weekly report failed — invalid response from Ollama."
            )
            return

        # 6. Extract report — NO rate adjustment
        confidence = result.get("confidence", "unknown")
        weekly_report = result.get(
            "weekly_report", "No weekly report generated."
        )
        suggested_rate = result.get("heating_rate", current_rate)
        reasoning = result.get("reasoning", "")

        # Update sensor state only
        self.confidence = confidence
        self.weekly_report = weekly_report
        await self.async_update_sensors()

        # 7. Create persistent notification in HA UI
        report_date = datetime.now().strftime("%Y-%m-%d")
        await self._async_persistent_notification(
            title=f"📊 Bathroom Heating Weekly Report — {report_date}",
            message=(
                f"## Weekly Summary\n"
                f"{weekly_report}\n\n"
                f"## Statistics (last 30 days)\n"
                f"- Sessions analyzed: {analysis['days_analyzed']}\n"
                f"- Success rate: {analysis['success_rate']}%\n"
                f"- Avg heating rate observed: {analysis.get('avg_rate', 'unknown')}°C/min\n"
                f"- Avg pre-heat start time: {analysis.get('avg_start_time', 'unknown')}\n"
                f"- Outside temp: {avg_outside_temp}°C ({season})\n\n"
                f"## AI Suggestion\n"
                f"Suggested rate: {suggested_rate:.3f}°C/min (current: {current_rate:.3f}°C/min)\n"
                f"Confidence: {confidence}\n"
                f"Reasoning: {reasoning}\n\n"
                f"_Note: Rate is NOT automatically adjusted by weekly report. "
                f"Apply manually if you agree with the suggestion._"
            ),
            notification_id="sha_bathroom_weekly_report",
        )

        _LOGGER.info("Weekly report complete. Suggested rate: %.3f", suggested_rate)