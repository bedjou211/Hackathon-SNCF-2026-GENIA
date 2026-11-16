"""
data_loader.py
Charge les KPIs depuis la table Snowflake KPI_UNIFIED
et les formate pour le pipeline run_batch_pipeline()
"""

from snowflake.snowpark.context import get_active_session
import pandas as pd
import time

TABLE = "M2_ISD_EQUIPE_4_DB.PUBLIC.KPI_UNIFIED"
BATCH_WINDOW = 5


def load_latest_batch() -> list:
    t0 = time.perf_counter()
    session = get_active_session()
    t1 = time.perf_counter()
    print(f"[PERF] get_active_session: {(t1 - t0) * 1000:.1f} ms")

    query = f"""
        WITH filtered AS (
            SELECT *
            FROM {TABLE}
            WHERE BATCH_ID = (SELECT MAX(BATCH_ID) FROM {TABLE})
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY LINE_REF, MONITORING_REF, DESTINATION
                       ORDER BY BATCH_ID DESC, CALCULATED_AT DESC
                   ) AS rn
            FROM filtered
        )
        SELECT *
        FROM ranked
        WHERE rn = 1
        ORDER BY LINE_REF, MONITORING_REF
    """

    t2 = time.perf_counter()
    df = session.sql(query).to_pandas()
    t3 = time.perf_counter()
    print(f"[PERF] SQL + to_pandas load_latest_batch: {(t3 - t2) * 1000:.1f} ms")

    if df.empty:
        print("[data_loader] ⚠️ Table KPI_UNIFIED vide.")
        return []

    t4 = time.perf_counter()
    df.columns = df.columns.str.lower()

    print("=" * 60)
    print(f"[data_loader] {len(df)} triplets chargés")
    print(f"[data_loader] BATCH_ID : {df['batch_id'].min()} → {df['batch_id'].max()}")
    print("-" * 60)
    for i, row in df.iterrows():
        print(f"Ligne {i+1} : {row.to_dict()}")
    print("=" * 60)

    kpis = _to_kpi_list(df)
    t5 = time.perf_counter()

    print(f"[PERF] normalize columns + _to_kpi_list: {(t5 - t4) * 1000:.1f} ms")
    print(f"[PERF] load_latest_batch TOTAL: {(t5 - t0) * 1000:.1f} ms")

    return kpis


def load_full_history(limit_batches=2000):
    session = get_active_session()

    df = session.sql(f"""
        SELECT *
        FROM {TABLE}
        WHERE BATCH_ID >= (
            SELECT MAX(BATCH_ID) - {limit_batches}
            FROM {TABLE}
        )
        ORDER BY BATCH_ID ASC, CALCULATED_AT ASC
    """).to_pandas()

    df.columns = df.columns.str.lower()

    return _to_kpi_list(df)


def load_kpis_from_snowflake(minutes_back: int = 30, limit: int = 100) -> list:
    session = get_active_session()

    df = session.sql(f"""
        WITH filtered AS (
            SELECT *
            FROM {TABLE}
            WHERE CALCULATED_AT >= DATEADD('minute', -{minutes_back}, CURRENT_TIMESTAMP())
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY LINE_REF, MONITORING_REF, DESTINATION
                       ORDER BY BATCH_ID DESC, CALCULATED_AT DESC
                   ) AS rn
            FROM filtered
        )
        SELECT *
        FROM ranked
        WHERE rn = 1
        ORDER BY BATCH_ID DESC
        LIMIT {limit}
    """).to_pandas()

    if df.empty:
        print("[data_loader] ⚠️ Aucune donnée dans la fenêtre temporelle.")
        return []

    df.columns = df.columns.str.lower()

    print("=" * 60)
    print(f"[data_loader] {len(df)} triplets chargés (fenêtre {minutes_back} min)")
    print("-" * 60)
    for i, row in df.iterrows():
        print(f"Ligne {i+1} : {row.to_dict()}")
    print("=" * 60)

    return _to_kpi_list(df)


