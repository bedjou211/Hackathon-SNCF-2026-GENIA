"""
chat_agent.py — Agent conversationnel hybride règles + LLM
"""
import json
import time
import numpy as np
import pandas as pd
from snowflake.snowpark.context import get_active_session


# ─────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────
def test_cortex():
    client = CortexLLMClient(model="llama3-8b", temperature=0.2, max_tokens=200)
    return client.generate(
        system_prompt="Tu réponds en français.",
        user_prompt="Donne-moi 3 priorités d'action pour un centre de supervision ferroviaire."
    )


# ─────────────────────────────────────────────
# CONTEXT BUILDER
# ─────────────────────────────────────────────
def build_chat_context(results, by_line, active_alerts, last_update=None):
    nb_total = len(by_line)
    levels   = [r.get("alert_level", "VERT") for r in by_line.values()]

    worst_line = max(by_line.items(), key=lambda x: x[1].get("priority_score", 0)) if by_line else None

    return {
        "results":       results,
        "by_line":       by_line,
        "active_alerts": active_alerts,
        "last_update":   last_update.strftime("%H:%M:%S") if last_update else "—",
        "summary": {
            "nb_total_lignes": nb_total,
            "nb_rouge":        levels.count("ROUGE"),
            "nb_orange":       levels.count("ORANGE"),
            "nb_jaune":        levels.count("JAUNE"),
            "nb_vert":         levels.count("VERT"),
        },
        "worst_line": worst_line,
    }


# ─────────────────────────────────────────────
# INTENT DETECTION — source unique de vérité
# ─────────────────────────────────────────────
_INTENT_RULES = [
    ("alerts",          ["alerte", "critique", "urgence", "rouge", "orange", "incident"]),
    ("worst_line",      ["plus perturbée", "plus perturbee", "pire", "worst", "plus dégradée", "plus degradee"]),
    ("traveler_impact", ["attente", "voyageur", "usager", "impact client", "impact usager"]),
    ("global_score",    ["score", "perturbation globale", "score global"]),
    ("priorities",      ["priorité", "priorite", "action", "que faire", "recommandation"]),
    ("brief",           ["brief", "synthèse", "synthese", "résumé direction", "resume direction", "5 lignes"]),
    ("analysis",        ["analyse", "explique", "compare", "pourquoi", "cause"]),
    ("network_summary", ["résumé", "resume", "global", "situation", "réseau"]),
]

def detect_intent(question: str, by_line: dict) -> dict:
    q = question.lower().strip()

    # Détection ligne spécifique
    line_match = next(
        (k for k in by_line if k.lower().replace("_", " ") in q or k.lower() in q),
        None
    )
    if line_match and not any(kw in q for kws in [v for _, v in _INTENT_RULES] for kw in kws):
        return {"intent": "line_status", "line": line_match, "use_llm": False}

    for intent, keywords in _INTENT_RULES:
        if any(kw in q for kw in keywords):
            use_llm = intent in ("priorities", "brief", "analysis")
            return {"intent": intent, "line": line_match, "use_llm": use_llm}

    return {"intent": "fallback", "line": line_match, "use_llm": False}


