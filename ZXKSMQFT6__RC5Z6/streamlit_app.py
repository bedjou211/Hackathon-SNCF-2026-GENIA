"""
Dashboard Streamlit - Binôme 2
SNCF Vigi Numérique — Agent de Détection de Perturbations
Compatible Streamlit in Snowflake (SiS) — 100% styles inline
"""

import streamlit as st
import numpy as np
from datetime import datetime
from app.agents import AgentOrchestrateur, load_active_alerts_index, generate_llm_explanation
from app.data_loader import load_latest_batch, close_old_incidents, load_active_alerts
import pandas as pd
from app.chat_agent import (
    build_chat_context,
    answer_question,
    CortexLLMClient,
)
import time
from contextlib import contextmanager
from collections import defaultdict



# ─────────────────────────────────────────────
# DEBUG / PROFILING
# ─────────────────────────────────────────────
DEBUG_PERF = True  # mets False pour couper facilement

def _init_perf():
    if "perf_log" not in st.session_state:
        st.session_state.perf_log = []
    st.session_state.perf_log = []

def _perf_add(step, duration_s, extra=None):
    if not DEBUG_PERF:
        return
    if "perf_log" not in st.session_state:
        st.session_state.perf_log = []
    st.session_state.perf_log.append({
        "step": step,
        "duration_ms": round(duration_s * 1000, 1),
        "extra": extra or ""
    })

@contextmanager
def perf_step(step, extra=None):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        _perf_add(step, dt, extra=extra)

def render_perf_panel():
    if not DEBUG_PERF:
        return

    perf_log = st.session_state.get("perf_log", [])
    if not perf_log:
        return

    total_ms = sum(x["duration_ms"] for x in perf_log)

    st.markdown("### 🧪 Debug performances")

    c1, c2 = st.columns([1, 3])
    with c1:
        st.metric("Temps total mesuré", f"{total_ms:.1f} ms")
    with c2:
        slowest = sorted(perf_log, key=lambda x: x["duration_ms"], reverse=True)[:3]
        slow_txt = " | ".join([f"{x['step']} ({x['duration_ms']} ms)" for x in slowest])
        st.caption(f"Étapes les plus lentes : {slow_txt}")

    rows_html = ""
    for row in perf_log:
        extra = f" — {row['extra']}" if row["extra"] else ""
        color = "#E53935" if row["duration_ms"] > 1000 else "#FB8C00" if row["duration_ms"] > 300 else "#2E7D32"
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;gap:12px;'
            f'padding:8px 10px;border-bottom:1px solid #EEF2F7;">'
            f'<span style="font-size:13px;color:#111827;font-weight:600;">{row["step"]}{extra}</span>'
            f'<span style="font-size:13px;color:{color};font-weight:700;">{row["duration_ms"]:.1f} ms</span>'
            f'</div>'
        )

    st.markdown(
        f"""
        <div style="
            background:#FFFFFF;
            border:1px solid #E5E7EB;
            border-radius:14px;
            overflow:hidden;
            box-shadow:0 2px 8px rgba(15,23,42,0.04);
            margin-top:8px;
            margin-bottom:16px;
        ">
            {rows_html}
        </div>
        """,
        unsafe_allow_html=True
    )


# ─────────────────────────────────────────────
# AUTO REFRESH
# ─────────────────────────────────────────────
@st.fragment(run_every="15s")
def auto_refresh_fragment():
    if "auto_refresh_last_ping" not in st.session_state:
        st.session_state.auto_refresh_last_ping = time.time()
        return

    now_ts = time.time()
    if now_ts - st.session_state.auto_refresh_last_ping >= 14.5:
        st.session_state.auto_refresh_last_ping = now_ts
        st.rerun()


# ─────────────────────────────────────────────
# COULEURS & STYLES
# ─────────────────────────────────────────────
ALERT_COLORS = {
    "ROUGE":  "#E53935",
    "ORANGE": "#FB8C00",
    "JAUNE":  "#FDD835",
    "VERT":   "#43A047",
}

LINE_COLORS = {
    "RER_A": "#E30512",
    "RER_B": "#5291CE",
    "RER_C": "#FFCE00",
    "RER_D": "#00814F",
    "RER_E": "#C04191",
    "LINE_H": "#9E66A1",
    "LINE_J": "#C9D200",
    "LINE_K": "#A1A000",
    "LINE_L": "#00B0FF",
    "LINE_N": "#007CB0",
    "LINE_P": "#F78F4F",
    "LINE_R": "#E59FDB",
    "LINE_U": "#9B9632",
    "LINE_V": "#9E66A1",
}

ALL_NETWORK_LINES = [
    "RER_A", "RER_B", "RER_C", "RER_D", "RER_E",
    "LINE_H", "LINE_J", "LINE_K", "LINE_L",
    "LINE_N", "LINE_P", "LINE_R", "LINE_U", "LINE_V",
]


# ─────────────────────────────────────────────
# HELPERS UI
# ─────────────────────────────────────────────
def _card(content, padding="20px 24px", bg="#fff", border="1px solid #E8E8E8", radius="12px", extra=""):
    return (
        f'<div style="background:{bg};border:{border};border-radius:{radius};'
        f'padding:{padding};{extra}">{content}</div>'
    )

def _badge(text, bg, color="#fff", radius="20px", px="5px 14px", fs="12px", fw="600"):
    return (
        f'<span style="background:{bg};color:{color};border-radius:{radius};'
        f'padding:{px};font-size:{fs};font-weight:{fw};display:inline-block;">{text}</span>'
    )

def _label(text):
    return (
        f'<p style="font-size:11px;font-weight:600;color:#9CA3AF;'
        f'text-transform:uppercase;letter-spacing:.07em;margin:0 0 6px;">{text}</p>'
    )

