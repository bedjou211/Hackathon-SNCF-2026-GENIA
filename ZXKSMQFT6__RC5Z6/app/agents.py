"""
Pipeline principale — Architecture multi-agents SNCF Vigi Numérique.
Pipeline : AgentTS → Agent3D → AgentDecideur → AgentDeduplication
           → AgentEstimateur → AgentHistorisation
AgentExplicatif : appelé à la demande uniquement (dashboard).
"""

import json
import math
import os
import time
import re

from datetime import datetime

import pandas as pd
from snowflake.snowpark.context import get_active_session

from app.scoring import (
    compute_s_operational,
    compute_s_impact_usager,
    compute_s_confidence,
    compute_priority_score_3d,
    decide_alert_level,
    generate_summary,
    compute_s_impact_contexte,
)
from app.System_TS import TSDDetectorAgent
from app.agent_explicateur import AgentExplicateurLLM


TABLE_IDFM_DISRUPTIONS = "M2_ISD_EQUIPE_4_DB.PUBLIC.IDFM_DISRUPTIONS"


def _extract_line_code(raw: str) -> str | None:
    if not raw:
        return None
    m = re.search(r'(C\d+)', str(raw))
    return m.group(1) if m else None


def _extract_stop_code(raw: str) -> str | None:
    if not raw:
        return None
    m = re.search(r'(\d+)', str(raw))
    return m.group(1) if m else None


def normalize_line_id_for_disruptions(kpi: dict) -> str | None:
    for raw in (kpi.get("line_id"), kpi.get("line_ref"), kpi.get("line")):
        code = _extract_line_code(raw)
        if code:
            return f"line:IDFM:{code}"
    return None


def normalize_stop_id_for_disruptions(kpi: dict) -> str | None:
    for raw in (kpi.get("stop_id"), kpi.get("stop_ref"), kpi.get("monitoring_ref")):
        code = _extract_stop_code(raw)
        if code:
            return f"IDFM:{code}"
    return None


def exists_in_idfm_disruptions(kpi: dict) -> bool:
    session = get_active_session()

    normalized_line = normalize_line_id_for_disruptions(kpi)
    normalized_stop = normalize_stop_id_for_disruptions(kpi)

    if not normalized_line or not normalized_stop:
        return False

    rows = session.sql(f"""
        SELECT 1
        FROM {TABLE_IDFM_DISRUPTIONS} d,
             LATERAL FLATTEN(input => d.LINE_IDS) l,
             LATERAL FLATTEN(input => d.IMPACTED_STOPS) s
        WHERE d.IS_ACTIVE = TRUE
          AND l.value::string = ?
          AND s.value:parent_station::string = ?
        LIMIT 1
    """, params=[normalized_line, normalized_stop]).collect()

    return len(rows) > 0

# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────
TABLE_ALERTES = "M2_ISD_EQUIPE_4_DB.PUBLIC.ALERTES_REMONTEES"


# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────
def _safe_num(val, default=None) -> float:
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────
# CHARGEMENT DES INCIDENTS INFO TRAFIC
# ─────────────────────────────────────────────
_INCIDENTS_PATH = os.path.join(os.path.dirname(__file__), "existing_incidents.json")
try:
    with open(_INCIDENTS_PATH, "r", encoding="utf-8") as f:
        EXISTING_INCIDENTS = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    EXISTING_INCIDENTS = []


# ─────────────────────────────────────────────
# FONCTIONS UTILITAIRES SNOWFLAKE
# ─────────────────────────────────────────────
def incident_key(kpi: dict) -> tuple:
    return (
        kpi.get("line_id", ""),
        kpi.get("stop_id", ""),
        kpi.get("destination_id", ""),
    )


def close_old_incidents(timeout_minutes: int = 3):
    session = get_active_session()
    session.sql(f"""
        UPDATE {TABLE_ALERTES}
        SET
            is_active           = FALSE,
            resolved_at         = CURRENT_TIMESTAMP(),
            actual_recovery_min = DATEDIFF('minute', first_seen_at, CURRENT_TIMESTAMP())
        WHERE is_active = TRUE
          AND DATEDIFF('minute', last_seen_at, CURRENT_TIMESTAMP()) > {timeout_minutes}
    """).collect()
    print(f"[agents] Incidents fermés après {timeout_minutes} min d'inactivité")


