"""Constants for Smart Heating Advisor."""

DOMAIN = "smart_heating_advisor"

# Ollama
DEFAULT_OLLAMA_URL = "http://192.168.187.195:11434"
DEFAULT_OLLAMA_MODEL = "phi4"
OLLAMA_TIMEOUT = 120  # seconds — phi4 can be slow

# InfluxDB
DEFAULT_INFLUXDB_URL = "http://192.168.187.195:8086"
DEFAULT_INFLUXDB_ORG = "home_assistant"
DEFAULT_INFLUXDB_BUCKET = "home_assistant"

# Heating defaults
DEFAULT_HEATING_RATE = 0.15
MIN_HEATING_RATE = 0.05
MAX_HEATING_RATE = 0.30
DEFAULT_TARGET_TEMP = 26
DEFAULT_TARGET_TIME = "06:00"

# Bathroom entities
TEMP_SENSOR = "sensor.bathroom_thermostat_temperature"
HEATING_RATE_HELPER = "input_number.bathroom_heating_rate"
WEATHER_ENTITY = "weather.forecast_home"

# Sensor names exposed to HA
SENSOR_HEATING_RATE = "sha_bathroom_heating_rate"
SENSOR_LAST_ANALYSIS = "sha_bathroom_last_analysis"
SENSOR_CONFIDENCE = "sha_bathroom_confidence"
SENSOR_WEEKLY_REPORT = "sha_bathroom_weekly_report"

# Config keys
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
CONF_INFLUXDB_URL = "influxdb_url"
CONF_INFLUXDB_TOKEN = "influxdb_token"
CONF_INFLUXDB_ORG = "influxdb_org"
CONF_INFLUXDB_BUCKET = "influxdb_bucket"
CONF_TEMP_SENSOR = "temp_sensor"
CONF_HEATING_RATE_HELPER = "heating_rate_helper"
CONF_TARGET_TEMP = "target_temp"
CONF_TARGET_TIME = "target_time"
CONF_WEATHER_ENTITY = "weather_entity"

# Analysis schedule
DAILY_ANALYSIS_HOUR = 2
DAILY_ANALYSIS_MINUTE = 0
WEEKLY_ANALYSIS_WEEKDAY = 6  # Sunday
WEEKLY_ANALYSIS_HOUR = 1
WEEKLY_ANALYSIS_MINUTE = 0

# Seasons
SEASONS = {
    (12, 1, 2): "winter",
    (3, 4, 5): "spring",
    (6, 7, 8): "summer",
    (9, 10, 11): "autumn"
}