def alert_order(level):
    return {"ROUGE": 0, "ORANGE": 1, "JAUNE": 2, "VERT": 3}.get(level, 4)

def panel_start(title, title_color="#111827", border="#E5E7EB", bg="#FFFFFF"):
    return (
        f'<div style="'
        f'background:{bg};'
        f'border:1px solid {border};'
        f'border-radius:16px;'
        f'padding:18px 18px 14px 18px;'
        f'margin-bottom:14px;'
        f'box-shadow:0 2px 8px rgba(15,23,42,0.04);'
        f'">'
        f'<div style="'
        f'font-size:15px;'
        f'font-weight:800;'
        f'color:{title_color};'
        f'margin:0 0 14px 0;'
        f'padding-bottom:10px;'
        f'border-bottom:1px solid #EEF2F7;'
        f'">{title}</div>'
    )

def panel_end():
    return '</div>'


# ─────────────────────────────────────────────
# HELPERS ÉTAT PERSISTANT
# ─────────────────────────────────────────────
def triplet_key_from_kpi(kpi: dict):
    return (
        str(kpi.get("line", "UNKNOWN") or "UNKNOWN"),
        str(kpi.get("stop_id", "") or ""),
        str(kpi.get("direction", "") or ""),
    )

def ensure_network_lines(by_line: dict):
    by_line_completed = dict(by_line)

    for line in ALL_NETWORK_LINES:
        if line not in by_line_completed:
            by_line_completed[line] = {
                "line": line,
                "line_name": line.replace("_", " "),
                "alert_level": "VERT",
                "priority_score": 0.0,
                "suppression_rate": 0.0,
                "delay_mean_min": 0.0,
                "wait_ratio": 1.0,
                "dwell_anomaly_min": 0.0,
                "irregularity_index": 1.0,
                "arrets": [],
                "nb_arrets": 0,
                "stop_name": "—",
                "direction": "",
            }

    return by_line_completed

def update_triplet_cache(new_kpis: list):
    cache = st.session_state.get("triplet_state_cache", {})

    for kpi in new_kpis:
        key = triplet_key_from_kpi(kpi)
        cache[key] = kpi

    st.session_state.triplet_state_cache = cache
    return list(cache.values())


# ─────────────────────────────────────────────
# AGRÉGATION
# ─────────────────────────────────────────────
def aggregate_alerts_by_line_and_terminus(alerts):
    grouped = defaultdict(list)

    for a in alerts:
        line = a.get("line", "—")
        terminus = (
            a.get("direction")
            or a.get("terminus")
            or a.get("branch")
            or "Direction non précisée"
        )
        grouped[(line, terminus)].append(a)

    aggregated = []
    for (line, terminus), rows in grouped.items():
        worst = sorted(
            rows,
            key=lambda x: (
                alert_order(x.get("alert_level", "VERT")),
                -float(x.get("priority_score", 0))
            )
        )[0]

        gares = []
        seen = set()
        for r in rows:
            stop = (r.get("stop_name") or "—").strip()
            if stop not in seen:
                seen.add(stop)
                gares.append(stop)

        first_seen_values = [r.get("first_seen_at") for r in rows if r.get("first_seen_at")]
        first_seen = min(first_seen_values) if first_seen_values else None

        aggregated.append({
            **worst,
            "group_line": line,
            "group_terminus": terminus,
            "group_alerts": rows,
            "group_stops": gares,
            "group_count": len(rows),
            "first_seen_at": first_seen or worst.get("first_seen_at"),
            "avg_priority_score": float(np.mean([r.get("priority_score", 0) for r in rows])),
            "max_delay_mean_min": float(np.max([r.get("delay_mean_min", 0) for r in rows])),
            "avg_delay_mean_min": float(np.mean([r.get("delay_mean_min", 0) for r in rows])),
            "avg_wait_ratio": float(np.mean([r.get("wait_ratio", 1.0) for r in rows])),
            "summary_text": worst.get("summary") or worst.get("explanation") or "—",
        })

    aggregated.sort(
        key=lambda x: (
            alert_order(x.get("alert_level", "VERT")),
            -x.get("avg_priority_score", 0)
        )
    )
    return aggregated


def aggregate_by_line(results):
    grouped = defaultdict(list)
    for r in results:
        grouped[r.get("line", "UNKNOWN")].append(r)

    by_line = {}
    for line, rows in grouped.items():
        worst = max(rows, key=lambda r: r.get("priority_score", 0))
        by_line[line] = {
            **worst,
            "line_name": worst.get("line_name", str(line).replace("_", " ")),
            "suppression_rate":   float(np.mean([r.get("suppression_rate", 0) for r in rows])),
            "delay_mean_min":     float(np.mean([r.get("delay_mean_min", 0) for r in rows])),
            "wait_ratio":         float(np.mean([r.get("wait_ratio", 1) for r in rows])),
            "dwell_anomaly_min":  float(np.mean([r.get("dwell_anomaly_min", 0) for r in rows])),
            "irregularity_index": float(np.mean([r.get("irregularity_index", 1) for r in rows])),
            "arrets": sorted(rows, key=lambda r: r.get("priority_score", 0), reverse=True),
            "nb_arrets": len(rows),
        }

    return ensure_network_lines(by_line)


