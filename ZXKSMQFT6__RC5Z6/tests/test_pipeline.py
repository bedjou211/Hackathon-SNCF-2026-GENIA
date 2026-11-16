"""
Tests unitaires de la pipeline Multi-Agents (Binôme 2).
Valide spécifiquement le comportement de chaque Agent (Breaker, Déduplication, etc.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from agents import run_pipeline


def test_agent_analyseur_nominal():
    """Test 1 : Situation nominale (Tous les indicateurs au vert) -> Alerte VERT."""
    kpi = {
        "line": "RER_A",
        "suppression_rate": 0.0,
        "delay_mean_min": 0.5,
        "headway_ratio": 1.0,
        "wait_ratio": 1.0,
        "dwell_anomaly_min": 0.0,
        "irregularity_index": 1.0
    }
    result = run_pipeline(kpi)
    
    assert result["alert_level"] == "VERT", f"Expected VERT, got {result['alert_level']}"
    assert result["priority_score"] < 0.30, "Priority score should be < 0.30"
    print("✅ Agent Analyseur (Nominal) PASSED")


def test_agent_breaker_urgency():
    """Test 2 : Circuit Breaker -> Force une alerte ORANGE si suppression critique, même si le reste est bon."""
    kpi = {
        "line": "RER_B",
        "suppression_rate": 0.30,  # > 0.25 déclenche le breaker
        "delay_mean_min": 0.0,
        "headway_ratio": 1.0,
        "wait_ratio": 1.0,
        "dwell_anomaly_min": 0.0,
        "irregularity_index": 1.0
    }
    result = run_pipeline(kpi)
    
    # Le score brut sans breaker serait très bas car tout le reste est à 0
    # Mais le Breaker doit forcer "ORANGE"
    assert result["alert_level"] in ["ORANGE", "ROUGE"], "Breaker should force at least ORANGE"
    # Le breaker peut aussi modifier la raison pour indiquer la suppression
    print("✅ Agent Breaker (Urgence Suppression) PASSED")


def test_agent_deduplication_active():
    """Test 3 : Déduplication -> Un incident existant ne doit pas être remonté (incident_a_remonter = False)."""
    # Ligne P a un incident actif dans existing_incidents.json
    kpi = {
        "line": "LINE_P",
        "line_id": "line:IDFM:C01730",
        "suppression_rate": 0.50, # Forte perturbation
        "delay_mean_min": 15.0,
        "headway_ratio": 2.0,
        "wait_ratio": 2.0,
        "dwell_anomaly_min": 10.0,
        "irregularity_index": 2.0
    }
    result = run_pipeline(kpi)
    
    assert result["alert_level"] == "ROUGE", "Should be ROUGE due to high metrics"
    assert result["incident_existant"] == True, "Deduplication Agent should flag existing incident"
    assert result["incident_a_remonter"] == False, "CCU already handling it, do not alert again"
    print("✅ Agent Deduplication (Incident Existant bloqué) PASSED")


def test_agent_confiance_multi_signals():
    """Test 4 : Confiance -> Plusieurs signaux dégradés font monter le S_confidence."""
    # On met 4 signaux juste au-dessus de leur seuil de dégradation pour activer la confiance
    kpi = {
        "line": "LINE_N",
        "suppression_rate": 0.15,   # seuil > 0.10
        "delay_mean_min": 4.0,      # seuil > 3
        "wait_ratio": 1.4,          # seuil > 1.3
        "dwell_anomaly_min": 4.0,   # seuil > 3
        "headway_ratio": 1.0,
        "irregularity_index": 1.0
    }
    result = run_pipeline(kpi)
    
    # Le score final devrait au moins atteindre le JAUNE ou plus grâce à la confiance
    assert result["priority_score"] >= 0.30, "Confidence should push the score higher"
    assert result["alert_level"] != "VERT", "Should not be VERT with 4 degraded signals"
    print("✅ Agent Confiance (Signaux Convergents) PASSED")


def test_agent_explicatif_et_estimateur():
    """Test 5 : Agents Explicatif & Estimateur -> Vérifie la génération de texte et l'estimation de reprise."""
    kpi = {
        "line": "RER_A",
        "suppression_rate": 0.0,
        "delay_mean_min": 10.0,
        "wait_ratio": 2.0,
        "dwell_anomaly_min": 0.0,
        "headway_ratio": 1.0,
        "irregularity_index": 1.0
    }
    result = run_pipeline(kpi)
    
    explanation = result["explanation"]
    assert "retard moyen" in explanation and "temps d'attente" in explanation, "Explanation lacks accurate causes"
    
    recovery = result["estimated_recovery"]
    assert isinstance(recovery, str) and len(recovery) > 5, "Recovery estimation is invalid"
    print("✅ Agents Explicatif & Estimateur PASSED")


if __name__ == "__main__":
    print("🧪 Validation Multi-Agents (Binôme 2)...\n")
    
    test_agent_analyseur_nominal()
    test_agent_breaker_urgency()
    test_agent_deduplication_active()
    test_agent_confiance_multi_signals()
    test_agent_explicatif_et_estimateur()
    
    print("\n✅ ✅ ✅ TOUTE L'ARCHITECTURE MULTI-AGENTS EST VALIDÉE ✅ ✅ ✅")
