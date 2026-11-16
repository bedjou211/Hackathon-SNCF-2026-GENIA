"""
Donnees simulees pour developpement/test sans dependre du binome 1.
Contient 15 cas de test metier realistes avec gare, direction et IDs IDFM.
Format : KPIs purs de Binome 1.
"""

SAMPLE_CASES = [
    
    # ─────────────── NOMINAL (baseline)
    {"timestamp": "2026-03-20T08:00:00", "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.02, "delay_mean_min": 1.0, "wait_ratio": 1.0, "headway_ratio": 1.0},

    {"timestamp": "2026-03-20T08:05:00", "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.03, "delay_mean_min": 1.2, "wait_ratio": 1.05, "headway_ratio": 1.0},

    {"timestamp": "2026-03-20T08:10:00", "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.02, "delay_mean_min": 1.1, "wait_ratio": 1.0, "headway_ratio": 1.0},

    # ─────────────── DÉGRADATION PROGRESSIVE
    {"timestamp": "2026-03-20T08:15:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.05, "delay_mean_min": 2.5, "wait_ratio": 1.2, "headway_ratio": 1.1},

    {"timestamp": "2026-03-20T08:20:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.08, "delay_mean_min": 3.5, "wait_ratio": 1.4, "headway_ratio": 1.2},

    {"timestamp": "2026-03-20T08:25:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.12, "delay_mean_min": 4.5, "wait_ratio": 1.6, "headway_ratio": 1.3},

    # ─────────────── RUPTURE BRUTALE (A1)
    {"timestamp": "2026-03-20T08:30:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.40, "delay_mean_min": 8.0, "wait_ratio": 2.2, "headway_ratio": 1.8},

    # ─────────────── PERSISTANCE
    {"timestamp": "2026-03-20T08:35:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.45, "delay_mean_min": 9.0, "wait_ratio": 2.4, "headway_ratio": 1.9},

    {"timestamp": "2026-03-20T08:40:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.48, "delay_mean_min": 9.5, "wait_ratio": 2.5, "headway_ratio": 2.0},

    # ─────────────── AMÉLIORATION
    {"timestamp": "2026-03-20T08:45:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.30, "delay_mean_min": 6.0, "wait_ratio": 1.8, "headway_ratio": 1.5},

    {"timestamp": "2026-03-20T08:50:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.15, "delay_mean_min": 4.0, "wait_ratio": 1.4, "headway_ratio": 1.2},

    # ─────────────── RETOUR NORMAL
    {"timestamp": "2026-03-20T08:55:00",
     "line": "RER_B", "stop_name": "Gare du Nord", "direction": "aller",
     "suppression_rate": 0.05, "delay_mean_min": 2.0, "wait_ratio": 1.1, "headway_ratio": 1.0},
]



def get_sample_case(index: int) -> dict:
    """Recupere un cas d'exemple par index."""
    if 0 <= index < len(SAMPLE_CASES):
        return SAMPLE_CASES[index].copy()
    raise IndexError(f"Indice {index} hors limites (0-{len(SAMPLE_CASES)-1})")


def get_all_cases() -> list:
    """Retourne tous les cas de test."""
    return [case.copy() for case in SAMPLE_CASES]