# ─────────────────────────────────────────────
# KPI MÉTIER DASHBOARD
# ─────────────────────────────────────────────
def compute_dashboard_kpis(results, by_line, active_alerts=None):
    active_alerts = active_alerts or []

    score_perturbation = np.mean([
        r.get("priority_score", 0)
        for r in by_line.values()
        if pd.notna(r.get("priority_score"))
    ]) if by_line else 0.0

    nb_total_lignes = len(ALL_NETWORK_LINES)
    nb_lignes_perturbees = sum(
        1 for line in ALL_NETWORK_LINES
        if by_line.get(line, {}).get("alert_level") in ["ROUGE", "ORANGE"]
    )

    attente_voyageurs = np.mean([
        by_line.get(line, {}).get("wait_ratio", 1.0)
        for line in ALL_NETWORK_LINES
        if pd.notna(by_line.get(line, {}).get("wait_ratio"))
    ]) if by_line else 1.0

    if active_alerts:
        nb_alertes_critiques = sum(
            1 for a in active_alerts
            if a.get("alert_level") in ["ROUGE", "ORANGE"]
        )
    else:
        nb_alertes_critiques = sum(
            1 for line in ALL_NETWORK_LINES
            if by_line.get(line, {}).get("alert_level") in ["ROUGE", "ORANGE"]
        )

    return {
        "score_perturbation": float(score_perturbation),
        "nb_total_lignes": int(nb_total_lignes),
        "nb_lignes_perturbees": int(nb_lignes_perturbees),
        "attente_voyageurs": float(attente_voyageurs),
        "nb_alertes_critiques": int(nb_alertes_critiques),
    }

def score_perturbation_status(score):
    if score < 0.25:
        return "🟢 Normal", "#43A047"
    elif score < 0.50:
        return "🟡 Vigilance", "#FDD835"
    elif score < 0.75:
        return "🟠 Perturbé", "#FB8C00"
    return "🔴 Critique", "#E53935"

def attente_status(wait_ratio):
    if wait_ratio < 1.10:
        return "🟢 Nominal", "#43A047"
    elif wait_ratio < 1.30:
        return "🟡 Dégradé", "#FDD835"
    elif wait_ratio < 1.60:
        return "🟠 Fortement dégradé", "#FB8C00"
    return "🔴 Critique", "#E53935"


# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
def render_header():
    return (
        '<div style="'
        'background:linear-gradient(135deg,#5B0E91 0%, #7B1FA2 55%, #8E24AA 100%);'
        'border-radius:18px;'
        'padding:22px 26px;'
        'margin-bottom:24px;'
        'box-shadow:0 10px 26px rgba(91,14,145,0.28);'
        'display:flex;'
        'align-items:center;'
        'justify-content:space-between;'
        'gap:20px;'
        '">'
            '<div style="display:flex;flex-direction:column;gap:6px;">'
                '<div style="'
                'font-size:28px;'
                'font-weight:800;'
                'color:#FFFFFF;'
                'letter-spacing:-0.02em;'
                'display:flex;'
                'align-items:center;'
                'gap:10px;'
                '">'
                    '<span style="'
                    'display:inline-flex;'
                    'width:42px;'
                    'height:42px;'
                    'border-radius:12px;'
                    'background:rgba(255,255,255,0.15);'
                    'align-items:center;'
                    'justify-content:center;'
                    'font-size:20px;'
                    '">🚆</span>'
                    'SNCF — Vigi Numérique'
                '</div>'
                '<div style="'
                'font-size:13px;'
                'color:rgba(255,255,255,0.86);'
                'font-weight:500;'
                'letter-spacing:.02em;'
                '">'
                    'Centre de supervision • Agent de détection de perturbations'
                '</div>'
            '</div>'
            '<div style="'
            'background:rgba(255,255,255,0.12);'
            'border:1px solid rgba(255,255,255,0.18);'
            'border-radius:12px;'
            'padding:10px 14px;'
            'color:#FFFFFF;'
            'font-size:12px;'
            'font-weight:700;'
            'text-transform:uppercase;'
            'letter-spacing:.06em;'
            'white-space:nowrap;'
            '">'
                'Exploitation temps réel'
            '</div>'
        '</div>'
    )


# ─────────────────────────────────────────────
# CARTE KPI — STYLE SNCF PRO
# ─────────────────────────────────────────────
def render_metric_pro(
    label,
    value,
    subtitle="",
    status_text="",
    accent="#7B1FA2",
    icon="📊",
    trend=None,
    trend_color="#6B7280"
):
    badge_html = ""
    if status_text:
        badge_html = (
            f'<span style="'
            f'display:inline-block;'
            f'background:{accent}15;'
            f'color:{accent};'
            f'border:1px solid {accent}30;'
            f'border-radius:999px;'
            f'padding:4px 10px;'
            f'font-size:11px;'
            f'font-weight:700;'
            f'letter-spacing:.02em;'
            f'margin-bottom:14px;'
            f'">{status_text}</span>'
        )

    subtitle_html = ""
    if subtitle:
        trend_html = ""
        if trend:
            trend_html = (
                f'<span style="color:{trend_color};font-weight:700;margin-right:6px;">'
                f'{trend}'
                f'</span>'
            )
        subtitle_html = (
            f'<div style="display:flex;align-items:center;gap:6px;margin-top:10px;">'
            f'{trend_html}'
            f'<span style="font-size:12px;color:#6B7280;font-weight:500;">{subtitle}</span>'
            f'</div>'
        )

    return (
        f'<div style="'
        f'position:relative;'
        f'background:#FFFFFF;'
        f'border:1px solid #E5E7EB;'
        f'border-radius:18px;'
        f'padding:20px 20px 18px 20px;'
        f'min-height:168px;'
        f'box-shadow:0 6px 18px rgba(15,23,42,0.06);'
        f'overflow:hidden;'
        f'">'
            f'<div style="'
            f'position:absolute;'
            f'top:0;left:0;right:0;'
            f'height:6px;'
            f'background:{accent};'
            f'"></div>'

            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;">'
                f'<div>'
                    f'<div style="'
                    f'font-size:11px;'
                    f'font-weight:800;'
                    f'color:#94A3B8;'
                    f'text-transform:uppercase;'
                    f'letter-spacing:.08em;'
                    f'margin-bottom:10px;'
                    f'">'
                        f'{label}'
                    f'</div>'
                    f'{badge_html}'
                f'</div>'

                f'<div style="'
                f'width:42px;'
                f'height:42px;'
                f'border-radius:12px;'
                f'background:{accent}15;'
                f'border:1px solid {accent}25;'
                f'display:flex;'
                f'align-items:center;'
                f'justify-content:center;'
                f'font-size:20px;'
                f'flex-shrink:0;'
                f'">'
                    f'{icon}'
                f'</div>'
            f'</div>'

            f'<div style="'
            f'font-size:42px;'
            f'line-height:1;'
            f'font-weight:800;'
            f'color:#0F172A;'
            f'letter-spacing:-0.03em;'
            f'margin:0;'
            f'">'
                f'{value}'
            f'</div>'

            f'{subtitle_html}'
        f'</div>'
    )

