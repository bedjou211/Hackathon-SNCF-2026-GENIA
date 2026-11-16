"""
Module de scoring 3D - Calcule les scores opérationnel, impact usager, confiance et priorité finale.
Architecture : S_operational (45%) + S_impact_usager (40%) + S_confidence (15%)
Agent Analyseur + Agent Décideur + Agent Explicatif.
"""
from snowflake.snowpark.context import get_active_session
from datetime import datetime, timezone
#import requests

# ============================================================================
# CONSTANTES SNCF
# ============================================================================

# Poids de demande basés sur fréquentation SNCF (normalisée)
# Rang 1 (RER A ~ 1.4M) = 1.0, rang 13 (Ligne K ~ 10.3K) = 0.35
DEMAND_WEIGHT = {
    "RER_A": 1.00,   # Rang 1: 1 400 000 - Hyper-structurante
    "RER_B": 0.95,   # Rang 2: 983 000 - Hyper-structurante
    "RER_D": 0.90,   # Rang 3: 660 000 - Hyper-structurante
    "RER_C": 0.85,   # Rang 4: 540 000 - Hyper-structurante
    "RER_E": 0.80,   # Rang 5: 372 000 - Très structurante
    "LINE_L": 0.75,  # Rang 6: 311 000 - Très structurante
    "LINE_J": 0.70,  # Rang 7: 260 000 - Très structurante
    "LINE_H": 0.68,  # Rang 8: 250 000 - Très structurante
    "LINE_P": 0.60,  # Rang 9: 120 000 - Structurante
    "LINE_N": 0.58,  # Rang 10: 117 000 - Structurante
    "LINE_U": 0.50,  # Rang 11: 52 000 - Modérément structurante
    "LINE_R": 0.48,  # Rang 12: 45 000 - Modérément structurante
    "LINE_K": 0.35,  # Rang 13: 10 300 - Faiblement structurante
}

# Heures de pointe (jours de semaine uniquement)
PEAK_HOURS = [
    (7.5, 9.5),    # 7h30 - 9h30
    (16.5, 19.5),  # 16h30 - 19h30
]


# ============================================================================
# FONCTIONS DE NORMALISATION
# ============================================================================

def normalize_suppression(suppression_rate: float) -> float:
    """Normalise taux suppression en score 0-1."""
    return min(suppression_rate / 0.25, 1.0)


def normalize_wait(wait_ratio: float) -> float:
    """Normalise wait_ratio : 1.0 = nominal (0), 2.0 = max (1)."""
    return min(max(wait_ratio - 1.0, 0) / 1.0, 1.0)

def normalize_dwell(dwell_ratio: float) -> float:
    """
    Normalise le dwell_ratio en score 0-1.
    1.0 = Stationnement normal (Score 0)
    3.0 = Stationnement très long, 3x la normale (Score 1)
    """
    # On soustrait 1 pour que le score commence à 0 quand le ratio est nominal (1.0)
    # On divise par 2.0 pour atteindre le max (1.0) quand le ratio arrive à 3.0
    score = (dwell_ratio - 1.0) / 2.0
    return min(max(score, 0.0), 1.0)


def normalize_delay(delay_mean_min: float) -> float:
    """Normalise retard moyen en score 0-1."""
    return min(delay_mean_min / 6.0, 1.0)



# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def is_peak_hour(timestamp: str) -> bool:
    """
    Vérifie si le timestamp est en heures de pointe (jours de semaine).
    Heures de pointe : 7h30-9h30 et 16h30-19h30, weekdays only.
    """
    try:
        dt = datetime.fromisoformat(timestamp)
        # Vérifier si c'est un jour de semaine (0=lundi, 6=dimanche)
        if dt.weekday() >= 5:  # weekend
            return False
        
        hour_decimal = dt.hour + dt.minute / 60.0
        for start, end in PEAK_HOURS:
            if start <= hour_decimal < end:
                return True
        return False
    except:
        return False