# ─────────────────────────────────────────────
# RULE-BASED ANSWERS
# ─────────────────────────────────────────────
def answer_with_rules(question: str, context: dict) -> str | None:
    """
    Retourne une réponse si l'intent est couvert par les règles,
    None si l'intent doit être traité par le LLM.
    """
    by_line       = context["by_line"]
    active_alerts = context["active_alerts"]
    summary       = context["summary"]
    last_update   = context["last_update"]

    intent_data = detect_intent(question, by_line)
    intent      = intent_data["intent"]
    line_key    = intent_data["line"]

    if intent_data["use_llm"]:
        return None  # déléguer au LLM

    if intent == "alerts":
        majors = [a for a in active_alerts if a.get("alert_level") in ("ROUGE", "ORANGE")]
        if not majors:
            return "✅ Aucune alerte majeure active pour le moment."
        top       = majors[0]
        line_name = top.get("line_name") or top.get("line", "—").replace("_", " ")
        return (
            f"🚨 {len(majors)} alerte(s) majeure(s) active(s). "
            f"La plus prioritaire : {line_name} à {top.get('stop_name', '—')} "
            f"(niveau {top.get('alert_level', '—')}, "
            f"reprise estimée : {top.get('estimated_recovery', '—')})."
        )

    if intent == "line_status" and line_key:
        r = by_line.get(line_key)
        if not r:
            return f"Ligne {line_key.replace('_', ' ')} non trouvée dans le batch courant."
        return (
            f"📍 {line_key.replace('_', ' ')} — {r.get('alert_level', '—')}. "
            f"Suppression : {r.get('suppression_rate', 0):.1%} · "
            f"Retard : {r.get('delay_mean_min', 0):.1f} min · "
            f"Attente : {r.get('wait_ratio', 1.0):.2f}x · "
            f"Score : {r.get('priority_score', 0):.2f}."
        )

    if intent == "network_summary":
        return (
            f"📋 Réseau à {last_update} — "
            f"{summary['nb_total_lignes']} lignes : "
            f"🔴 {summary['nb_rouge']} · "
            f"🟠 {summary['nb_orange']} · "
            f"🟡 {summary['nb_jaune']} · "
            f"🟢 {summary['nb_vert']}."
        )

    if intent == "worst_line":
        worst = context["worst_line"]
        if not worst:
            return "Pas assez de données pour identifier la ligne la plus perturbée."
        k, r = worst
        return (
            f"📉 Ligne la plus perturbée : {k.replace('_', ' ')} "
            f"(score {r.get('priority_score', 0):.2f} · niveau {r.get('alert_level', '—')})."
        )

    if intent == "traveler_impact":
        ratios  = [r.get("wait_ratio", 1.0) for r in by_line.values() if pd.notna(r.get("wait_ratio"))]
        avg     = float(np.mean(ratios)) if ratios else 1.0
        status  = "nominal" if avg < 1.10 else "dégradé" if avg < 1.30 else "fortement dégradé"
        return f"🚶 Attente moyenne : {avg:.2f}x ({status})."

    if intent == "global_score":
        scores = [r.get("priority_score", 0) for r in by_line.values() if pd.notna(r.get("priority_score"))]
        avg    = float(np.mean(scores)) if scores else 0.0
        return f"🔥 Score global de perturbation : {avg:.2f}."

    # fallback : réponse générique utile
    return (
        f"📋 Réseau à {last_update} — "
        f"{summary['nb_total_lignes']} lignes surveillées, "
        f"🔴 {summary['nb_rouge']} alertes, "
        f"🟠 {summary['nb_orange']} perturbées. "
        f"Posez une question sur une ligne spécifique, les alertes, ou demandez un brief."
    )


# ─────────────────────────────────────────────
# LLM CONTEXT BUILDER
# ─────────────────────────────────────────────
def build_llm_context(context: dict, max_lines: int = 6, max_alerts: int = 5) -> dict:
    by_line       = context["by_line"]
    active_alerts = context["active_alerts"]

    top_lines = sorted(by_line.items(), key=lambda x: x[1].get("priority_score", 0), reverse=True)[:max_lines]
    top_alerts = sorted(active_alerts, key=lambda x: x.get("priority_score", 0), reverse=True)[:max_alerts]

    return {
        "last_update": context["last_update"],
        "summary":     context["summary"],
        "top_lines": [
            {
                "line":             line,
                "line_name":        r.get("line_name", line.replace("_", " ")),
                "alert_level":      r.get("alert_level", "—"),
                "priority_score":   round(float(r.get("priority_score", 0)), 3),
                "suppression_rate": round(float(r.get("suppression_rate", 0)), 3),
                "delay_mean_min":   round(float(r.get("delay_mean_min", 0)), 2),
                "wait_ratio":       round(float(r.get("wait_ratio", 1.0)), 2),
                # explanation volontairement omis — trop long pour le prompt
            }
            for line, r in top_lines
        ],
        "top_alerts": [
            {
                "line":               a.get("line", ""),
                "line_name":          a.get("line_name", ""),
                "stop_name":          a.get("stop_name", ""),
                "alert_level":        a.get("alert_level", ""),
                "priority_score":     round(float(a.get("priority_score", 0)), 3),
                "estimated_recovery": a.get("estimated_recovery", "—"),
            }
            for a in top_alerts
        ],
    }


