"""
Agent Explicateur basé sur Snowflake Cortex (LLM)
Génère une explication métier à partir des KPIs + contexte
"""

from snowflake.snowpark.context import get_active_session


class AgentExplicateurLLM:

    def __init__(self, model="mistral-large"):
        self.session = get_active_session()
        self.model = model

    def explain(self, kpi: dict, context: dict) -> dict:

        prompt = self._build_prompt(kpi, context)

        query = f"""
        SELECT SNOWFLAKE.CORTEX.COMPLETE(
            '{self.model}',
            $$ {prompt} $$
        ) AS RESPONSE
        """
        try:
            result = self.session.sql(query).collect()
            response = result[0]["RESPONSE"]

        except Exception as e:
            response = f"[LLM ERROR] {str(e)}"

        return {
            "llm_explanation": response,
            "prompt_used": prompt
        }

    def _build_prompt(self, kpi, context):
    
        ts = context.get("ts_details", {})
    
        return f"""
    Tu es un expert en exploitation ferroviaire SNCF.
    
    Ton objectif est d'expliquer une anomalie réseau en t'appuyant STRICTEMENT sur les scores calculés.
    
    ==============================
    📊 CONTEXTE KPI
    ==============================
    - Ligne : {kpi.get("line")}
    - Suppression : {kpi.get("suppression_rate")}
    - Retard moyen : {kpi.get("delay_mean_min")} min
    - Attente : {kpi.get("wait_ratio")}x
    
    ==============================
    🧠 LOGIQUE DES SCORES
    ==============================
    
    🔵 Score TS (temporal) :
    Mesure le caractère anormal du comportement dans le temps.
    
    Score final TS = somme pondérée des angles :
    - court terme (z_short) : 18%
    - journalier : 10%
    - hebdomadaire : 8%
    - jour de semaine : 6%
    - tendance (slope) : 18%
    - corrélation : 14%
    - persistance : 20%
    - propagation : 6%
    
    Formule Z-score :
    z = (valeur - moyenne) / écart-type
    
    Formule pente (slope) :
    slope = Σ(x - x̄)(y - ȳ) / Σ(x - x̄)²
    
    ==============================
    📈 INTERPRÉTATION DES SCORES
    ==============================
    
    - z_short :
      détecte une rupture brutale (choc immédiat)
    
    - z_daily / z_weekly / z_dow :
      compare au comportement habituel (anomalie contextuelle)
    
    - slope :
      mesure la dynamique (dégradation ou amélioration)
    
    - correlation :
      cohérence entre les KPIs → signature réelle d’incident
    
    - persistence :
      nombre de snapshots consécutifs anormaux → filtre le bruit
    
    - propagation :
      part des arrêts impactés → diffusion réseau
    
    ==============================
    📊 VALEURS ACTUELLES
    ==============================
    
    - z_short : {ts.get("z_short", 0):.3f}
    - z_daily : {ts.get("z_daily", 0):.3f}
    - z_weekly : {ts.get("z_weekly", 0):.3f}
    - z_dow : {ts.get("z_dow", 0):.3f}
    - slope : {ts.get("slope", 0):.3f}
    - correlation : {ts.get("correlation", 0):.3f}
    - persistence : {ts.get("persistence", 0):.3f}
    - propagation : {ts.get("propagation", 0):.3f}
    
    ==============================
    ⚙️ DÉCISION
    ==============================
    - Score TS : {context.get("ts_score")}
    - Angle dominant : {context.get("ts_angle")}
    - Niveau alerte : {context.get("alert_level")}
    - Score priorité : {context.get("priority_score")}
    
    ==============================
    🎯 OBJECTIF
    ==============================
    1. Identifier la cause principale (angle dominant)
    2. Décrire la dynamique (rupture, dérive, propagation…)
    3. Expliquer l’impact réseau et usager
    
    ==============================
    📝 FORMAT
    ==============================
    - 2 à 3 phrases maximum
    - Style métier SNCF
    - Pas de jargon technique inutile
    
    IMPORTANT :
    - Si propagation > 0.7 → mentionner effet réseau
    - Si persistence > 0.6 → mentionner incident installé
    - Si z_short > 0.6 → mentionner rupture brutale
    
    Réponse :
    """