USE WAREHOUSE HACKATHON_WH;

CREATE OR REPLACE PROCEDURE LOAD_IDFM_STOP_MONITORING_ALL_SNCF()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'main'
EXTERNAL_ACCESS_INTEGRATIONS = (IDFM_API_INTEGRATION)
SECRETS = ('api_key' = IDFM_API_KEY)
PACKAGES = ('snowflake-snowpark-python', 'requests')
EXECUTE AS OWNER
AS
$$
import requests
import _snowflake
import time
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

def _safe_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

def _get_text(field):
    if field is None:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, dict):
        return field.get("value") or field.get("Value")
    if isinstance(field, list) and field:
        first = field[0]
        if isinstance(first, dict):
            return first.get("value") or first.get("Value")
        if isinstance(first, str):
            return first
    return None

def _first(*values):
    for v in values:
        if v:
            return v
    return None

def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def _delay_min(aimed, expected):
    if not aimed or not expected:
        return None
    try:
        dt_a = datetime.fromisoformat(aimed.replace("Z", "+00:00"))
        dt_e = datetime.fromisoformat(expected.replace("Z", "+00:00"))
        return round((dt_e - dt_a).total_seconds() / 60, 2)
    except Exception:
        return None

def _fetch_with_retry(url, headers, params, max_retries=3):
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        if resp.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    return None



SCHEMA = [
    "INGESTED_AT","MONITORING_REF","MONITORED_STOP","LINE_REF","LINE_NAME",
    "DESTINATION","DIRECTION","VEHICLE_JOURNEY_REF","VEHICLE_JOURNEY_NAME",
    "CALL_STOP","PLATFORM",
    "EXPECTED_ARRIVAL_TIME","EXPECTED_DEPARTURE_TIME",
    "AIMED_ARRIVAL_TIME","AIMED_DEPARTURE_TIME",
    "DELAY_ARRIVAL_MIN","DELAY_DEPARTURE_MIN",
    "ARRIVAL_STATUS","DEPARTURE_STATUS","RAW_PAYLOAD"
]

def _fetch_line(base_url, headers, line_ref, line_name, stop_names, ingested_at):
    rows = []
    try:
        payload = _fetch_with_retry(base_url, headers, {"LineRef": line_ref})
        if payload is None:
            return rows, 1

        siri = payload.get("Siri", payload)
        sd = siri.get("ServiceDelivery", siri)
        for delivery in _safe_list(sd.get("EstimatedTimetableDelivery", [])):
            for frame in _safe_list(delivery.get("EstimatedJourneyVersionFrame", [])):
                for journey in _safe_list(frame.get("EstimatedVehicleJourney", [])):
                    dest_name = _get_text(journey.get("DestinationName"))
                    direction = _first(_get_text(journey.get("DirectionName")), _get_text(journey.get("DirectionRef")))
                    journey_ref = _get_text(journey.get("DatedVehicleJourneyRef"))
                    journey_name = _get_text(journey.get("VehicleJourneyName"))

                    calls_container = journey.get("EstimatedCalls") or journey.get("RecordedCalls") or {}
                    if isinstance(calls_container, dict):
                        calls = _safe_list(
                            calls_container.get("EstimatedCall",
                            calls_container.get("RecordedCall", []))
                        )
                    else:
                        calls = _safe_list(calls_container)

                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        stop_ref = _get_text(call.get("StopPointRef")) or ""
                        stop_name = stop_names.get(stop_ref) or _get_text(call.get("StopPointName"))
                        aimed_arr = call.get("AimedArrivalTime")
                        aimed_dep = call.get("AimedDepartureTime")
                        expected_arr = call.get("ExpectedArrivalTime")
                        expected_dep = call.get("ExpectedDepartureTime")

                        rows.append((
                            ingested_at,
                            stop_ref,
                            stop_name,
                            line_ref,
                            line_name,
                            _first(dest_name, _get_text(call.get("DestinationDisplay"))),
                            direction,
                            journey_ref,
                            journey_name,
                            _first(_get_text(call.get("StopPointName")), stop_ref),
                            _first(_get_text(call.get("ArrivalPlatformName")), _get_text(call.get("DeparturePlatformName"))),
                            _parse_iso(expected_arr),
                            _parse_iso(expected_dep),
                            _parse_iso(aimed_arr),
                            _parse_iso(aimed_dep),
                            _delay_min(aimed_arr, expected_arr),
                            _delay_min(aimed_dep, expected_dep),
                            _get_text(call.get("ArrivalStatus")),
                            _get_text(call.get("DepartureStatus")),
                            {"call": call}
                        ))
        return rows, 0
    except Exception:
        return rows, 1

def main(session):
    api_key = _snowflake.get_generic_secret_string("api_key")
    base_url = "https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable"
    headers = {"Accept": "application/json", "apiKey": api_key}
    ingested_at = datetime.now(timezone.utc)

    stop_names = {}
    try:
        rows = session.sql("SELECT MONITORING_REF, STOP_NAME FROM IDFM_REF_STOPS_SNCF").collect()
        for r in rows:
            stop_names[r["MONITORING_REF"]] = r["STOP_NAME"]
    except Exception:
        pass

    all_rows = []
    errors = 0

    sncf_lines = []
    try:
        rows = session.sql("SELECT LINE_REF, LINE_NAME FROM IDFM_REF_LINES_SNCF").collect()
        sncf_lines = [(r["LINE_REF"], r["LINE_NAME"]) for r in rows]
    except Exception:
        pass

    if not sncf_lines:
        return "0 passages, 0 lignes (table IDFM_REF_LINES_SNCF vide)"

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_line, base_url, headers, ref, name, stop_names, ingested_at): name
            for ref, name in sncf_lines
        }
        for future in as_completed(futures):
            rows, err = future.result()
            all_rows.extend(rows)
            errors += err

    if all_rows:
        session.sql("DELETE FROM IDFM_STOP_MONITORING_HISTORY WHERE SNAPSHOT_ID = 3").collect()
        session.sql("UPDATE IDFM_STOP_MONITORING_HISTORY SET SNAPSHOT_ID = 3 WHERE SNAPSHOT_ID = 2").collect()
        session.sql("UPDATE IDFM_STOP_MONITORING_HISTORY SET SNAPSHOT_ID = 2 WHERE SNAPSHOT_ID = 1").collect()
        session.sql("""
            INSERT INTO IDFM_STOP_MONITORING_HISTORY
            SELECT 1, * FROM IDFM_STOP_MONITORING_CURRENT
        """).collect()
        session.sql("TRUNCATE TABLE IDFM_STOP_MONITORING_CURRENT").collect()
        for i in range(0, len(all_rows), 1000):
            session.create_dataframe(
                all_rows[i:i+1000], schema=SCHEMA
            ).write.mode("append").save_as_table(
                "IDFM_STOP_MONITORING_CURRENT", column_order="name"
            )

    return f"{len(all_rows)} passages, {len(sncf_lines)} lignes, {errors} err"
$$;
