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
MIN_HEATING_RATE = 0.05
MAX_HEATING_RATE = 0.30
DEFAULT_TARGET_TEMP = 22
DEFAULT_TARGET_TIME = "06:00"

# ── TRV setpoint defaults ──────────────────────────────────────────────
DEFAULT_TRV_SETPOINT = 26.0   # °C — same as typical comfort target
MIN_TRV_SETPOINT = 5.0        # °C — frost protection floor
MAX_TRV_SETPOINT = 35.0       # °C — safe cap when HA max_temp unavailable

# ── Config keys ───────────────────────────────────────────────────────
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
CONF_INFLUXDB_URL = "influxdb_url"
CONF_INFLUXDB_TOKEN = "influxdb_token"
CONF_INFLUXDB_ORG = "influxdb_org"
CONF_INFLUXDB_BUCKET = "influxdb_bucket"
CONF_WEATHER_ENTITY = "weather_entity"

# ── Room config key (stored in entry.data) ────────────────────────────
CONF_ROOM_CONFIGS = "room_configs"

# ── Options keys ──────────────────────────────────────────────────────
CONF_DEBUG_LOGGING = "debug_logging"

# ── Blueprint tag ─────────────────────────────────────────────────────
# Automations created from the SHA blueprint embed this tag in their
# description field so SHA can discover rooms automatically.
# Format: sha:<room_name>|<temp_sensor>|<schedule1>,<schedule2>,...
BLUEPRINT_TAG_PREFIX = "sha:"

# ── Blueprint file ────────────────────────────────────────────────────
BLUEPRINT_FILENAME = "smart_heating_advisor.yaml"
BLUEPRINT_RELATIVE_PATH = "blueprints"

# ── Analysis schedule ─────────────────────────────────────────────────
DAILY_ANALYSIS_HOUR = 0
DAILY_ANALYSIS_MINUTE = 1
WEEKLY_ANALYSIS_WEEKDAY = 5  # Saturday
WEEKLY_ANALYSIS_HOUR = 1
WEEKLY_ANALYSIS_MINUTE = 0

# ── Seasons ───────────────────────────────────────────────────────────
SEASONS = {
    (12, 1, 2): "winter",
    (3, 4, 5): "spring",
    (6, 7, 8): "summer",
    (9, 10, 11): "autumn",
}