def load_active_alerts_index() -> dict:
    session = get_active_session()
    rows = session.sql(f"""
        SELECT
            id, line_id, stop_id, destination_id,
            line_name, stop_name, destination_name, batch_id,
            first_seen_at, last_seen_at, raised_at, is_active,
            alert_level, priority_score, explanation, estimated_recovery_min,
            s_operational, s_impact_usager, s_confidence, ts_score_final,
            angle_z_score_short, angle_z_score_daily, angle_z_score_weekly, angle_z_score_dow,
            angle_slope_score, angle_correlation_score, angle_persistence_score, angle_propagation_score,
            dominant_angle,
            suppression_rate, delay_mean_min, wait_ratio, dwell_anomaly_min,
            breaker_details, full_context_json
        FROM {TABLE_ALERTES}
        WHERE is_active = TRUE
    """).collect()

    return {
        (row["LINE_ID"] or "", row["STOP_ID"] or "", row["DESTINATION_ID"] or ""): dict(row.as_dict())
        for row in rows
    }


def exists_in_info_trafic(kpi: dict) -> bool:
    line_id, stop_id, destination_id = incident_key(kpi)
    for incident in EXISTING_INCIDENTS:
        if not incident.get("is_active_now", False):
            continue
        t = incident.get("transport", {})
        if (t.get("line_id") == line_id and
                t.get("stop_id") == stop_id and
                t.get("destination_id") == destination_id):
            return True
    return False


# ─────────────────────────────────────────────
# FONCTION PUBLIQUE : explication LLM à la demande
# ─────────────────────────────────────────────
def generate_llm_explanation(kpi: dict, context: dict) -> str:
    try:
        llm = AgentExplicateurLLM()
        result = llm.explain(kpi, context)
        return result.get("llm_explanation", "Aucune explication générée.")
    except Exception as e:
        return f"Erreur lors de la génération de l'explication : {e}"


# ══════════════════════════════════════════════
# CLASSE DE BASE
# ══════════════════════════════════════════════
class BaseAgent:
    def __init__(self):
        self.name = self.__class__.__name__

    def process(self, kpi: dict, context: dict) -> dict:
        return self._execute(kpi, context)

    def _execute(self, kpi: dict, context: dict) -> dict:
        raise NotImplementedError


# ══════════════════════════════════════════════
# AGENT 1 — TS DETECTOR
# Calcule le score temporel (anomalie multivariée)
# ══════════════════════════════════════════════
class AgentTS(BaseAgent):
    """
    Détecte les anomalies temporelles et spatiales sur les KPIs.
    Produit : ts_score, ts_level, ts_angle, ts_details, ts_tokens.
    """

    def __init__(self, detector: TSDDetectorAgent):
        super().__init__()
        self.detector = detector

    def _execute(self, kpi: dict, context: dict) -> dict:
        t0 = time.perf_counter()

        kpi_ts = {
            "timestamp":       kpi["timestamp"],
            "line":            kpi["line"],
            "stop_id":         kpi.get("stop_id", ""),
            "direction":       kpi.get("direction", ""),
            "suppression_rate": kpi.get("suppression_rate", 0),
            "delay_mean":      kpi.get("delay_mean_min", 0),
            "wait_ratio":      kpi.get("wait_ratio", 1.0),
            "headway_ratio":   kpi.get("headway_ratio", 1.0),
        }

        vec = self.detector.process(kpi_ts)

        context["ts_score"] = vec.final_score
        context["ts_level"] = vec.level
        context["ts_angle"] = vec.dominant_angle
        context["ts_details"] = {
            "z_short":       vec.z_score_short,
            "z_daily":       vec.z_score_daily,
            "z_weekly":      vec.z_score_weekly,
            "z_dow":         vec.z_score_dow,
            "slope":         vec.slope_score,
            "correlation":   vec.correlation_score,
            "persistence":   vec.persistence_score,
            "propagation":   vec.propagation_score,
        }
        context["ts_tokens"] = vec.explanation_tokens

        print(f"[PERF] AgentTS {(time.perf_counter() - t0)*1000:.1f} ms")
        return context


