USE WAREHOUSE HACKATHON_WH;

CREATE OR REPLACE PROCEDURE LOAD_PREDICTHQ_EVENTS()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'main'
EXTERNAL_ACCESS_INTEGRATIONS = (PREDICTHQ_EAI)
SECRETS = ('api_key' = PREDICTHQ_API_KEY)
PACKAGES = ('snowflake-snowpark-python', 'requests')
EXECUTE AS OWNER
AS
$$
import requests
import _snowflake
from datetime import datetime, timezone, timedelta

def main(session):
    token = _snowflake.get_generic_secret_string('api_key')
    url = "https://api.predicthq.com/v1/events/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "category": "concerts,sports,conferences,expos,community,severe-weather,public-holidays",
        "active.gte": start,
        "active.lte": end,
        "within": "50km@48.8566,2.3522",
        "sort": "start",
        "limit": 100
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("results", [])
    if not events:
        return "0 evenements trouves"

    existing = set()
    try:
        rows = session.sql("SELECT DISTINCT EVENT_ID FROM EVENTS_PREDICTHQ_LOG").collect()
        existing = {r["EVENT_ID"] for r in rows}
    except Exception:
        pass

    inserted = 0
    for e in events:
        eid = e.get("id", "")
        if eid in existing:
            continue

        start_dt = e.get("start")
        end_dt = e.get("end")
        duration = None
        if start_dt and end_dt:
            try:
                s = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                en = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                duration = round((en - s).total_seconds() / 3600, 2)
            except Exception:
                pass

        loc = e.get("location", [])
        lat = loc[1] if len(loc) > 1 else None
        lon = loc[0] if len(loc) > 0 else None

        labels = ", ".join(e.get("labels", []))

        session.sql("""
            INSERT INTO EVENTS_PREDICTHQ_LOG
                (EVENT_ID, TITLE, CATEGORY, START_TIME, END_TIME, DURATION_HOURS,
                 RANK, LOCAL_RANK, LOCATION_LAT, LOCATION_LON, COUNTRY, LABELS, DESCRIPTION)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, params=[
            eid,
            e.get("title"),
            e.get("category"),
            start_dt,
            end_dt,
            duration,
            e.get("rank"),
            e.get("local_rank"),
            lat,
            lon,
            e.get("country"),
            labels,
            (e.get("description") or "")[:1000]
        ]).collect()
        inserted += 1
        existing.add(eid)

    return f"{inserted} nouveaux evenements inseres, {len(events)} trouves au total"
$$;
