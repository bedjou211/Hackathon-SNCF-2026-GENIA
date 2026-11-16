USE WAREHOUSE HACKATHON_WH;

CREATE OR REPLACE PROCEDURE LOAD_CURRENT_WEATHER(LAT FLOAT, LON FLOAT)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'main'
EXTERNAL_ACCESS_INTEGRATIONS = (OPEN_METEO_EAI)
PACKAGES = ('snowflake-snowpark-python', 'requests')
EXECUTE AS OWNER
AS
$$
import requests

WEATHER_LABELS = {
    0: "Ciel clair",
    1: "Plutot clair",
    2: "Partiellement nuageux",
    3: "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine legere",
    53: "Bruine moderee",
    55: "Bruine forte",
    61: "Pluie legere",
    63: "Pluie moderee",
    65: "Pluie forte",
    71: "Neige legere",
    73: "Neige moderee",
    75: "Neige forte",
    80: "Averses legeres",
    81: "Averses moderees",
    82: "Averses fortes",
    95: "Orage"
}

def main(session, LAT, LON):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,weather_code,is_day,precipitation,wind_speed_10m",
        "timezone": "Europe/Paris"
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    current = data.get("current", {})
    code = current.get("weather_code")
    label = WEATHER_LABELS.get(code, f"Code {code}")

    session.sql("""
        INSERT INTO WEATHER_CURRENT_LOG
            (LAT, LON, TEMPERATURE, WEATHER_CODE, WEATHER_LABEL, IS_DAY, PRECIPITATION, WIND_SPEED, OBSERVATION_TIME)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, params=[
        LAT, LON,
        current.get("temperature_2m"),
        code,
        label,
        current.get("is_day"),
        current.get("precipitation"),
        current.get("wind_speed_10m"),
        current.get("time")
    ]).collect()

    return f"OK - {label}, {current.get('temperature_2m')}C"
$$;