# ─────────────────────────────────────────────
# PROMPT BUILDER
# ─────────────────────────────────────────────
_SYSTEM_BASE = (
    "Tu es un assistant de supervision SNCF. "
    "Tu réponds uniquement à partir du contexte JSON fourni. "
    "Tu n'inventes jamais de ligne, de station, de métrique ou de cause. "
    "Si une cause est incertaine, tu l'indiques explicitement. "
    "Tu réponds en français, de façon concise et opérationnelle."
)

_INTENT_INSTRUCTIONS = {
    "priorities": (
        "Propose exactement 3 actions prioritaires, concrètes et opérationnelles, "
        "directement déduites du contexte. Format : liste numérotée."
    ),
    "brief": (
        "Rédige un brief de 5 lignes maximum pour la direction, "
        "synthétisant l'état du réseau et les points d'attention principaux."
    ),
    "analysis": (
        "Analyse la situation en t'appuyant sur les métriques disponibles. "
        "Si une cause n'est pas certaine, indique-le clairement."
    ),
}

def build_llm_prompt(question: str, llm_context: dict, intent: str) -> tuple[str, str]:
    extra = _INTENT_INSTRUCTIONS.get(intent, "")
    system_prompt = _SYSTEM_BASE + (f" {extra}" if extra else "")
    user_prompt   = (
        f"Question : {question}\n\n"
        f"Contexte : {json.dumps(llm_context, ensure_ascii=False)}\n\n"
        "Réponds de façon utile pour un superviseur réseau."
    )
    return system_prompt, user_prompt


# ─────────────────────────────────────────────
# CORTEX LLM CLIENT
# ─────────────────────────────────────────────
def _escape_sql(text: str) -> str:
    return str(text or "").replace("$$", "$ $")

class CortexLLMClient:
    def __init__(self, model: str = "llama3-8b", temperature: float = 0.2, max_tokens: int = 500):
        self.model       = model
        self.temperature = temperature
        self.max_tokens  = max_tokens

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        session = get_active_session()

        # Séparation system/user dans le prompt pour meilleure qualité
        full_prompt = _escape_sql(
            f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}"
        )

        result = session.sql(f"""
            SELECT AI_COMPLETE(
                '{self.model}',
                $$ {full_prompt} $$,
                OBJECT_CONSTRUCT(
                    'temperature', {self.temperature},
                    'max_tokens',  {self.max_tokens}
                )
            ) AS response
        """).collect()

        raw = (result[0][0] if result else None) or ""
        return str(raw).strip()


# ─────────────────────────────────────────────
# POST-PROCESSING
# ─────────────────────────────────────────────
def postprocess_llm_answer(text: str, max_chars: int = 1200) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0] + "…"
    return cleaned


# ─────────────────────────────────────────────
# ENTRYPOINT PRINCIPAL
# ─────────────────────────────────────────────
def answer_question(question: str, context: dict, llm_client: CortexLLMClient | None = None) -> str:
    print(f"[chat_agent] question={question!r}")

    intent_data = detect_intent(question, context["by_line"])
    intent      = intent_data["intent"]
    use_llm     = intent_data["use_llm"]

    print(f"[chat_agent] intent={intent} use_llm={use_llm}")

    # Toujours calculer la réponse règles (fallback garanti)
    rule_answer = answer_with_rules(question, context)

    if not use_llm or llm_client is None:
        print("[chat_agent] routed → rules")
        return rule_answer or ""

    try:
        t0          = time.time()
        llm_ctx     = build_llm_context(context)
        sys_p, usr_p = build_llm_prompt(question, llm_ctx, intent)

        print(f"[chat_agent] calling cortex model={llm_client.model}…")
        raw         = llm_client.generate(sys_p, usr_p)
        answer      = postprocess_llm_answer(raw)
        dt          = time.time() - t0

        print(f"[chat_agent] llm latency={dt:.2f}s answer_len={len(answer)}")

        return answer if answer else (rule_answer or "")

    except Exception as e:
        print(f"[chat_agent] llm error={e!r} → fallback rules")
        return rule_answer or ""