"""Constants for Smart Heating Advisor."""

DOMAIN = "smart_heating_advisor"

# ── Ollama ────────────────────────────────────────────────────────────
DEFAULT_OLLAMA_URL = "http://192.168.187.195:11434"
DEFAULT_OLLAMA_MODEL = "phi4"
OLLAMA_TIMEOUT = 120  # seconds — phi4 can be slow

# ── InfluxDB ──────────────────────────────────────────────────────────
DEFAULT_INFLUXDB_URL = "http://192.168.187.195:8086"
DEFAULT_INFLUXDB_ORG = "home_assistant"
DEFAULT_INFLUXDB_BUCKET = "home_assistant"

# ── Heating defaults ──────────────────────────────────────────────────
DEFAULT_HEATING_RATE = 0.08
MIN_HEATING_RATE = 0.01
MAX_HEATING_RATE = 0.30

# ── TRV setpoint defaults ──────────────────────────────────────────────
DEFAULT_TRV_SETPOINT = 26.0   # °C — same as typical comfort target
MIN_TRV_SETPOINT = 5.0        # °C — frost protection floor
MAX_TRV_SETPOINT = 35.0       # °C — safe cap when HA max_temp unavailable

# ── Comfort temperature defaults ──────────────────────────────────────
CONF_COMFORT_TEMP = "comfort_temp"
DEFAULT_COMFORT_TEMP = 18.0
MIN_COMFORT_TEMP = 4.0
MAX_COMFORT_TEMP = 35.0

# Backward-compat aliases — used by Phase 3 / Phase 3b migration code
DEFAULT_DEFAULT_TEMP = DEFAULT_COMFORT_TEMP
MIN_DEFAULT_TEMP = MIN_COMFORT_TEMP
MAX_DEFAULT_TEMP = MAX_COMFORT_TEMP

# ── Config keys ───────────────────────────────────────────────────────
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
CONF_INFLUXDB_URL = "influxdb_url"
CONF_INFLUXDB_TOKEN = "influxdb_token"
CONF_INFLUXDB_ORG = "influxdb_org"
CONF_INFLUXDB_BUCKET = "influxdb_bucket"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_OUTSIDE_TEMP_SENSOR = "outside_temp_sensor"

# ── Room config key (stored in entry.data) ────────────────────────────
CONF_ROOM_CONFIGS = "room_configs"

# ── Options keys ──────────────────────────────────────────────────────
CONF_DEBUG_LOGGING = "debug_logging"

# ── Vacation config keys ───────────────────────────────────────────────
CONF_VACATION_ENABLED = "vacation_enabled"
CONF_VACATION_MODE = "vacation_mode"
CONF_VACATION_START_DATE = "vacation_start_date"
CONF_VACATION_END_DATE = "vacation_end_date"
DEFAULT_VACATION_MODE = "frost"

# ── Room wizard defaults ───────────────────────────────────────────────
DEFAULT_AIRING_DURATION = 2         # minutes — used in Phase 3b migration conversion
DEFAULT_HUMIDITY_THRESHOLD = 70.0   # % — used in Phase 3 migration defaults only

# ── Blueprint / automation versioning ────────────────────────────────
# Bump this whenever the inline automation content changes so SHA
# automatically recreates outdated automations on next startup.
SHA_AUTOMATION_VERSION = "0.0.20"

# ── Blueprint file ────────────────────────────────────────────────────
BLUEPRINT_FILENAME = "smart_heating_advisor.yaml"
BLUEPRINT_RELATIVE_PATH = "blueprints"

# ── Analysis schedule ─────────────────────────────────────────────────
DAILY_ANALYSIS_HOUR = 0
DAILY_ANALYSIS_MINUTE = 1
WEEKLY_ANALYSIS_WEEKDAY = 6  # Sunday
WEEKLY_ANALYSIS_HOUR = 1
WEEKLY_ANALYSIS_MINUTE = 0
