import numpy as np
from datetime import datetime

def generate_realtime_kpi():
    """Génère un KPI simulé pour temps réel avec variation réaliste des données."""
    # Lignes SNCF Transilien et RER uniquement
    lines = ["RER_A", "RER_B", "RER_C", "RER_D", "RER_E", "LINE_H", "LINE_J", "LINE_K", "LINE_L", "LINE_N", "LINE_P", "LINE_R", "LINE_U"]
    
    # Gares par ligne
    STOPS_BY_LINE = {
        "RER_A": ["La Défense", "Nation", "Châtelet", "Gare de Lyon", "Val de Fontenay", "Nanterre"],
        "RER_B": ["Gare du Nord", "Châtelet", "Aulnay-sous-Bois", "Aéroport CDG 2", "Denfert-Rochereau"],
        "RER_C": ["Juvisy", "Bibliothèque F. Mitterrand", "Musée d'Orsay", "Versailles Chantiers"],
        "RER_D": ["Gare de Lyon", "Corbeil-Essonnes", "Melun", "Villeneuve-Saint-Georges"],
        "RER_E": ["Haussmann – Saint-Lazare", "Magenta", "Rosa Parks", "Chelles-Gournay"],
        "LINE_H": ["Gare du Nord", "Pontoise", "Luzarches", "Persan-Beaumont"],
        "LINE_J": ["Saint-Lazare", "Mantes-la-Jolie", "Ermont – Eaubonne", "Conflans"],
        "LINE_K": ["Gare du Nord", "Crépy-en-Valois", "Mitry-Claye"],
        "LINE_L": ["Saint-Lazare", "Versailles RD", "Saint-Nom-la-Bretèche", "Cergy"],
        "LINE_N": ["Montparnasse", "Rambouillet", "Dreux", "Mantes-la-Jolie"],
        "LINE_P": ["Gare de l'Est", "Meaux", "Coulommiers", "Château-Thierry"],
        "LINE_R": ["Gare de Lyon", "Melun", "Montargis", "Montereau"],
        "LINE_U": ["La Vérriere", "La Défense"],
    }
    
    # Distribuer les données pour avoir une bonne variété d'alertes
    risk_level = np.random.choice(["low", "medium", "high"], p=[0.5, 0.35, 0.15])
    
    if risk_level == "low":
        suppression = np.random.uniform(0.0, 0.08)
        delay = np.random.uniform(0.5, 2.5)
        headway = np.random.uniform(1.0, 1.15)
        wait = np.random.uniform(1.0, 1.2)
        dwell = np.random.uniform(0.0, 1.5)
    elif risk_level == "medium":
        suppression = np.random.uniform(0.08, 0.25)
        delay = np.random.uniform(2.5, 8.0)
        headway = np.random.uniform(1.15, 1.7)
        wait = np.random.uniform(1.2, 1.8)
        dwell = np.random.uniform(1.5, 5.0)
    else:
        suppression = np.random.uniform(0.25, 0.95)
        delay = np.random.uniform(8.0, 15.0)
        headway = np.random.uniform(1.7, 2.5)
        wait = np.random.uniform(1.8, 2.5)
        dwell = np.random.uniform(5.0, 12.0)
    
    selected_line = np.random.choice(lines)
    stops = STOPS_BY_LINE.get(selected_line, ["Gare"])
    selected_stop = np.random.choice(stops)
    
    # Mapping basique des IDs de ligne
    LINE_IDS = {
        "RER_A": "line:IDFM:C01742", "RER_B": "line:IDFM:C01743",
        "RER_C": "line:IDFM:C01727", "RER_D": "line:IDFM:C01728",
        "RER_E": "line:IDFM:C01729", "LINE_H": "line:IDFM:C01737",
        "LINE_J": "line:IDFM:C01740", "LINE_L": "line:IDFM:C01739",
        "LINE_N": "line:IDFM:C01736", "LINE_P": "line:IDFM:C01730",
    }
    
    return {
        "timestamp": datetime.now().isoformat(),
        "line": selected_line,
        "line_id": LINE_IDS.get(selected_line, f"line:IDFM:{selected_line}"),
        "stop_name": selected_stop,
        "stop_id": f"stop_area:IDFM:dummy_{selected_stop.replace(' ', '')}",
        "direction": np.random.choice(["aller", "retour"]),
        "suppression_rate": suppression,
        "delay_mean_min": delay,
        "headway_ratio": headway,
        "wait_ratio": wait,
        "dwell_anomaly_min": dwell,
        "irregularity_index": np.random.uniform(1.0, 1.0 + wait - 0.8),
    }