def perturbation_badge(score):
    if score < 0.25:
        return "Réseau nominal", "#2E7D32"
    elif score < 0.50:
        return "Sous vigilance", "#FB8C00"
    elif score < 0.75:
        return "Perturbation forte", "#EF6C00"
    return "Situation critique", "#E53935"

def attente_badge(wait_ratio):
    if wait_ratio < 1.10:
        return "Attente normale", "#2E7D32"
    elif wait_ratio < 1.30:
        return "Attente dégradée", "#FB8C00"
    elif wait_ratio < 1.60:
        return "Attente élevée", "#EF6C00"
    return "Attente critique", "#E53935"

def lignes_badge(nb_perturbed, total):
    if total == 0 or nb_perturbed == 0:
        return "Aucune ligne impactée", "#2E7D32"
    ratio = nb_perturbed / total
    if ratio < 0.20:
        return "Impact localisé", "#FB8C00"
    elif ratio < 0.50:
        return "Impact réseau modéré", "#EF6C00"
    return "Impact réseau fort", "#E53935"

def alertes_badge(nb_alertes):
    if nb_alertes == 0:
        return "Aucune alerte majeure", "#2E7D32"
    elif nb_alertes <= 2:
        return "Situation sensible", "#FB8C00"
    return "Surveillance renforcée", "#E53935"


