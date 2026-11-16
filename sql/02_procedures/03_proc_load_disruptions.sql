USE WAREHOUSE HACKATHON_WH;

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

SNCF_LINES = {
    "IDFM:C01742", "IDFM:C01743", "IDFM:C01727",
    "IDFM:C01728", "IDFM:C01729", "IDFM:C01737",
    "IDFM:C01739", "IDFM:C01738", "IDFM:C01740",
    "IDFM:C01736", "IDFM:C01730", "IDFM:C01731",
    "IDFM:C01741",
}

def _is_sncf(sections):
    for s in (sections or []):
        lid = (s.get("lineId") or "").replace("line:", "")
        if lid in SNCF_LINES:
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

    skipped = 0
    for d in disruptions:
        sections = d.get("impactedSections") or []
        periods = d.get("applicationPeriods") or []

        if not _is_sncf(sections):
            skipped += 1
            continue

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
            [],
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

    _log(session, "ENRICH", "Enrichissement des arrets impactes via GTFS...")

    enrich_sql = """
        UPDATE IDFM_DISRUPTIONS d
        SET d.IMPACTED_STOPS = enriched.ALL_STOPS
        FROM (
            WITH sections_flat AS (
                SELECT
                    d2.ID_INCIDENT,
                    REPLACE(sec.VALUE:from.id::STRING, 'stop_area:', '') AS from_station,
                    REPLACE(sec.VALUE:to.id::STRING, 'stop_area:', '') AS to_station,
                    REPLACE(sec.VALUE:lineId::STRING, 'line:', '') AS route_id
                FROM IDFM_DISRUPTIONS d2,
                     LATERAL FLATTEN(INPUT => d2.IMPACTED_SECTIONS) sec
                WHERE ARRAY_SIZE(d2.IMPACTED_SECTIONS) > 0
            ),
            trips_valides AS (
                SELECT
                    sf.ID_INCIDENT,
                    sf.from_station,
                    sf.to_station,
                    sf.route_id,
                    st_a.TRIP_ID,
                    st_a.STOP_SEQUENCE AS SEQ_A,
                    st_b.STOP_SEQUENCE AS SEQ_B
                FROM sections_flat sf
                JOIN GTFS_STOPS gs_a ON gs_a.PARENT_STATION = sf.from_station
                JOIN GTFS_STOP_TIMES st_a ON st_a.STOP_ID = gs_a.STOP_ID
                JOIN GTFS_STOPS gs_b ON gs_b.PARENT_STATION = sf.to_station
                JOIN GTFS_STOP_TIMES st_b ON st_b.STOP_ID = gs_b.STOP_ID
                    AND st_b.TRIP_ID = st_a.TRIP_ID
                JOIN GTFS_TRIPS t ON t.TRIP_ID = st_a.TRIP_ID
                    AND t.ROUTE_ID = sf.route_id
                WHERE st_a.STOP_SEQUENCE < st_b.STOP_SEQUENCE
            ),
            best_trip AS (
                SELECT
                    ID_INCIDENT,
                    from_station,
                    to_station,
                    route_id,
                    TRIP_ID,
                    SEQ_A,
                    SEQ_B,
                    ROW_NUMBER() OVER (
                        PARTITION BY ID_INCIDENT, route_id, from_station, to_station
                        ORDER BY (SEQ_B - SEQ_A) DESC, TRIP_ID
                    ) AS rn
                FROM trips_valides
            ),
            stops_between AS (
                SELECT DISTINCT
                    bt.ID_INCIDENT,
                    s.PARENT_STATION,
                    s.STOP_NAME,
                    st.STOP_SEQUENCE
                FROM best_trip bt
                JOIN GTFS_STOP_TIMES st ON st.TRIP_ID = bt.TRIP_ID
                    AND st.STOP_SEQUENCE BETWEEN bt.SEQ_A AND bt.SEQ_B
                JOIN GTFS_STOPS s ON s.STOP_ID = st.STOP_ID
                WHERE bt.rn = 1
            ),
            unique_stops AS (
                SELECT
                    ID_INCIDENT,
                    PARENT_STATION,
                    STOP_NAME,
                    MIN(STOP_SEQUENCE) AS STOP_SEQUENCE
                FROM stops_between
                GROUP BY ID_INCIDENT, PARENT_STATION, STOP_NAME
            )
            SELECT
                ID_INCIDENT,
                ARRAY_AGG(
                    OBJECT_CONSTRUCT('parent_station', PARENT_STATION, 'stop_name', STOP_NAME)
                ) WITHIN GROUP (ORDER BY STOP_SEQUENCE) AS ALL_STOPS
            FROM unique_stops
            GROUP BY ID_INCIDENT
        ) enriched
        WHERE d.ID_INCIDENT = enriched.ID_INCIDENT
    """

    try:
        session.sql(enrich_sql).collect()
        _log(session, "ENRICH_OK", "Enrichissement termine")
    except Exception as e:
        _log(session, "ENRICH_ERR", f"Erreur enrichissement: {e}")

    _log(session, "DONE", f"{len(rows)} disruptions inserees, {skipped} non-SNCF ignorees")
    return f"OK: {len(rows)} disruptions SNCF chargees dans IDFM_DISRUPTIONS ({skipped} non-SNCF ignorees)"
$$;