# ══════════════════════════════════════════════
# AGENT 2 — 3D SCORER
# Calcule les 3 scores métier et le score 3D agrégé
# ══════════════════════════════════════════════
class Agent3D(BaseAgent):
    """
    Calcule les 3 dimensions du score métier :
      - s_operational  : sévérité infrastructure
      - s_impact_usager: ressenti voyageur (heure de pointe incluse)
      - s_confidence   : fiabilité du signal
    Produit : s_operational, s_impact_usager, s_confidence, score_3d.
    """

    def _execute(self, kpi: dict, context: dict) -> dict:
        context["s_operational"] = compute_s_operational(kpi)

        context["s_impact_usager"] = (
            0.7 * compute_s_impact_usager(kpi, kpi.get("timestamp", ""))
            + 0.3 * compute_s_impact_contexte(kpi)
        )
        # context["s_impact_usager"] = compute_s_impact_usager(kpi, kpi.get("timestamp", ""))
           

        context["s_confidence"] = compute_s_confidence(kpi)

        context["score_3d"] = compute_priority_score_3d(
            context["s_operational"],
            context["s_impact_usager"],
            context["s_confidence"],
        )

        return context


# ══════════════════════════════════════════════
# AGENT 3 — DECIDEUR
# Fusionne score TS et score 3D → niveau d'alerte
# ══════════════════════════════════════════════
class AgentDecideur(BaseAgent):
    """
    Fusionne les scores TS et 3D pour décider du niveau d'alerte.
    priority_score = 0.60 × ts_score + 0.40 × score_3d
    alert_level ∈ {VERT, JAUNE, ORANGE, ROUGE}
    """

    def _execute(self, kpi: dict, context: dict) -> dict:
        context["priority_score"] = (
            0.60 * context.get("ts_score", 0)
            + 0.40 * context.get("score_3d", 0)
        )
        context["alert_level"] = decide_alert_level(context["priority_score"])
        return context


# ══════════════════════════════════════════════
# AGENT 4 — DEDUPLICATION
# Vérifie si l'alerte existe déjà (info trafic ou BDD interne)
# ══════════════════════════════════════════════
class AgentDeduplication(BaseAgent):
    """
    Évite les doublons en croisant :
      1. La table IDFM_DISRUPTIONS
      2. L'index mémoire des alertes actives (ALERTES_REMONTEES)
    Traitement uniquement pour les alertes ORANGE et ROUGE.
    """

    def _execute(self, kpi: dict, context: dict) -> dict:
        incident_existant = False
        incident_deja_remonte = False
        incident_a_remonter = False
        existing = None

        if context.get("alert_level") in ("ORANGE", "ROUGE"):
            active_alerts_index = context.get("active_alerts_index", {})
            existing = active_alerts_index.get(incident_key(kpi))

            incident_existant = exists_in_idfm_disruptions(kpi)
            incident_deja_remonte = existing is not None

            if not incident_existant and not incident_deja_remonte:
                incident_a_remonter = True

        context["existing_active_alert"] = existing
        context["incident_existant"] = incident_existant
        context["incident_deja_remonte"] = incident_deja_remonte
        context["incident_a_remonter"] = incident_a_remonter
        return context