def get_line_demand_weight(line: str) -> float:
    """Retourne le poids de demande pour une ligne (0-1)."""
    return DEMAND_WEIGHT.get(line, 0.5)


# ============================================================================
# FONCTIONS DE CALCUL DES SCORES
# ============================================================================



def query_meteo_db(timestamp: str) -> dict | None:
    """
    Récupère la ligne météo la plus proche du timestamp (±30 min)
    depuis Snowflake.
    """

    session = get_active_session()

    query = f"""
    SELECT
        LOGGED_AT,
        TEMPERATURE,
        WEATHER_LABEL,
        PRECIPITATION,
        WIND_SPEED,
        ABS(DATEDIFF(minute, LOGGED_AT, TO_TIMESTAMP('{timestamp}'))) AS diff_minutes
    FROM M2_ISD_EQUIPE_4_DB.PUBLIC.WEATHER_CURRENT_LOG
    WHERE ABS(DATEDIFF(minute, LOGGED_AT, TO_TIMESTAMP('{timestamp}'))) <= 30
    ORDER BY diff_minutes ASC
    LIMIT 1
    """

    df = session.sql(query).to_pandas()

    if df.empty:
        print(f"[Meteo DB] Aucune donnée autour de {timestamp}")
        return None

    row = df.iloc[0].to_dict()

    print(f"[Meteo DB] Trouvé : {row['LOGGED_AT']} | "
          f"{row['WEATHER_LABEL']} | diff={row['DIFF_MINUTES']} min")

    return row


def get_weather(kpi):
    timestamp = kpi.get("timestamp")

    if not timestamp:
        return 0.0

    row = query_meteo_db(timestamp)

    if not row:
        return 0.0

    condition = str(row.get("WEATHER_LABEL", "")).lower()
    precipitation = float(row.get("PRECIPITATION", 0))
    wind = float(row.get("WIND_SPEED", 0))

    #  Score météo enrichi (meilleur que ton ancien)
    score = 0.0

    # pluie / précipitation
    if precipitation > 5:
        score += 0.6
    elif precipitation > 0:
        score += 0.3

    # vent fort
    if wind > 60:
        score += 0.5
    elif wind > 30:
        score += 0.2

    # condition texte
    if "orage" in condition or "storm" in condition:
        score += 0.7
    elif "neige" in condition or "snow" in condition:
        score += 0.6
    elif "pluie" in condition or "rain" in condition:
        score += 0.4
    elif "brouillard" in condition or "fog" in condition:
        score += 0.3

    score = min(score, 1.0)

    print(f"[Weather] {condition} | pluie={precipitation} | vent={wind} → score={score}")

    return score


# ============================================================================
# CALCUL DES 3 COMPOSANTES DU SCORE 3D
# ============================================================================

def compute_s_operational(kpi: dict) -> float:
    """
    S_operational : Sévérité de la dégradation de service.
    Mesure la dégradation de l'infrastructure (suppression, retards, fréquence, irrégularité).
    
    Poids :
    - 35% suppression_rate
    - 25% dwell_anomaly (si fourni, sinon 0)
    - 25% delay_mean_min
    - 15% irregularity_index
    
    Retourne un score entre 0 et 1.
    """
    supp_norm = normalize_suppression(kpi.get("suppression_rate", 0))
    dwell_norm = normalize_dwell(kpi.get("dwell_anomaly_min", 0))
    delay_norm = normalize_delay(kpi.get("delay_mean_min", 0))
    
    s_op = (
        0.40 * supp_norm
        + 0.30 * dwell_norm
        + 0.30 * delay_norm
    
    )
    return round(min(s_op, 1.0), 3)


