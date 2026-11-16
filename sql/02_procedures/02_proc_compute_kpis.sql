USE WAREHOUSE HACKATHON_WH;

CREATE OR REPLACE PROCEDURE M2_ISD_EQUIPE_4_DB.PUBLIC.COMPUTE_ALL_KPIS()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'main'
PACKAGES = ('snowflake-snowpark-python')
EXECUTE AS OWNER
AS
$$
from snowflake.snowpark import functions as F

def main(session):
    TARGET = "KPI_UNIFIED"
    JOIN_KEYS = ["LINE_REF", "LINE_NAME", "MONITORING_REF", "MONITORED_STOP", "DESTINATION"]

    try:
        last = session.sql(f"SELECT COALESCE(MAX(BATCH_ID),0) FROM {TARGET}").collect()[0][0]
    except:
        last = 0
    batch_id = last + 1

    df = session.table("IDFM_STOP_MONITORING_CURRENT")

    total = df.group_by(*JOIN_KEYS).agg(
        F.count_distinct("VEHICLE_JOURNEY_REF").alias("KPI1_TOTAL_TRAINS")
    )
    cancelled = df.filter(
        (F.col("ARRIVAL_STATUS") == "CANCELLED") | (F.col("DEPARTURE_STATUS") == "CANCELLED")
    ).group_by(*JOIN_KEYS).agg(
        F.count_distinct("VEHICLE_JOURNEY_REF").alias("KPI1_TRAINS_ANNULES")
    )
    kpi1 = total.join(cancelled, on=JOIN_KEYS, join_type="left").with_column(
        "KPI1_TRAINS_ANNULES", F.coalesce(F.col("KPI1_TRAINS_ANNULES"), F.lit(0))
    ).with_column(
        "KPI1_TAUX_SUPPRESSION", F.round(F.col("KPI1_TRAINS_ANNULES") / F.col("KPI1_TOTAL_TRAINS") * 100, 2)
    ).select(*JOIN_KEYS, "KPI1_TOTAL_TRAINS", "KPI1_TRAINS_ANNULES", "KPI1_TAUX_SUPPRESSION")

    passed = df.filter(
        (F.col("EXPECTED_DEPARTURE_TIME").is_not_null()) &
        (F.col("AIMED_DEPARTURE_TIME").is_not_null()) &
        (F.col("EXPECTED_DEPARTURE_TIME") >= F.dateadd("minute", F.lit(-15), F.col("INGESTED_AT"))) &
        (F.col("EXPECTED_DEPARTURE_TIME") <= F.dateadd("minute", F.lit(30), F.col("INGESTED_AT")))
    ).with_column(
        "RETARD_MIN", F.round(
            F.datediff("second", F.col("AIMED_DEPARTURE_TIME"), F.col("EXPECTED_DEPARTURE_TIME")) / 60, 2
        )
    )
    kpi2 = passed.group_by(*JOIN_KEYS).agg(
        F.round(F.avg("RETARD_MIN"), 2).alias("KPI2_RETARD_MOYEN_MIN"),
        F.count_distinct("VEHICLE_JOURNEY_REF").alias("KPI2_NB_TRAINS_RETARD")
    ).select(*JOIN_KEYS, "KPI2_RETARD_MOYEN_MIN", "KPI2_NB_TRAINS_RETARD")

    kpi3 = session.sql("""
        WITH base AS (
            SELECT
                LINE_REF, LINE_NAME, MONITORING_REF, MONITORED_STOP, DESTINATION,
                VEHICLE_JOURNEY_REF,
                INGESTED_AT,
                AIMED_DEPARTURE_TIME,
                EXPECTED_DEPARTURE_TIME,
                CASE WHEN ARRIVAL_STATUS = 'CANCELLED' OR DEPARTURE_STATUS = 'CANCELLED'
                     THEN TRUE ELSE FALSE END AS IS_CANCELLED
            FROM IDFM_STOP_MONITORING_CURRENT
            WHERE AIMED_DEPARTURE_TIME IS NOT NULL
              AND AIMED_DEPARTURE_TIME > INGESTED_AT
              AND AIMED_DEPARTURE_TIME <= DATEADD('minute', 30, INGESTED_AT)
        ),
        with_next AS (
            SELECT *,
                CASE
                    WHEN IS_CANCELLED THEN
                        LEAD(EXPECTED_DEPARTURE_TIME_NC) IGNORE NULLS OVER (
                            PARTITION BY LINE_REF, MONITORING_REF, DESTINATION
                            ORDER BY AIMED_DEPARTURE_TIME
                        )
                    ELSE EXPECTED_DEPARTURE_TIME
                END AS REAL_DEPARTURE
            FROM (
                SELECT *,
                    CASE WHEN IS_CANCELLED THEN NULL ELSE EXPECTED_DEPARTURE_TIME END AS EXPECTED_DEPARTURE_TIME_NC
                FROM base
            )
        ),
        computed AS (
            SELECT
                LINE_REF, LINE_NAME, MONITORING_REF, MONITORED_STOP, DESTINATION,
                VEHICLE_JOURNEY_REF,
                DATEDIFF('second', INGESTED_AT, AIMED_DEPARTURE_TIME) AS ATTENTE_THEORIQUE_SEC,
                DATEDIFF('second', INGESTED_AT, REAL_DEPARTURE) AS ATTENTE_REELLE_SEC
            FROM with_next
            WHERE REAL_DEPARTURE IS NOT NULL
        )
        SELECT
            LINE_REF, LINE_NAME, MONITORING_REF, MONITORED_STOP, DESTINATION,
            ROUND(SUM(ATTENTE_REELLE_SEC) / NULLIF(SUM(ATTENTE_THEORIQUE_SEC), 0), 2) AS KPI3_RATIO_ATTENTE,
            ROUND(AVG(ATTENTE_REELLE_SEC) / 60, 2) AS KPI3_ATTENTE_REELLE_MOY_MIN,
            ROUND(AVG(ATTENTE_THEORIQUE_SEC) / 60, 2) AS KPI3_ATTENTE_THEORIQUE_MOY_MIN,
            COUNT(DISTINCT VEHICLE_JOURNEY_REF) AS KPI3_NB_TRAINS_FENETRE
        FROM computed
        GROUP BY LINE_REF, LINE_NAME, MONITORING_REF, MONITORED_STOP, DESTINATION
    """)

    stop_type = df.group_by(*JOIN_KEYS).agg(
        F.count(F.col("EXPECTED_DEPARTURE_TIME")).alias("_DEP_CNT"),
        F.count(F.col("AIMED_DEPARTURE_TIME")).alias("_AIMED_DEP_CNT"),
        F.count(F.col("EXPECTED_ARRIVAL_TIME")).alias("_ARR_CNT"),
        F.count(F.col("AIMED_ARRIVAL_TIME")).alias("_AIMED_ARR_CNT")
    ).with_column(
        "STOP_TYPE",
        F.when(
            (F.col("_DEP_CNT") == F.lit(0)) & (F.col("_AIMED_DEP_CNT") == F.lit(0)),
            F.lit("TERMINUS")
        ).when(
            (F.col("_ARR_CNT") == F.lit(0)) & (F.col("_AIMED_ARR_CNT") == F.lit(0)),
            F.lit("DEPART")
        ).otherwise(F.lit("AUTRE"))
    ).select(*JOIN_KEYS, "STOP_TYPE")

    a_quai = df.filter(
        (F.col("ARRIVAL_STATUS") == "ARRIVED") &
        (F.col("EXPECTED_DEPARTURE_TIME").is_not_null()) &
        (F.col("EXPECTED_ARRIVAL_TIME").is_not_null()) &
        (F.col("AIMED_DEPARTURE_TIME").is_not_null()) &
        (F.col("AIMED_ARRIVAL_TIME").is_not_null()) &
        (F.datediff("second", F.col("AIMED_ARRIVAL_TIME"), F.col("AIMED_DEPARTURE_TIME")) > F.lit(0))
    ).with_column(
        "DUREE_REELLE_SEC",
        F.datediff("second", F.col("EXPECTED_ARRIVAL_TIME"), F.col("EXPECTED_DEPARTURE_TIME"))
    ).with_column(
        "DUREE_PREVUE_SEC",
        F.datediff("second", F.col("AIMED_ARRIVAL_TIME"), F.col("AIMED_DEPARTURE_TIME"))
    )
    kpi4 = a_quai.group_by(*JOIN_KEYS).agg(
        F.round(
            F.avg(F.col("DUREE_REELLE_SEC")) / F.nullif(F.avg(F.col("DUREE_PREVUE_SEC")), F.lit(0)), 2
        ).alias("KPI4_RATIO_STATIONNEMENT")
    ).select(*JOIN_KEYS, "KPI4_RATIO_STATIONNEMENT")

    unified = (
        kpi1
        .join(kpi2, on=JOIN_KEYS, join_type="left")
        .join(kpi3, on=JOIN_KEYS, join_type="left")
        .join(kpi4, on=JOIN_KEYS, join_type="left")
        .join(stop_type, on=JOIN_KEYS, join_type="left")
        .with_column("KPI4_RATIO_STATIONNEMENT", F.coalesce(F.col("KPI4_RATIO_STATIONNEMENT"), F.lit(1.0)))
        .with_column("BATCH_ID", F.lit(batch_id))
        .with_column("CALCULATED_AT", F.current_timestamp())
    )

    unified.write.mode("append").save_as_table(TARGET, column_order="name")
    count = unified.count()
    return f"BATCH_ID={batch_id}, {count} lignes inserees dans {TARGET}"
$$;