# ══════════════════════════════════════════════
# AGENT 5 — ESTIMATEUR
# Calcule le temps estimé de retour à la normale (ETR)
# ══════════════════════════════════════════════
class AgentEstimateur(BaseAgent):
    """
    Estime le temps de reprise (ETR) à partir des KPIs et du score TS.
    Tient compte des heures de pointe (+40%) et du niveau d'alerte (plancher).
    Plafond à 120 min pour rester crédible.
    """

    def _execute(self, kpi: dict, context: dict) -> dict:
        est_min  = 5.0
        est_min += 30.0 * kpi.get("suppression_rate", 0)
        est_min += 2.0  * kpi.get("delay_mean_min", 0)
        est_min += 15.0 * max(kpi.get("wait_ratio", 1.0) - 1.0, 0)
        est_min += 1.5  * kpi.get("dwell_anomaly_min", 0)

        ts_score = context.get("ts_score", 0)
        if ts_score > 0.8:
            est_min += 15
        elif ts_score > 0.5:
            est_min += 7

        try:
            dt   = datetime.strptime(kpi["timestamp"], "%Y-%m-%d %H:%M:%S")
            hour = dt.hour
            if (7 <= hour <= 9) or (17 <= hour <= 19):
                est_min *= 1.4
        except Exception:
            pass

        level = context.get("alert_level", "VERT")
        if level == "ROUGE":
            est_min = max(est_min, 35)
        elif level == "ORANGE":
            est_min = max(est_min, 15)

        est_min = min(round(est_min, 1), 120)

        context["estimated_recovery_min"] = est_min
        context["estimated_recovery"]     = "> 120 min" if est_min >= 120 else f"{est_min:.0f} min"
        return context