# ─────────────────────────────────────────────
# TABLEAU ÉTAT DES LIGNES
# ─────────────────────────────────────────────
def render_line_table(by_line):
    LEVEL_LABEL = {"ROUGE": "Alerte", "ORANGE": "Perturbé", "JAUNE": "Vigilance", "VERT": "Normal"}
    LEVEL_BG    = {"ROUGE": "#E53935", "ORANGE": "#FB8C00", "JAUNE": "#FDD835", "VERT": "#43A047"}
    LEVEL_COL   = {"ROUGE": "#fff",    "ORANGE": "#fff",    "JAUNE": "#111",    "VERT": "#fff"}

    sorted_lines = sorted(
        by_line.items(),
        key=lambda x: (alert_order(x[1].get("alert_level")), x[0])
    )

    def kpi_color(val, warn, crit):
        if val >= crit:
            return "#E53935"
        if val >= warn:
            return "#FB8C00"
        return "#111827"

    def row_html(r, is_arret=False):
        level  = r.get("alert_level", "VERT")
        supp   = r.get("suppression_rate", 0)
        delay  = r.get("delay_mean_min", 0)
        wait   = r.get("wait_ratio", 1.0)
        dwell  = r.get("dwell_anomaly_min", 0.0)

        bg = "#FFF8F8" if level == "ROUGE" else "#FFFAF5" if level == "ORANGE" else "#F9FAFB" if is_arret else "#fff"
        badge_html = _badge(LEVEL_LABEL.get(level, level), LEVEL_BG.get(level, "#9CA3AF"), LEVEL_COL.get(level, "#fff"))
        indent = "padding-left:32px;" if is_arret else ""
        fs = "12px" if is_arret else "14px"
        fw = "500" if is_arret else "600"

        if is_arret:
            name_html = f'<span style="font-size:{fs};color:#374151;">↳ {r.get("stop_name", "—")}</span>'
        else:
            line_key_normalized = str(r.get("line", "UNKNOWN")).upper().replace(" ", "_")
            line_color = LINE_COLORS.get(line_key_normalized, "#6B7280")
            raw_name = r.get("line_name") or ""
            line_label = raw_name if raw_name and raw_name not in ("None", "nan", "none") else str(r.get("line", "UNKNOWN")).replace("_", " ")
            LIGHT_COLORS = {"#FFCC00", "#C8A000", "#6EC4E8", "#F28C00"}
            text_color = "#111" if line_color in LIGHT_COLORS else "#fff"
            name_html = (
                f'<span style="background:{line_color};color:{text_color};'
                f'border-radius:8px;padding:4px 12px;font-size:{fs};'
                f'font-weight:700;letter-spacing:.03em;">{line_label}</span>'
            )

        return (
            f'<div style="display:flex;align-items:center;padding:12px 16px;{indent}'
            f'border:1px solid #EEF2F7;background:{bg};border-radius:10px;margin-bottom:8px;">'
            f'<span style="flex:1.2;">{name_html}</span>'
            f'<span style="flex:0.8;">{badge_html}</span>'
            f'<span style="flex:1;font-size:{fs};font-weight:{fw};color:{kpi_color(supp,0.10,0.25)};">{supp:.1%}</span>'
            f'<span style="flex:1;font-size:{fs};font-weight:{fw};color:{kpi_color(delay,3.0,6.0)};">{delay:.1f} min</span>'
            f'<span style="flex:1;font-size:{fs};font-weight:{fw};color:{kpi_color(wait,1.3,1.8)};">{wait:.2f}x</span>'
            f'<span style="flex:1;font-size:{fs};font-weight:{fw};color:{kpi_color(dwell,1.5,2.5)};">{dwell:.2f}x</span>'
            f'</div>'
        )

    header = (
        '<div style="display:flex;align-items:center;padding:8px 16px 12px 16px;'
        'margin-bottom:10px;border-bottom:1px solid #EEF2F7;">'
        '<span style="flex:1.2;font-size:12px;font-weight:700;color:#6B7280;">Ligne / Arrêt</span>'
        '<span style="flex:0.8;font-size:12px;font-weight:700;color:#6B7280;">État</span>'
        '<span style="flex:1;font-size:12px;font-weight:700;color:#6B7280;">Suppression</span>'
        '<span style="flex:1;font-size:12px;font-weight:700;color:#6B7280;">Retard moyen</span>'
        '<span style="flex:1;font-size:12px;font-weight:700;color:#6B7280;">Ratio attente</span>'
        '<span style="flex:1;font-size:12px;font-weight:700;color:#6B7280;">Ratio retard quai</span>'
        '</div>'
    )

    st.markdown(
        panel_start("ÉTAT DES LIGNES", title_color="#7B1FA2") + header + panel_end(),
        unsafe_allow_html=True
    )

    for line, r in sorted_lines:
        arrets = r.get("arrets", [])
        nb_arrets = r.get("nb_arrets", 0)
        level = r.get("alert_level", "VERT")
        icon = "🔴" if level == "ROUGE" else "🟠" if level == "ORANGE" else "🟡" if level == "JAUNE" else "🟢"

        st.markdown(row_html(r, is_arret=False), unsafe_allow_html=True)

        if arrets:
            with st.expander(f"{icon} {nb_arrets} arrêt(s) — {str(line).replace('_', ' ')}", expanded=False):
                arrets_html = ""
                for arret in arrets:
                    arrets_html += row_html(arret, is_arret=True)
                st.markdown(arrets_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────
# PANNEAU ALERTES
# ─────────────────────────────────────────────
def render_alerts_panel(alerts):
    LIGHT = {"#FFCC00", "#C8A000", "#6EC4E8", "#F28C00"}

    st.markdown("""
    <style>
    .ap-group-header {
        display:flex; align-items:center; gap:8px; padding:10px 14px;
        background:#fff; border:1px solid #E5E7EB; border-radius:10px;
        margin-bottom:4px; flex-wrap:wrap;
    }
    .ap-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; display:inline-block; }
    .ap-badge { font-size:11px; font-weight:700; padding:2px 9px; border-radius:5px; color:#fff; }
    .ap-terminus { font-size:13px; font-weight:600; color:#111827; flex:1; }
    .ap-pill { font-size:11px; color:#6B7280; background:#F3F4F6; border-radius:999px; padding:2px 8px; }
    .ap-ago { font-size:11px; color:#9CA3AF; margin-left:auto; }
    section[data-testid="stExpander"] > details {
        border: 1px solid #E5E7EB !important;
        border-radius: 10px !important;
        background: #fff !important;
    }
    section[data-testid="stExpander"] > details > summary {
        padding: 8px 14px !important;
        font-size: 13px !important;
        font-weight: 600 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        '<p style="font-size:11px;font-weight:700;color:#9CA3AF;text-transform:uppercase;'
        'letter-spacing:.07em;margin-bottom:10px;">Alertes en temps réel</p>',
        unsafe_allow_html=True
    )

    if not alerts:
        st.markdown(
            '<div style="text-align:center;font-size:13px;color:#9CA3AF;padding:20px;'
            'border:1px dashed #E5E7EB;border-radius:10px;">✅ Aucune alerte active</div>',
            unsafe_allow_html=True
        )
        return

    grouped = aggregate_alerts_by_line_and_terminus(alerts)

    for gi, g in enumerate(grouped):
        level = g.get("alert_level", "VERT")
        if level not in ("ROUGE", "ORANGE", "JAUNE"):
            continue

        line        = g.get("group_line", "—")
        terminus    = g.get("group_terminus", "Direction non précisée")
        line_label  = g.get("line_name") or str(line).replace("_", " ")
        nb_gares    = len(g.get("group_stops", []))
        max_delay   = g.get("max_delay_mean_min", 0)

        lc          = LINE_COLORS.get(str(line).upper().replace(" ", "_"), ALERT_COLORS.get(level, "#999"))
        tc          = "#111" if lc in LIGHT else "#fff"
        dot_color   = {"ROUGE": "#E53935", "ORANGE": "#FB8C00", "JAUNE": "#FDD835"}.get(level, "#9CA3AF")

        try:
            fs    = g.get("first_seen_at", "")
            delta = datetime.now() - datetime.fromisoformat(str(fs)[:19])
            mins  = int(delta.total_seconds() / 60)
            ago   = f"Il y a {mins} min" if mins < 60 else f"Il y a {mins//60}h{mins%60:02d}"
        except Exception:
            ago = "Il y a quelques min"

        st.markdown(
            f'<div class="ap-group-header">'
            f'<span class="ap-dot" style="background:{dot_color};"></span>'
            f'<span class="ap-badge" style="background:{lc};color:{tc};">{line_label}</span>'
            f'<span class="ap-terminus">{terminus}</span>'
            f'<span class="ap-pill">{nb_gares} gare{"s" if nb_gares > 1 else ""}</span>'
            f'<span class="ap-pill">max {max_delay:.1f} min</span>'
            f'<span class="ap-ago">{ago}</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        sorted_alerts = sorted(
            g.get("group_alerts", []),
            key=lambda x: -float(x.get("priority_score", 0))
        )

        with st.expander(f"▸ {nb_gares} arrêt(s) concerné(s)", expanded=False):
            for si, r in enumerate(sorted_alerts):
                stop  = r.get("stop_name") or "—"
                prio  = float(r.get("priority_score", 0) or 0)
                delay = float(r.get("delay_mean_min", 0) or 0)
                wait  = float(r.get("wait_ratio", 1.0) or 1.0)
                supp  = float(r.get("suppression_rate", 0) or 0)
                dwell = float(r.get("dwell_anomaly_min", 0) or 0)
                recovery = r.get("estimated_recovery") or "—"

                s_op    = float(r.get("s_operational", 0) or 0)
                s_imp   = float(r.get("s_impact_usager", 0) or 0)
                s_conf  = float(r.get("s_confidence", 0) or 0)
                ts_sc   = float((r.get("ts") or {}).get("score") or r.get("ts_score") or 0)
                ts_angle = (r.get("ts") or {}).get("angle") or r.get("ts_angle") or "—"

                def _c(v, warn=0.4, danger=0.7):
                    return "#E53935" if v >= danger else "#FB8C00" if v >= warn else "#43A047"

                c_prio  = _c(prio)
                c_delay = "#E53935" if delay >= 10 else "#FB8C00" if delay >= 5  else "#111827"
                c_wait  = "#E53935" if wait  >= 2.0 else "#FB8C00" if wait  >= 1.3 else "#111827"
                c_supp  = "#E53935" if supp  >= 0.4 else "#FB8C00" if supp  >= 0.15 else "#111827"
                c_dwell = "#E53935" if dwell >= 5.0 else "#FB8C00" if dwell >= 2.0  else "#111827"
                c_s_op  = _c(s_op)
                c_s_imp = _c(s_imp)
                c_s_conf= _c(s_conf)
                c_ts    = _c(ts_sc)

                def _kpi_block(label, value, color):
                    return (
                        f'<div style="text-align:center;">'
                        f'<div style="font-size:10px;color:#9CA3AF;white-space:nowrap;">{label}</div>'
                        f'<div style="font-size:13px;font-weight:700;color:{color};">{value}</div>'
                        f'</div>'
                    )

                def _score_badge(label, value, color):
                    return (
                        f'<div style="background:#F9FAFB;border:1px solid #E5E7EB;'
                        f'border-radius:7px;padding:5px 10px;text-align:center;">'
                        f'<div style="font-size:10px;color:#9CA3AF;white-space:nowrap;">{label}</div>'
                        f'<div style="font-size:12px;font-weight:700;color:{color};">{value}</div>'
                        f'</div>'
                    )

                st.markdown(
                    f"""
                    <div style="border:1px solid #E5E7EB;border-radius:10px;
                                padding:12px 14px;margin-bottom:8px;background:#fff;">

                      <div style="font-size:13px;font-weight:700;color:#111827;
                                  margin-bottom:10px;">🚉 {stop}</div>

                      <div style="display:flex;gap:16px;flex-wrap:wrap;
                                  padding-bottom:10px;border-bottom:1px solid #F3F4F6;
                                  margin-bottom:10px;">
                        {_kpi_block("Suppression", f"{supp:.1%}", c_supp)}
                        {_kpi_block("Retard", f"{delay:.1f} min", c_delay)}
                        {_kpi_block("Attente", f"{wait:.2f}x", c_wait)}
                        {_kpi_block("Retard quai", f"{dwell:.2f}x", c_dwell)}
                        {_kpi_block("Reprise", recovery, "#111827")}
                        {_kpi_block("Score final", f"{prio:.2f}", c_prio)}
                      </div>

                      <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        {_score_badge("S. Opérationnel",  f"{s_op:.2f}",  c_s_op)}
                        {_score_badge("S. Impact usager", f"{s_imp:.2f}", c_s_imp)}
                        {_score_badge("S. Confiance",     f"{s_conf:.2f}",c_s_conf)}
                        {_score_badge("Score TS",         f"{ts_sc:.2f}", c_ts)}
                        {_score_badge("Angle dominant",   ts_angle,       "#374151")}
                      </div>

                    </div>
                    """,
                    unsafe_allow_html=True
                )

                explain_key = f"llm_expl_{gi}_{si}"
                with st.popover("🧠 Expliquer", use_container_width=False):
                    if st.button("▶ Générer l'analyse", key=f"btn_{gi}_{si}"):
                        llm_kpi = {k: r.get(k) for k in [
                            "timestamp", "line", "line_name", "stop_name", "direction",
                            "suppression_rate", "delay_mean_min", "wait_ratio", "dwell_anomaly_min"
                        ]}
                        llm_ctx = {k: r.get(k) for k in [
                            "s_operational", "s_impact_usager", "s_confidence",
                            "priority_score", "alert_level", "estimated_recovery",
                            "estimated_recovery_min", "ts", "breaker_details"
                        ]}
                        with st.spinner("Génération…"):
                            st.session_state[explain_key] = generate_llm_explanation(llm_kpi, llm_ctx)

                    saved = st.session_state.get(explain_key)
                    if saved:
                        st.markdown(f"**🚉 {stop}**")
                        st.markdown(saved)
                    else:
                        st.caption("Cliquez sur « Générer » pour lancer l'analyse IA.")

                if si < len(sorted_alerts) - 1:
                    st.divider()


# ─────────────────────────────────────────────
# AGENT CONVERSATIONNEL
# ─────────────────────────────────────────────
def render_agent(results, by_line):
    st.markdown(
        '<div style="background:#fff;border:1px solid #E8E8E8;border-radius:12px 12px 0 0;padding:20px 20px 0;">'
        '<p style="font-size:16px;font-weight:700;color:#111827;margin:0 0 16px;">Agent Conversationnel</p>'
        '</div>',
        unsafe_allow_html=True
    )

    if not st.session_state.get("chat_history"):
        nb_lignes  = len(ALL_NETWORK_LINES)
        nb_alertes = sum(1 for line in ALL_NETWORK_LINES if by_line.get(line, {}).get("alert_level") in ["ROUGE", "ORANGE"])
        st.session_state.chat_history = [{
            "role": "assistant",
            "content": (
                f"Bonjour ! Je suis votre assistant Vigi Numérique. "
                f"Je surveille actuellement {nb_lignes} lignes. "
                f"J'ai détecté {nb_alertes} situation(s) nécessitant votre attention."
            ),
            "time": datetime.now().strftime("%H:%M")
        }]

    chat_html = (
        '<div style="background:#F9FAFB;border-radius:10px;padding:16px;'
        'min-height:180px;max-height:320px;overflow-y:auto;margin-bottom:12px;">'
    )
    for msg in st.session_state.chat_history:
        if msg["role"] == "assistant":
            chat_html += (
                f'<div style="display:flex;gap:10px;margin-bottom:14px;">'
                f'<div style="width:34px;height:34px;border-radius:50%;background:#7B1FA2;'
                f'display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:16px;">🤖</div>'
                f'<div style="background:#fff;border:1px solid #E8E8E8;border-radius:10px;padding:12px 14px;max-width:85%;">'
                f'<p style="font-size:13px;color:#111827;margin:0 0 4px;">{msg["content"]}</p>'
                f'<p style="font-size:11px;color:#9CA3AF;margin:0;">{msg.get("time","")}</p>'
                f'</div></div>'
            )
        else:
            chat_html += (
                f'<div style="display:flex;justify-content:flex-end;margin-bottom:14px;">'
                f'<div style="background:#7B1FA2;border-radius:10px;padding:10px 14px;max-width:75%;">'
                f'<p style="font-size:13px;color:#fff;margin:0;">{msg["content"]}</p>'
                f'</div></div>'
            )
    chat_html += '</div>'

    st.markdown(
        f'<div style="background:#fff;border:1px solid #E8E8E8;border-radius:0 0 12px 12px;padding:0 20px 20px;">'
        f'{chat_html}</div>',
        unsafe_allow_html=True
    )

    suggestions = [
        "Quelles sont les alertes critiques ?",
        "Quelle ligne est la plus perturbée ?",
        "Résumé de la situation",
        "Quelles sont les 3 priorités d'action ?",
        "Fais-moi un brief direction en 5 lignes"
    ]
    s_cols = st.columns(len(suggestions))
    for i, sug in enumerate(suggestions):
        with s_cols[i]:
            if st.button(sug, key=f"sug_{i}", use_container_width=True):
                _handle_question(sug, results, by_line)
                st.rerun()

    col_inp, col_btn = st.columns([8, 1])
    with col_inp:
        question = st.text_input(
            "question",
            placeholder="Posez une question à l'agent...",
            label_visibility="collapsed",
            key="agent_input"
        )
    with col_btn:
        send = st.button("➤", use_container_width=True)

    if send and question.strip():
        _handle_question(question.strip(), results, by_line)
        st.rerun()

def _handle_question(question, results, by_line):
    with perf_step("chat_total"):
        st.session_state.chat_history.append({
            "role": "user",
            "content": question,
            "time": datetime.now().strftime("%H:%M")
        })

        with perf_step("chat_load_active_alerts"):
            active_alerts = load_active_alerts()

        with perf_step("chat_build_context"):
            context = build_chat_context(
                results=results,
                by_line=by_line,
                active_alerts=active_alerts,
                last_update=st.session_state.get("last_update")
            )

        # with perf_step("chat_init_llm"):
        #     llm_client = CortexLLMClient(
        #         model="llama3-8b",
        #         temperature=0.2,
        #         max_tokens=500
        #     )

        # with perf_step("chat_answer_question"):
        #     answer = answer_question(question, context, llm_client=llm_client)

        st.session_state.chat_history.append({
            "role": "assistant",
            "content": answer,
            "time": datetime.now().strftime("%H:%M")
        })


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="SNCF Vigi Numérique",
        page_icon="🚊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # _init_perf()  # volontairement commenté
    app_t0 = time.perf_counter()

    auto_refresh_fragment()

    with perf_step("init_session_objects"):
        if "ts_detector" not in st.session_state:
            from app.System_TS import TSDDetectorAgent
            st.session_state.ts_detector = TSDDetectorAgent()

        if (
            "orchestrator" not in st.session_state
            or not hasattr(st.session_state.orchestrator, "set_active_alerts_index")
        ):
            st.session_state.orchestrator = AgentOrchestrateur(
                st.session_state.ts_detector
            )

        for key, val in [
            ("batch_results", []),
            ("last_update", None),
            ("chat_history", []),
            ("triplet_state_cache", {}),
        ]:
            if key not in st.session_state:
                st.session_state[key] = val

        if "is_bootstrap_done" not in st.session_state:
            st.session_state.is_bootstrap_done = False

        if "use_simulation" not in st.session_state:
            st.session_state.use_simulation = False

    top_left, top_mid, top_right = st.columns([6, 2, 1])
    with top_mid:
        use_simulation = st.checkbox("🧪 Mode simulation", key="use_simulation")
    with top_right:
        force = st.button("↺ Actualiser")

    now = datetime.now()
    last_load = st.session_state.get("last_update")
    need_refresh = (
        force
        or not st.session_state.batch_results
        or (last_load is not None and (now - last_load).total_seconds() > 15)
    )

    if need_refresh:
        with perf_step("refresh_total"):
            with st.spinner("Analyse en cours…"):
                last_close = st.session_state.get("last_close_incidents")
                if last_close is None or (now - last_close).total_seconds() > 60:
                    with perf_step("close_old_incidents"):
                        close_old_incidents(timeout_minutes=3)
                    st.session_state.last_close_incidents = now

                with perf_step("load_latest_batch"):
                    if use_simulation:
                        from app.simulated_kpis import generate_mixed_kpis
                        kpis = generate_mixed_kpis(30)
                    else:
                        kpis = load_latest_batch()

                with perf_step("load_active_alerts_index"):
                    active_alerts_index = load_active_alerts_index()
                    st.session_state.orchestrator.set_active_alerts_index(active_alerts_index)

                _perf_add("kpis_count", 0, extra=f"{0 if not kpis else len(kpis)} kpis")

                if not kpis and not st.session_state.triplet_state_cache:
                    st.warning("⚠️ Aucune donnée disponible dans KPI_UNIFIED.")
                    st.stop()

                results_new_batch = []
                if kpis:
                    with perf_step("process_kpis_total", extra=f"{len(kpis)} kpis"):
                        process_t0 = time.perf_counter()
                        checkpoint_every = max(1, len(kpis) // 5)

                        for i, kpi in enumerate(kpis, start=1):
                            item_t0 = time.perf_counter()
                            result = st.session_state.orchestrator.process_kpi(kpi)
                            results_new_batch.append(result)
                            item_dt = time.perf_counter() - item_t0

                            if i == 1 or i % checkpoint_every == 0 or i == len(kpis):
                                _perf_add(
                                    step=f"process_kpi_checkpoint_{i}",
                                    duration_s=item_dt,
                                    extra=f"{i}/{len(kpis)}"
                                )

                        process_total = time.perf_counter() - process_t0
                        avg_ms = (process_total / len(kpis)) * 1000 if kpis else 0
                        _perf_add("process_kpi_avg", 0, extra=f"{avg_ms:.1f} ms/kpi")

                with perf_step("update_triplet_cache"):
                    consolidated_results = update_triplet_cache(results_new_batch)

                with perf_step("store_batch_results"):
                    st.session_state.batch_results = consolidated_results
                    st.session_state.last_update = datetime.now()

    with perf_step("load_active_alerts"):
        active_alerts = load_active_alerts()

    with perf_step("read_cached_results"):
        results = st.session_state.batch_results

    with perf_step("aggregate_by_line"):
        by_line = aggregate_by_line(results)

    with perf_step("compute_dashboard_kpis"):
        dashboard_kpis = compute_dashboard_kpis(results, by_line, active_alerts)

    now_str = st.session_state.last_update.strftime("%H:%M:%S") if st.session_state.last_update else "—"

    score_perturbation = dashboard_kpis["score_perturbation"]
    nb_total_lignes = dashboard_kpis["nb_total_lignes"]
    nb_lignes_perturbees = dashboard_kpis["nb_lignes_perturbees"]
    attente_voyageurs = dashboard_kpis["attente_voyageurs"]
    nb_alertes_critiques = dashboard_kpis["nb_alertes_critiques"]

    with perf_step("render_header"):
        st.markdown(render_header(), unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    with perf_step("compute_kpi_labels"):
        score_text, score_accent = perturbation_badge(score_perturbation)
        lignes_text, lignes_accent = lignes_badge(nb_lignes_perturbees, nb_total_lignes)
        attente_text, attente_accent = attente_badge(attente_voyageurs)
        alertes_text, alertes_accent = alertes_badge(nb_alertes_critiques)

    with perf_step("render_kpi_cards"):
        m1, m2, m3, m4 = st.columns(4)

        with m1:
            st.markdown(
                render_metric_pro(
                    label="Score perturbation",
                    value=f"{score_perturbation:.2f}",
                    subtitle=f"Mis à jour à {now_str}",
                    status_text=score_text,
                    accent=score_accent,
                    icon="🔥",
                    trend="•",
                    trend_color=score_accent
                ),
                unsafe_allow_html=True
            )

        with m2:
            ratio_lignes = (nb_lignes_perturbees / nb_total_lignes * 100) if nb_total_lignes else 0
            st.markdown(
                render_metric_pro(
                    label="Lignes perturbées",
                    value=f"{nb_lignes_perturbees}/{nb_total_lignes}",
                    subtitle=f"{ratio_lignes:.0f}% du réseau impacté",
                    status_text=lignes_text,
                    accent=lignes_accent,
                    icon="🚨",
                    trend="↑" if nb_lignes_perturbees > 0 else "•",
                    trend_color=lignes_accent
                ),
                unsafe_allow_html=True
            )

        with m3:
            variation_attente = (
                "Nominal" if attente_voyageurs < 1.10
                else "Temps d'attente allongé"
            )
            st.markdown(
                render_metric_pro(
                    label="Attente voyageurs",
                    value=f"{attente_voyageurs:.2f}x",
                    subtitle=variation_attente,
                    status_text=attente_text,
                    accent=attente_accent,
                    icon="🚶‍♂️",
                    trend="↑" if attente_voyageurs >= 1.10 else "•",
                    trend_color=attente_accent
                ),
                unsafe_allow_html=True
            )

        with m4:
            st.markdown(
                render_metric_pro(
                    label="Alertes majeures",
                    value=str(nb_alertes_critiques),
                    subtitle="Incidents ORANGE + ROUGE actifs",
                    status_text=alertes_text,
                    accent=alertes_accent,
                    icon="🔴",
                    trend="↑" if nb_alertes_critiques > 0 else "•",
                    trend_color=alertes_accent
                ),
                unsafe_allow_html=True
            )

    with perf_step("render_main_panels"):
        col_table, col_alerts = st.columns([1.6, 1], gap="large")
        with col_table:
            with perf_step("render_line_table"):
                render_line_table(by_line)
        with col_alerts:
            with perf_step("render_alerts_panel"):
                render_alerts_panel(active_alerts)

    with perf_step("render_agent"):
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        render_agent(results, by_line)

    total_app = time.perf_counter() - app_t0
    _perf_add("APP_TOTAL", total_app)

    # render_perf_panel()


if __name__ == "__main__":
    main()