from snowflake.snowpark.context import get_active_session

session = get_active_session()
session.sql("USE ROLE M2_ISD_EQUIPE_4_ROLE").collect()
session.sql("USE DATABASE M2_ISD_EQUIPE_4_DB").collect()
session.sql("USE SCHEMA PUBLIC").collect()
session.sql("USE WAREHOUSE HACKATHON_WH").collect()

session.sql("""
CREATE TABLE IF NOT EXISTS IDFM_DISRUPTIONS (
    INGESTED_AT           TIMESTAMP_TZ,
    ID_INCIDENT           STRING,
    UPDATED_AT            TIMESTAMP_TZ,
    IS_ACTIVE             BOOLEAN,
    CAUSE                 STRING,
    SEVERITY              STRING,
    TITLE                 STRING,
    SHORT_MESSAGE         STRING,
    FULL_MESSAGE_HTML     STRING,
    LINE_IDS              VARIANT,
    IMPACTED_STOPS        VARIANT,
    APPLICATION_PERIODS   VARIANT,
    IMPACTED_SECTIONS     VARIANT,
    RAW_PAYLOAD           VARIANT
)
""").collect()

session.sql('''
CREATE OR REPLACE PROCEDURE LOAD_IDFM_DISRUPTIONS()
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
from datetime import datetime, timezone

SCHEMA = [
    "INGESTED_AT", "ID_INCIDENT", "UPDATED_AT", "IS_ACTIVE", "CAUSE", "SEVERITY",
    "TITLE", "SHORT_MESSAGE", "FULL_MESSAGE_HTML",
    "LINE_IDS", "IMPACTED_STOPS",
    "APPLICATION_PERIODS", "IMPACTED_SECTIONS", "RAW_PAYLOAD"
]

def _log(session, step, message):
    msg = str(message).replace("'", "''")[:4000]
    stp = str(step).replace("'", "''")
    session.sql(f"""
        INSERT INTO IDFM_STOP_MONITORING_LOG (LOGGED_AT, MON_REF, STEP, MESSAGE)
        VALUES (CURRENT_TIMESTAMP(), 'DISRUPTIONS', '{stp}', '{msg}')
    """).collect()

def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.strptime(str(val), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _is_active(periods):
    now = datetime.now(timezone.utc)
    for p in (periods or []):
        begin = _parse_dt(p.get("begin"))
        end = _parse_dt(p.get("end"))
        if begin and end and begin <= now <= end:
            return True
    return False

def _extract_line_ids(sections):
    seen = set()
    result = []
    for s in sections:
        lid = s.get("lineId")
        if lid and lid not in seen:
            seen.add(lid)
            result.append(lid)
    return result

def _extract_stops(sections):
    seen = set()
    result = []
    for s in sections:
        for key in ("from", "to"):
            stop = s.get(key) or {}
            sid = stop.get("id")
            if sid and sid not in seen:
                seen.add(sid)
                result.append({"id": sid, "name": stop.get("name")})
    return result

def main(session):
    api_key = _snowflake.get_generic_secret_string("api_key")
    url = "https://prim.iledefrance-mobilites.fr/marketplace/disruptions_bulk/disruptions/v2"
    headers = {"Accept": "application/json", "apiKey": api_key}

    _log(session, "START", f"Appel API: {url}")

    try:
        resp = requests.get(url, headers=headers, timeout=120)
    except Exception as e:
        _log(session, "ERROR", f"Requete echouee: {e}")
        return f"ERROR: {e}"

    if resp.status_code != 200:
        _log(session, "ERROR", f"HTTP {resp.status_code}: {resp.text[:500]}")
        return f"ERROR: HTTP {resp.status_code}"

    data = resp.json()

    disruptions = data.get("disruptions", []) if isinstance(data, dict) else data
    if not isinstance(disruptions, list):
        return "ERROR: format reponse inattendu"

    _log(session, "PARSE", f"{len(disruptions)} disruptions trouvees")

    session.sql("TRUNCATE TABLE IF EXISTS IDFM_DISRUPTIONS").collect()

    ingested_at = datetime.now(timezone.utc)
    rows = []

    for d in disruptions:
        sections = d.get("impactedSections") or []
        periods = d.get("applicationPeriods") or []

        rows.append((
            ingested_at,
            d.get("id"),
            _parse_dt(d.get("lastUpdate")),
            _is_active(periods),
            d.get("cause"),
            d.get("severity"),
            d.get("title"),
            d.get("shortMessage"),
            d.get("message"),
            _extract_line_ids(sections),
            _extract_stops(sections),
            periods,
            sections,
            d
        ))

    if rows:
        for i in range(0, len(rows), 500):
            session.create_dataframe(
                rows[i:i+500], schema=SCHEMA
            ).write.mode("append").save_as_table(
                "IDFM_DISRUPTIONS", column_order="name"
            )

    _log(session, "DONE", f"{len(rows)} disruptions inserees")
    return f"OK: {len(rows)} disruptions chargees dans IDFM_DISRUPTIONS"
$$
''').collect()

result = session.sql("CALL LOAD_IDFM_DISRUPTIONS()").collect()
print(result[0][0])