# ══════════════════════════════════════════════
# AGENT 6 — HISTORISATION
# INSERT ou UPDATE dans ALERTES_REMONTEES
# ══════════════════════════════════════════════
class AgentHistorisation(BaseAgent):
    """
    Persiste l'alerte dans Snowflake (ALERTES_REMONTEES) :
      - INSERT si nouvelle alerte à remonter
      - UPDATE si alerte existante déjà connue
      - NOOP sinon (VERT/JAUNE ou doublon)
    Met également à jour le cache mémoire (active_alerts_index).
    """

    def _execute(self, kpi: dict, context: dict) -> dict:
        t0 = time.perf_counter()

        if context.get("alert_level") not in ("ORANGE", "ROUGE"):
            print(f"[PERF] AgentHistorisation {(time.perf_counter()-t0)*1000:.1f} ms | skipped")
            return context

        t_session_0 = time.perf_counter()
        session     = get_active_session()
        t_session_1 = time.perf_counter()

        existing             = context.get("existing_active_alert")
        active_alerts_index  = context.get("active_alerts_index", {})
        key                  = incident_key(kpi)
        ts_d                 = context.get("ts_details", {})

        # ── INSERT ──
        if context.get("incident_a_remonter") and existing is None:
            params = [
                kpi.get("line_id"),
                kpi.get("stop_id"),
                kpi.get("destination_id"),
                kpi.get("line_name", kpi.get("line")),
                kpi.get("stop_name"),
                kpi.get("destination"),
                int(kpi.get("batch_id", 0)),
                context.get("alert_level"),
                _safe_num(context.get("priority_score")),
                context.get("explanation"),
                _safe_num(context.get("estimated_recovery_min")),
                _safe_num(context.get("s_operational")),
                _safe_num(context.get("s_impact_usager")),
                _safe_num(context.get("s_confidence")),
                _safe_num(context.get("ts_score")),
                _safe_num(ts_d.get("z_short")),
                _safe_num(ts_d.get("z_daily")),
                _safe_num(ts_d.get("z_weekly")),
                _safe_num(ts_d.get("z_dow")),
                _safe_num(ts_d.get("slope")),
                _safe_num(ts_d.get("correlation")),
                _safe_num(ts_d.get("persistence")),
                _safe_num(ts_d.get("propagation")),
                context.get("ts_angle", ""),
                _safe_num(kpi.get("suppression_rate")),
                _safe_num(kpi.get("delay_mean_min")),
                _safe_num(kpi.get("wait_ratio")),
                _safe_num(kpi.get("dwell_anomaly_min")),
            ]

            t_insert_0 = time.perf_counter()
            session.sql(f"""
                INSERT INTO {TABLE_ALERTES} (
                    line_id, stop_id, destination_id,
                    line_name, stop_name, destination_name,
                    batch_id, first_seen_at, last_seen_at, raised_at, is_active,
                    alert_level, priority_score, explanation, estimated_recovery_min,
                    s_operational, s_impact_usager, s_confidence, ts_score_final,
                    angle_z_score_short, angle_z_score_daily, angle_z_score_weekly, angle_z_score_dow,
                    angle_slope_score, angle_correlation_score, angle_persistence_score, angle_propagation_score,
                    dominant_angle,
                    suppression_rate, delay_mean_min, wait_ratio, dwell_anomaly_min,
                    breaker_details, full_context_json
                )
                SELECT
                    ?,?,?, ?,?,?, ?,
                    CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), TRUE,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,
                    ?,?,?,?,
                    NULL, NULL
            """, params=params).collect()
            t_insert_1 = time.perf_counter()

            active_alerts_index[key] = {
                "ID": None,
                "LINE_ID": kpi.get("line_id"),
                "STOP_ID": kpi.get("stop_id"),
                "DESTINATION_ID": kpi.get("destination_id"),
                "LINE_NAME": kpi.get("line_name", kpi.get("line")),
                "STOP_NAME": kpi.get("stop_name"),
                "DESTINATION_NAME": kpi.get("destination"),
                "BATCH_ID": int(kpi.get("batch_id", 0)),
                "ALERT_LEVEL": context.get("alert_level"),
                "PRIORITY_SCORE": _safe_num(context.get("priority_score")),
                "EXPLANATION": context.get("explanation"),
                "ESTIMATED_RECOVERY_MIN": _safe_num(context.get("estimated_recovery_min")),
                "S_OPERATIONAL": _safe_num(context.get("s_operational")),
                "S_IMPACT_USAGER": _safe_num(context.get("s_impact_usager")),
                "S_CONFIDENCE": _safe_num(context.get("s_confidence")),
                "TS_SCORE_FINAL": _safe_num(context.get("ts_score")),
                "ANGLE_Z_SCORE_SHORT": _safe_num(ts_d.get("z_short")),
                "ANGLE_Z_SCORE_DAILY": _safe_num(ts_d.get("z_daily")),
                "ANGLE_Z_SCORE_WEEKLY": _safe_num(ts_d.get("z_weekly")),
                "ANGLE_Z_SCORE_DOW": _safe_num(ts_d.get("z_dow")),
                "ANGLE_SLOPE_SCORE": _safe_num(ts_d.get("slope")),
                "ANGLE_CORRELATION_SCORE": _safe_num(ts_d.get("correlation")),
                "ANGLE_PERSISTENCE_SCORE": _safe_num(ts_d.get("persistence")),
                "ANGLE_PROPAGATION_SCORE": _safe_num(ts_d.get("propagation")),
                "DOMINANT_ANGLE": context.get("ts_angle", ""),
                "SUPPRESSION_RATE": _safe_num(kpi.get("suppression_rate")),
                "DELAY_MEAN_MIN": _safe_num(kpi.get("delay_mean_min")),
                "WAIT_RATIO": _safe_num(kpi.get("wait_ratio")),
                "DWELL_ANOMALY_MIN": _safe_num(kpi.get("dwell_anomaly_min")),
                "BREAKER_DETAILS": None,
                "FULL_CONTEXT_JSON": None,
            }
            context["existing_active_alert"] = active_alerts_index[key]

            print(
                f"[PERF] AgentHistorisation INSERT {(time.perf_counter()-t0)*1000:.1f} ms | "
                f"session={(t_session_1-t_session_0)*1000:.1f} ms | "
                f"insert={(t_insert_1-t_insert_0)*1000:.1f} ms | "
                f"line={kpi.get('line_id')} stop={kpi.get('stop_id')}"
            )

        # ── UPDATE ──
        elif existing is not None:
            existing_id = existing.get("ID") or existing.get("id")

            if existing_id is not None:
                t_update_0 = time.perf_counter()
                session.sql(f"""
                    UPDATE {TABLE_ALERTES}
                    SET
                        last_seen_at      = CURRENT_TIMESTAMP(),
                        alert_level       = ?,
                        priority_score    = ?,
                        explanation       = ?,
                        s_operational     = ?,
                        s_impact_usager   = ?,
                        s_confidence      = ?,
                        ts_score_final    = ?,
                        suppression_rate  = ?,
                        delay_mean_min    = ?,
                        wait_ratio        = ?,
                        dwell_anomaly_min = ?,
                        breaker_details   = NULL,
                        full_context_json = NULL
                    WHERE id = ?
                """, params=[
                    context.get("alert_level"),
                    _safe_num(context.get("priority_score")),
                    context.get("explanation"),
                    _safe_num(context.get("s_operational")),
                    _safe_num(context.get("s_impact_usager")),
                    _safe_num(context.get("s_confidence")),
                    _safe_num(context.get("ts_score")),
                    _safe_num(kpi.get("suppression_rate")),
                    _safe_num(kpi.get("delay_mean_min")),
                    _safe_num(kpi.get("wait_ratio")),
                    _safe_num(kpi.get("dwell_anomaly_min")),
                    existing_id,
                ]).collect()
                t_update_1 = time.perf_counter()

                print(
                    f"[PERF] AgentHistorisation UPDATE {(time.perf_counter()-t0)*1000:.1f} ms | "
                    f"session={(t_session_1-t_session_0)*1000:.1f} ms | "
                    f"update={(t_update_1-t_update_0)*1000:.1f} ms | "
                    f"id={existing_id}"
                )

            active_alerts_index[key].update({
                "ALERT_LEVEL":           context.get("alert_level"),
                "PRIORITY_SCORE":        _safe_num(context.get("priority_score")),
                "EXPLANATION":           context.get("explanation"),
                "ESTIMATED_RECOVERY_MIN":_safe_num(context.get("estimated_recovery_min")),
                "S_OPERATIONAL":         _safe_num(context.get("s_operational")),
                "S_IMPACT_USAGER":       _safe_num(context.get("s_impact_usager")),
                "S_CONFIDENCE":          _safe_num(context.get("s_confidence")),
                "TS_SCORE_FINAL":        _safe_num(context.get("ts_score")),
                "ANGLE_Z_SCORE_SHORT":   _safe_num(ts_d.get("z_short")),
                "ANGLE_Z_SCORE_DAILY":   _safe_num(ts_d.get("z_daily")),
                "ANGLE_Z_SCORE_WEEKLY":  _safe_num(ts_d.get("z_weekly")),
                "ANGLE_Z_SCORE_DOW":     _safe_num(ts_d.get("z_dow")),
                "ANGLE_SLOPE_SCORE":     _safe_num(ts_d.get("slope")),
                "ANGLE_CORRELATION_SCORE":_safe_num(ts_d.get("correlation")),
                "ANGLE_PERSISTENCE_SCORE":_safe_num(ts_d.get("persistence")),
                "ANGLE_PROPAGATION_SCORE":_safe_num(ts_d.get("propagation")),
                "DOMINANT_ANGLE":        context.get("ts_angle", ""),
                "SUPPRESSION_RATE":      _safe_num(kpi.get("suppression_rate")),
                "DELAY_MEAN_MIN":        _safe_num(kpi.get("delay_mean_min")),
                "WAIT_RATIO":            _safe_num(kpi.get("wait_ratio")),
                "DWELL_ANOMALY_MIN":     _safe_num(kpi.get("dwell_anomaly_min")),
                "BREAKER_DETAILS":       None,
                "FULL_CONTEXT_JSON":     None,
            })

        else:
            print(f"[PERF] AgentHistorisation NOOP {(time.perf_counter()-t0)*1000:.1f} ms")

        return context


