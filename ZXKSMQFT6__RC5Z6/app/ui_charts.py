import streamlit as st
import altair as alt
import pandas as pd
import numpy as np


def create_gauge_priority(score: float):
    """Gauge pour Score de Priorité — remplacé par progress bar + badge coloré."""
    score = float(np.clip(score, 0, 1))

    if score < 0.30:
        color = "#4CAF50"
        label = "🟢 Faible"
    elif score < 0.50:
        color = "#FFC107"
        label = "🟡 Modéré"
    elif score < 0.70:
        color = "#FF9800"
        label = "🟠 Élevé"
    else:
        color = "#F44336"
        label = "🔴 Critique"

    st.markdown(f"**Score Priorité : {score:.2f}** — {label}")
    st.progress(score)
    st.markdown(
        f"<div style='background:{color};color:white;text-align:center;"
        f"border-radius:8px;padding:8px;font-weight:bold;font-size:1.1rem;'>"
        f"{score:.2f}</div>",
        unsafe_allow_html=True,
    )


def create_radar_scores(results: list) -> alt.Chart:
    """Radar scores moyens — remplacé par barres horizontales Altair."""
    avg_analysis = float(np.mean([r['analysis_score'] for r in results]))
    avg_anomaly = float(np.mean([r['anomaly_score'] for r in results]))
    avg_impact = float(np.mean([
        min(0.5 * r['suppression_rate'] + 0.5 * min(r['wait_ratio'] / 2.0, 1.0), 1.0)
        for r in results
    ]))

    df = pd.DataFrame({
        "Dimension": ["Analyse", "Anomalie TS", "Impact"],
        "Score": [avg_analysis, avg_anomaly, avg_impact],
    })

    return (
        alt.Chart(df)
        .mark_bar(color="#FF6B35", cornerRadiusTopRight=5, cornerRadiusBottomRight=5)
        .encode(
            x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 1]), title="Score moyen"),
            y=alt.Y("Dimension:N", sort="-x", title=""),
            tooltip=["Dimension:N", alt.Tooltip("Score:Q", format=".3f")],
        )
        .properties(title="Radar Scores Moyens", width=300, height=180)
    )


def create_alert_distribution(results: list) -> alt.Chart:
    """Pie chart alertes — remplacé par donut Altair."""
    alert_counts = (
        pd.Series([r["alert_level"] for r in results])
        .value_counts()
        .reset_index()
    )
    alert_counts.columns = ["alert_level", "count"]

    color_map = {
        "VERT": "#4CAF50",
        "JAUNE": "#FFC107",
        "ORANGE": "#FF9800",
        "ROUGE": "#F44336",
    }

    return (
        alt.Chart(alert_counts)
        .mark_arc(innerRadius=40)
        .encode(
            theta=alt.Theta("count:Q"),
            color=alt.Color(
                "alert_level:N",
                scale=alt.Scale(
                    domain=list(color_map.keys()),
                    range=list(color_map.values()),
                ),
                legend=alt.Legend(title="Niveau"),
            ),
            tooltip=["alert_level:N", "count:Q"],
        )
        .properties(title="Distribution des Alertes", width=250, height=250)
    )


def create_timeseries_priority(results: list) -> alt.Chart:
    """Timeseries scores par ligne — remplacé par line chart Altair."""
    df = pd.DataFrame(results)[["timestamp", "priority_score", "line", "alert_level", "suppression_rate"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")

    # Lignes de seuil (équivalent add_hline de Plotly)
    thresholds = pd.DataFrame([
        {"y": 0.30, "label": "JAUNE",  "color": "#FFC107"},
        {"y": 0.50, "label": "ORANGE", "color": "#FF9800"},
        {"y": 0.70, "label": "ROUGE",  "color": "#F44336"},
    ])

    lines = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("timestamp:T", title="Heure", axis=alt.Axis(format="%H:%M:%S")),
            y=alt.Y("priority_score:Q", scale=alt.Scale(domain=[0, 1]), title="Score priorité"),
            color=alt.Color("line:N", legend=alt.Legend(title="Ligne")),
            tooltip=[
                alt.Tooltip("timestamp:T", format="%H:%M:%S"),
                "line:N",
                alt.Tooltip("priority_score:Q", format=".3f"),
                "alert_level:N",
                alt.Tooltip("suppression_rate:Q", format=".1%"),
            ],
        )
    )

    rules = (
        alt.Chart(thresholds)
        .mark_rule(strokeDash=[6, 4])
        .encode(
            y="y:Q",
            color=alt.Color("color:N", scale=None),
            tooltip=["label:N", "y:Q"],
        )
    )

    return (
        (lines + rules)
        .properties(title="Évolution Scores par Ligne", width=450, height=300)
        .interactive()
    )


def create_suppression_bar(results: list) -> alt.Chart:
    """Bar chart suppression par ligne — remplacé par barres Altair."""
    df = (
        pd.DataFrame(results)
        .groupby("line")["suppression_rate"]
        .mean()
        .reset_index()
    )

    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("line:N", title="Ligne", sort="-y"),
            y=alt.Y(
                "suppression_rate:Q",
                scale=alt.Scale(domain=[0, 1]),
                title="Taux de suppression",
                axis=alt.Axis(format="%"),
            ),
            color=alt.Color(
                "suppression_rate:Q",
                scale=alt.Scale(scheme="reds", domain=[0, 1]),
                legend=None,
            ),
            tooltip=[
                "line:N",
                alt.Tooltip("suppression_rate:Q", format=".1%", title="Suppression"),
            ],
        )
        .properties(title="Taux de Suppression Moyen par Ligne", width=280, height=280)
    )


def display_alert_badge(level: str):
    """Affiche le badge statut IA — inchangé (pas de Plotly)."""
    colors = {
        "VERT":   ("#4CAF50", "✅ VERT"),
        "JAUNE":  ("#FFC107", "⚠️ JAUNE"),
        "ORANGE": ("#FF9800", "⚠️ ORANGE"),
        "ROUGE":  ("#F44336", "🔴 ROUGE"),
    }

    color, text = colors.get(level, ("gray", "❓"))

    st.markdown(
        f"""
        <div style="
            background-color: {color};
            color: white;
            padding: 15px 20px;
            border-radius: 8px;
            text-align: center;
            font-weight: bold;
            font-size: 18px;
        ">
        {text}
        </div>
        """,
        unsafe_allow_html=True,
    )
create_gauge_priority_native = create_gauge_priority