def compute_s_impact_usager(kpi: dict, timestamp: str) -> float:
    """
    S_impact_usager : Impact sur l'expérience des passagers.
    Mesure le ressenti usager (attente, contexte, demande).
    
    Poids :
    - 70% wait_ratio (normalisé) — signal principal
    - 15% peak_hour_factor (1.0 en heures de pointe, 0.3 sinon)
    - 15% demand_weight de la ligne
    
    Retourne un score entre 0 et 1.
    """
    wait_norm = normalize_wait(kpi.get("wait_ratio", 1.0))
    # On applique les facteurs (heure de pointe + demande) COMME MULTIPLICATEURS du temps d'attente
    # Si le temps d'attente est nominal (wait_norm = 0), l'impact est 0.
    
    peak_multiplier = 1.0 if is_peak_hour(timestamp) else 0.5
    demand_multiplier = get_line_demand_weight(kpi.get("line", "RER_A")) # 0.35 à 1.0
    
    # Formule combinée : l'impact dépend avant tout de l'attente, aggravée par la pointe et la demande
    # Le max théorique est wait_norm * 1.0 * 1.0.  On lisse un peu.
    s_impact = wait_norm * peak_multiplier * demand_multiplier
    
    # Pour s'assurer de bien utiliser tout le spectre 0-1 on remonte un peu le score final
    s_impact = s_impact * 2.0  # (puisque offpeak et petites lignes vont baisser le score)

    return round(min(s_impact, 1.0), 3)


""" pour chaque snapshot, on peut aussi calculer un score de risque d'aggravation à court terme basé 
sur le contexte externe (météo, événements, grèves) qui peut influencer la durée et la sévérité de la perturbation. 
Ce score pourrait être utilisé pour prioriser les alertes qui risquent de s'aggraver rapidement. """

def compute_s_impact_contexte(kpi: dict) -> float:
    """
    Mesure le risque externe d'aggravation.
    Combine météo + heure + criticité ligne.
    """

    # ─── 1. MÉTÉO ───────────────────────────
    weather_score = get_weather(kpi)  # 0 → 1

    # ─── 2. HEURE DE POINTE ─────────────────
    peak_score = 0.0
    try:
        dt = datetime.fromisoformat(kpi.get("timestamp", ""))
        hour_decimal = dt.hour + dt.minute / 60

        if dt.weekday() < 5:  # semaine
            if 7.5 <= hour_decimal <= 9.5 or 16.5 <= hour_decimal <= 19.5:
                peak_score = 1.0
            elif 6 <= hour_decimal <= 22:
                peak_score = 0.4
    except:
        pass

    # ─── 3. IMPORTANCE DE LA LIGNE ──────────
    demand_score = get_line_demand_weight(kpi.get("line", "RER_A"))  # 0.35 → 1.0

    # ─── 4. COMBINAISON ─────────────────────
    # logique importante :
    # - météo = facteur déclencheur
    # - pointe = amplificateur
    # - demande = amplificateur réseau

    s_context = (
        0.5 * weather_score +
        0.3 * peak_score +
        0.2 * demand_score
    )

    return round(min(s_context, 1.0), 3)

def compute_s_confidence(kpi: dict) -> float:
    """
    S_confidence : niveau de confiance dans la perturbation détectée.

    Logique :
    - pondérer les signaux selon leur importance métier
    - éviter qu'un seul signal faible déclenche une forte confiance

    Poids :
    - suppression_rate : 0.4 (signal très fort)
    - wait_ratio       : 0.3 (ressenti client)
    - delay_mean       : 0.2 (retard opérationnel)
    - dwell_anomaly    : 0.1 (signal technique)

    Retourne un score entre 0 et 1.
    """

    score = 0.0

    # ───────────────
    # 1. Suppression (signal fort)
    # ───────────────
    if kpi.get("suppression_rate", 0) > 0.10:
        score += 0.4

    # ───────────────
    # 2. Attente (très important côté client)
    # ───────────────
    if kpi.get("wait_ratio", 1.0) > 1.3:
        score += 0.3

    # ───────────────
    # 3. Retard (modéré)
    # ───────────────
    if kpi.get("delay_mean_min", 0) > 3:
        score += 0.2

    # ───────────────
    # 4. Blocage à quai (faible mais utile)
    # ───────────────
    if kpi.get("dwell_anomaly_min", 1.0) > 1.8 :    # maintenant en ratio
        score += 0.1

    # incohérence suppression sans attente
    if kpi.get("suppression_rate", 0) > 0.2 and kpi.get("wait_ratio", 1.0) < 1.2:
        score *= 0.8
    return round(min(score, 1.0), 3)