# ══════════════════════════════════════════════
# AGENT EXPLICATIF — à la demande uniquement
# N'est PAS dans le pipeline automatique
# ══════════════════════════════════════════════
class AgentExplicatif:
    """
    Génère une explication LLM pour une alerte donnée.
    Appelé uniquement via le bouton « Expliquer » du dashboard,
    jamais automatiquement dans le pipeline.
    """

    def __init__(self):
        self.llm = AgentExplicateurLLM()

    def explain(self, kpi: dict, context: dict) -> str:
        t0 = time.perf_counter()
        result = self.llm.explain(kpi, context)
        print(f"[PERF] AgentExplicatif {(time.perf_counter()-t0)*1000:.1f} ms")
        return result.get("llm_explanation", "Aucune explication générée.")


# ══════════════════════════════════════════════
# ORCHESTRATEUR
# ══════════════════════════════════════════════
class AgentOrchestrateur:
    """
    Coordonne l'exécution séquentielle des agents.
    Pipeline : AgentTS → Agent3D → AgentDecideur
               → AgentDeduplication → AgentEstimateur → AgentHistorisation
    """

    def __init__(self, ts_detector: TSDDetectorAgent):
        self.active_alerts_index = {}
        self.agents = [
            AgentTS(ts_detector),
            Agent3D(),
            AgentDecideur(),
            AgentDeduplication(),
            AgentEstimateur(),
            AgentHistorisation(),
        ]

    def set_active_alerts_index(self, active_alerts_index: dict):
        self.active_alerts_index = active_alerts_index

    def process_kpi(self, kpi: dict) -> dict:
        context = {"active_alerts_index": self.active_alerts_index}

        kpi_id = (
            f"{kpi.get('line', '?')} | "
            f"{kpi.get('stop_name', kpi.get('stop_id', '?'))} | "
            f"{kpi.get('direction', '?')}"
        )

        timings   = []
        total_t0  = time.perf_counter()

        for agent in self.agents:
            t0      = time.perf_counter()
            context = agent.process(kpi, context)
            dt      = (time.perf_counter() - t0) * 1000
            timings.append((agent.name, dt))

        format_t0 = time.perf_counter()
        result    = self._format_output(kpi, context)
        timings.append(("_format_output", (time.perf_counter() - format_t0) * 1000))

        total_dt   = (time.perf_counter() - total_t0) * 1000
        slow_steps = [f"{n}={d:.1f}ms" for n, d in timings if d > 20]

        print(
            f"[PERF process_kpi] TOTAL={total_dt:.1f}ms | {kpi_id} | "
            f"{' | '.join(slow_steps) if slow_steps else 'aucune étape lente'}"
        )

        return result

    def _format_output(self, kpi: dict, context: dict) -> dict:
        return {
            # ── Identifiants ──
            "timestamp":   kpi.get("timestamp"),
            "line":        kpi.get("line"),
            "line_id":     kpi.get("line_id", ""),
            "line_name":   kpi.get("line_name", ""),
            "stop_name":   kpi.get("stop_name", ""),
            "stop_id":     kpi.get("stop_id", ""),
            "direction":   kpi.get("direction", ""),

            # ── KPIs bruts ──
            "suppression_rate":  kpi.get("suppression_rate", 0),
            "delay_mean_min":    kpi.get("delay_mean_min", 0),
            "wait_ratio":        kpi.get("wait_ratio", 1.0),
            "headway_ratio":     kpi.get("headway_ratio", 1.0),
            "dwell_anomaly_min": kpi.get("dwell_anomaly_min", 0),

            # ── Scores 3D ──
            "s_operational":   context["s_operational"],
            "s_impact_usager": context["s_impact_usager"],
            "s_confidence":    context["s_confidence"],
            "score_3d":        context["score_3d"],

            # ── Score TS ──
            "ts": {
                "score":   context.get("ts_score"),
                "level":   context.get("ts_level"),
                "angle":   context.get("ts_angle"),
                "details": context.get("ts_details", {}),
                "tokens":  context.get("ts_tokens", {}),
            },

            # ── Décision ──
            "priority_score":         context["priority_score"],
            "alert_level":            context["alert_level"],
            "incident_existant":      context["incident_existant"],
            "incident_a_remonter":    context["incident_a_remonter"],
            "estimated_recovery":     context["estimated_recovery"],
            "estimated_recovery_min": context["estimated_recovery_min"],
            "explanation":            context.get("explanation", ""),
        }