def get_latest_batch_id() -> int:
    session = get_active_session()
    result = session.sql(f"SELECT MAX(BATCH_ID) AS latest FROM {TABLE}").to_pandas()
    return int(result["LATEST"].iloc[0] or 0)


def _normalize_percent_ratio(value, default=0.0) -> float:
    try:
        val = float(value if value is not None else default)
        return val / 100.0 if val > 1 else val
    except (TypeError, ValueError):
        return default


def _to_kpi_list(df) -> list:
    kpi_list = []
    required_fields = [
        "kpi1_taux_suppression",
        "kpi2_retard_moyen_min",
        "kpi3_ratio_attente",
        "kpi4_ratio_stationnement",
    ]

    for _, row in df.iterrows():
        if not all(pd.notna(row.get(f)) for f in required_fields):
            continue

        destination_name = str(row.get("destination") or "")
        line_id = str(row.get("line_ref") or "")
        stop_id = str(row.get("monitoring_ref") or "")
        line_name = _normalize_line(line_id)
        stop_name = str(row.get("monitored_stop") or row.get("monitoring_ref") or "")
        raw_name = row.get("line_name")
        line_name_label = (
            str(raw_name)
            if raw_name and str(raw_name) not in ("None", "nan", "NaN")
            else line_name
        )

        suppression_rate = _normalize_percent_ratio(
            row.get("kpi1_taux_suppression"),
            default=0.0
        )

        kpi = {
            "timestamp": str(row.get("calculated_at") or ""),
            "line_id": line_id,
            "stop_id": stop_id,
            "destination_id": destination_name,
            "line_name": line_name_label,
            "stop_name": stop_name,
            "destination_name": destination_name,
            "line": line_name,
            "direction": destination_name,
            "destination": destination_name,
            "suppression_rate": suppression_rate,
            "delay_mean_min": float(row.get("kpi2_retard_moyen_min") or 0),
            "wait_ratio": float(row.get("kpi3_ratio_attente") or 1.0),
            "dwell_anomaly_min": float(row.get("kpi4_ratio_stationnement") or 1.0),
            "headway_ratio": 1.0,
            "batch_id": int(row.get("batch_id") or 0),
            "total_trains": int(row.get("kpi1_total_trains") or 0),
            "trains_annules": int(row.get("kpi1_trains_annules") or 0),
        }

        kpi_list.append(kpi)

    print(f"[data_loader] {len(kpi_list)} KPIs valides après filtrage")
    return kpi_list


LINE_REF_MAP = {
    "STIF:Line::C01742:": "RER_A",
    "STIF:Line::C01743:": "RER_B",
    "STIF:Line::C01727:": "RER_C",
    "STIF:Line::C01728:": "RER_D",
    "STIF:Line::C01729:": "RER_E",
    "STIF:Line::C01737:": "LINE_H",
    "STIF:Line::C01739:": "LINE_J",
    "STIF:Line::C01738:": "LINE_K",
    "STIF:Line::C01740:": "LINE_L",
    "STIF:Line::C01736:": "LINE_N",
    "STIF:Line::C01730:": "LINE_P",
    "STIF:Line::C01731:": "LINE_R",
    "STIF:Line::C01741:": "LINE_U",
    "STIF:Line::C02711:": "LINE_V",
    "RER A": "RER_A", "RERA": "RER_A",
    "RER B": "RER_B", "RERB": "RER_B",
    "RER C": "RER_C", "RERC": "RER_C",
    "RER D": "RER_D", "RERD": "RER_D",
    "RER E": "RER_E", "RERE": "RER_E",
    "H": "LINE_H", "J": "LINE_J", "K": "LINE_K",
    "L": "LINE_L", "N": "LINE_N", "P": "LINE_P",
    "R": "LINE_R", "U": "LINE_U", "V": "LINE_V",
}

VALID_LINES = {
    "RER_A", "RER_B", "RER_C", "RER_D", "RER_E",
    "LINE_H", "LINE_J", "LINE_K", "LINE_L",
    "LINE_N", "LINE_P", "LINE_R", "LINE_U", "LINE_V",
}