def compute_priority_score_3d(
    s_operational: float,
    s_impact_usager: float,
    s_confidence: float
) -> float:
    """
    Calcule la priorité finale en combinant les 3 dimensions.
    Formule :
    Priorité = 0.60*S_operational + 0.25*S_impact_usager + 0.15*S_confidence
    Retourne un score entre 0 et 1.
    """
    priority = (
        0.60 * s_operational
        + 0.25 * s_impact_usager
        + 0.15 * s_confidence
    )
    return round(min(priority, 1.0), 3)


def decide_alert_level(priority_score: float) -> str:
    """
    Transforme un score de priorité en niveau d'alerte.
    
    - ROUGE : >= 0.70  (critique)
    - ORANGE : 0.50-0.70  (sérieux)
    - JAUNE : 0.35-0.50  (attention)
    - VERT : < 0.35  (nominal)
    """
    if priority_score >= 0.70:
        return "ROUGE"
    elif priority_score >= 0.50:
        return "ORANGE"
    elif priority_score >= 0.30:
        return "JAUNE"
    else:
        return "VERT"


# ============================================================================
# FONCTIONS POUR EXPLICATION ET SYNTHÈSE
# ============================================================================

def explain_alert(kpi: dict) -> str:
    """
    Agent Explicatif : justifie l'alerte par ses signaux dominants (KPIs purs).
    Retourne une liste lisible des raisons.
    """
    reasons = []

    if kpi.get("suppression_rate", 0) > 0.15:
        reasons.append(f"taux de suppression élevé ({kpi['suppression_rate']:.0%})")
    
    if kpi.get("delay_mean_min", 0) > 5:
        reasons.append(f"retard moyen important ({kpi['delay_mean_min']:.1f} min)")
    
    if kpi.get("wait_ratio", 1.0) > 1.5:
        reasons.append(f"temps d'attente dégradé (ratio {kpi['wait_ratio']:.2f})")
    
    if kpi.get("dwell_anomaly_min", 1.0) > 2:
        reasons.append(f"stationnement anormal ratio ({kpi['dwell_anomaly_min']:.2f})")

    return ", ".join(reasons) if reasons else "situation proche du nominal"


def estimate_recovery(priority_score: float, kpi: dict) -> str:
    """
    Agent Estimateur : estime la durée avant retour au nominal selon la sévérité.
    
    - Score >= 0.75 : 30-45 min (très dégradé)
    - Score 0.55-0.75 : 15-30 min (dégradé)
    - Score 0.30-0.55 : moins de 15 min (léger)
    - Score < 0.30 : retour nominal (déjà ok)
    """
    if priority_score >= 0.75:
        return "30 à 45 minutes"
    elif priority_score >= 0.55:
        return "15 à 30 minutes"
    elif priority_score >= 0.30:
        return "moins de 15 minutes"
    else:
        return "retour nominal"


def generate_summary(
    kpi: dict,
    alert_level: str,
    explanation: str,
    recovery: str
) -> str:
    """
    Agent Rédacteur : transforme la sortie technique en message métier.
    Prêt à afficher dans la démo ou un dashboard.
    """
    return (
        f"Ligne {kpi['line']} en alerte {alert_level}. "
        f"Signaux observés : {explanation}. "
        f"Reprise estimée : {recovery}."
    )