def _normalize_line(line_ref: str) -> str:
    if not line_ref:
        return "UNKNOWN"
    if line_ref in VALID_LINES:
        return line_ref
    result = LINE_REF_MAP.get(line_ref.strip(), None)
    if result is None:
        print(f"[normalize_line] ⚠️ Pas de mapping pour : '{line_ref}'")
        return line_ref.strip().replace(" ", "_")
    return result


def close_old_incidents(timeout_minutes: int = 3):
    session = get_active_session()
    session.sql(f"""
        UPDATE M2_ISD_EQUIPE_4_DB.PUBLIC.ALERTES_REMONTEES
        SET
            is_active = FALSE,
            resolved_at = CURRENT_TIMESTAMP(),
            actual_recovery_min = DATEDIFF('minute', first_seen_at, CURRENT_TIMESTAMP())
        WHERE is_active = TRUE
          AND DATEDIFF('minute', last_seen_at, CURRENT_TIMESTAMP()) > {timeout_minutes}
    """).collect()
    print(f"[data_loader] Incidents fermés après {timeout_minutes} min d'inactivité")


def load_active_alerts() -> list:
    t0 = time.perf_counter()
    session = get_active_session()

    df = session.sql("""
        SELECT
            line_id, line_name, stop_name, destination_name,
            alert_level, priority_score, explanation,
            estimated_recovery_min, first_seen_at, last_seen_at,
            suppression_rate, delay_mean_min, wait_ratio, dwell_anomaly_min,
            s_operational, s_impact_usager, s_confidence
        FROM M2_ISD_EQUIPE_4_DB.PUBLIC.ALERTES_REMONTEES
        WHERE is_active = TRUE
        ORDER BY priority_score DESC
    """).to_pandas()
    t1 = time.perf_counter()

    print(f"[PERF] load_active_alerts SQL + to_pandas: {(t1 - t0) * 1000:.1f} ms")

    if df.empty:
        return []

    df.columns = df.columns.str.lower()

    alerts = []
    for _, row in df.iterrows():
        alerts.append({
            "line": _normalize_line(str(row.get("line_id") or "")),
            "line_name": str(row.get("line_name") or ""),
            "stop_name": str(row.get("stop_name") or "—"),
            "direction": str(row.get("destination_name") or ""),
            "alert_level": str(row.get("alert_level") or "JAUNE"),
            "priority_score": float(row.get("priority_score") or 0),
            "explanation": str(row.get("explanation") or ""),
            "estimated_recovery": f"{int(row.get('estimated_recovery_min') or 0)} min",
            "suppression_rate": float(row.get("suppression_rate") or 0),
            "delay_mean_min": float(row.get("delay_mean_min") or 0),
            "wait_ratio": float(row.get("wait_ratio") or 1),
            "dwell_anomaly_min": float(row.get("dwell_anomaly_min") or 0),
            "s_operational": float(row.get("s_operational") or 0),
            "s_impact_usager": float(row.get("s_impact_usager") or 0),
            "s_confidence": float(row.get("s_confidence") or 0),
            "first_seen_at": str(row.get("first_seen_at") or ""),
            "last_seen_at": str(row.get("last_seen_at") or ""),
        })

    t2 = time.perf_counter()
    print(f"[PERF] load_active_alerts mapping python: {(t2 - t1) * 1000:.1f} ms")
    print(f"[PERF] load_active_alerts TOTAL: {(t2 - t0) * 1000:.1f} ms")

    return alerts

def load_active_alerts_index() -> dict:
    session = get_active_session()

    rows = session.sql(f"""
        SELECT
            id,
            line_id,
            stop_id,
            destination_id,
            first_seen_at,
            last_seen_at,
            alert_level,
            priority_score
        FROM {TABLE_ALERTES}
        WHERE is_active = TRUE
    """).collect()

    index = {}
    for row in rows:
        key = (
            row["LINE_ID"] or "",
            row["STOP_ID"] or "",
            row["DESTINATION_ID"] or "",
        )
        index[key] = row

    return index

def find_active_alert_in_cache(kpi: dict, active_alerts_index: dict):
    return active_alerts_index.get(incident_key(kpi))