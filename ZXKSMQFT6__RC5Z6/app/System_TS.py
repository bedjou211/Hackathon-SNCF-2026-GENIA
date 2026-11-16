"""
Fichier System_TS.py
Agent Détecteur TS — Détecte les anomalies sur les KPIs en temps réel, en combinant 5 angles d’analyse complémentaires :
1. Z-score multi-échelle : court terme (30 min), hier même créneau, semaine même créneau, même jour de semaine
2. Slope (pente de dégradation) sur une fenetre de 15 min
3. Corrélation : signature propre dans l'espace multi-KPI 
4. Persistance : combien de snapshots consécutifs sont anormaux
5. Propagation spatiale : est-ce que les autres arrêts de la même ligne sont aussi en anomalie ?    
"""
import argparse, csv, math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

def _mean(s):
    lst = s if isinstance(s, list) else list(s)
    return sum(lst) / len(lst) if lst else 0.0

def _stdev(s):
    lst = s if isinstance(s, list) else list(s)
    n = len(lst)
    if n < 2: return 0.0
    mu = sum(lst) / n
    return (sum((x - mu)**2 for x in lst) / (n - 1)) ** 0.5

def _zscore(val, series):
    lst = series if isinstance(series, list) else list(series)
    if len(lst) < 3: return 0.0
    mu = sum(lst) / len(lst)
    sg = _stdev(lst)
    return (val - mu) / sg if sg > 1e-9 else 0.0

def _rolling_mean(series, window):
    result = []
    for i in range(len(series)):
        s = series[max(0, i-window+1):i+1]
        result.append(sum(s)/len(s))
    return result

def _cosine_distance(v1, v2):
    keys = list(v1.keys())
    dot = sum(v1[k]*v2[k] for k in keys)
    n1 = math.sqrt(sum(v1[k]**2 for k in keys))
    n2 = math.sqrt(sum(v2[k]**2 for k in keys))
    if n1 < 1e-9 or n2 < 1e-9: return 0.0
    return 1.0 - max(-1.0, min(1.0, dot/(n1*n2)))

@dataclass
class KPISnapshot:
    timestamp: datetime
    line: str
    stop_id: str
    direction: str
    suppression_rate: float
    delay_mean: float
    wait_ratio: float
    headway_ratio: float
    dwell_anomaly: float = 0.0
    
    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            d = dict(d)
        d = {k.lower(): v for k, v in d.items()}
        return cls(
            timestamp=datetime.fromisoformat(d.get("timestamp", "")),
            line=d.get("line", "").strip(),
            stop_id=d.get("stop_id", ""),
            direction=d.get("direction", "").strip(),
            suppression_rate=float(d.get("suppression_rate", 0)),
            delay_mean=float(d.get("delay_mean", 0)),
            wait_ratio=float(d.get("wait_ratio", 1.0)),
            headway_ratio=float(d.get("headway_ratio", 1.0)),
            dwell_anomaly=float(d.get("dwell_anomaly", 0.0)),
        )
@dataclass
class AnomalyVector:
    z_score_short: float = 0.0; z_score_daily: float = 0.0
    z_score_weekly: float = 0.0; z_score_dow: float = 0.0
    slope_score: float = 0.0
    correlation_score: float = 0.0; persistence_score: float = 0.0
    propagation_score: float = 0.0; final_score: float = 0.0
    level: str = "VERT"; dominant_angle: str = ""
    consecutive_count: int = 0; propagation_available: bool = True
    explanation_tokens: dict = field(default_factory=dict) ; z_supp_court: float = 0.0; z_delay_court: float = 0.0; z_wait_court: float = 0.0
    z_supp_daily: float = 0.0; z_delay_daily: float = 0.0; z_wait_daily: float = 0.0
    z_weekly_supp: float = 0.0; z_weekly_delay: float = 0.0; z_weekly_wait: float = 0.0

# ── Normalisation Welford O(1)
class AngleNormalizer:
    """Welford running mean/variance — O(1) par appel, auto-calibré."""
    MIN_HISTORY = 30
    SIGMA_CLIP  = 3.0
    FALLBACK = {"z_supp":3.0,"z_delay":4.5,"z_wait":4.5,"slope":0.30,
                "correlation":0.45,"persistence":8.0,"propagation":0.6,
                "residual":3.0,"volatility":3.0}

    def __init__(self):
        self._n  = defaultdict(int)
        self._mu = defaultdict(float)
        self._M2 = defaultdict(float)

    def normalize(self, key, raw):
        v = abs(raw)
        n = self._n[key]
        if n < self.MIN_HISTORY:
            score = min(1.0, max(0.0, v / self.FALLBACK.get(key, 4.0)))
        else:
            sigma = (self._M2[key]/(n-1))**0.5 if n > 1 else 1e-9
            if sigma < 1e-9:
                score = 0.0
            else:
                z = max(0.0, (v - self._mu[key]) / sigma)
                score = min(1.0, z / self.SIGMA_CLIP)
        # Mise à jour Welford après calcul
        n += 1; self._n[key] = n
        delta = v - self._mu[key]
        self._mu[key] += delta / n
        self._M2[key] += delta * (v - self._mu[key])
        return score

# ── Mémoire TS (optimisée — slot_history utilise cache)
class TSMemory:
    MAX_HISTORY = 2000

    def __init__(self):
        self.history    = defaultdict(lambda: deque(maxlen=self.MAX_HISTORY))
        self.consecutive = defaultdict(int)

    def push(self, snap):
        self.history[(snap.line, snap.stop_id, snap.direction)].append(snap)

    def get_history(self, snap):
        return list(self.history[(snap.line, snap.stop_id, snap.direction)])

    def get_window(self, snap, minutes):
        cutoff = snap.timestamp - timedelta(minutes=minutes)
        return [s for s in self.history[(snap.line, snap.stop_id, snap.direction)]
                if s.timestamp >= cutoff]

    def get_same_slot_history(self, snap, days_back=7, tolerance_min=20):
        snap_min = snap.timestamp.hour * 60 + snap.timestamp.minute
        snap_date = snap.timestamp.date()
        result = []
        for s in self.history[(snap.line, snap.stop_id, snap.direction)]:
            dd = (snap_date - s.timestamp.date()).days
            if dd < 1 or dd > days_back:
                continue
            if abs(snap_min - (s.timestamp.hour*60 + s.timestamp.minute)) <= tolerance_min:
                result.append(s)
        return result
    
    def get_same_dow_history(self, snap: KPISnapshot,
                          weeks_back: int = 1,
                          tol: int = 20) -> list:

        snap_dow  = snap.timestamp.weekday()
        snap_min  = snap.timestamp.hour * 60 + snap.timestamp.minute
        snap_date = snap.timestamp.date()
        result = []

        for s in self.history[(snap.line, snap.stop_id, snap.direction)]:

            if s.timestamp.weekday() != snap_dow:
                continue

            dd = (snap_date - s.timestamp.date()).days
            if dd < 1:
                continue

            if dd > weeks_back * 7:
                continue

            s_min = s.timestamp.hour * 60 + s.timestamp.minute
            if abs(snap_min - s_min) <= tol:
                result.append(s)

        return result

    def incr_consecutive(self, k): self.consecutive[k] += 1
    def reset_consecutive(self, k): self.consecutive[k] = 0
    def get_consecutive(self, k):   return self.consecutive[k]

# ── Poids KPI
KPI_WEIGHTS = {"suppression_rate":0.40, "delay_mean":0.35, "wait_ratio":0.25}

def _composite(s):
    w = KPI_WEIGHTS
    return (w["suppression_rate"]*s.suppression_rate
          + w["delay_mean"]*min(s.delay_mean/10.0,1.0)
          + w["wait_ratio"]*min(s.wait_ratio/3.0,1.0))

# ── 5 angles solides (valeurs BRUTES)

"""
🔵 Angle 1 — Z-score multi-échelle
Ce qu'il détecte : est-ce que les KPIs actuels sont anormaux par rapport à 4 fenêtres temporelles différentes ?
4 fenêtres temporelles : court terme (30 min), hier même créneau, semaine même créneau, même jour de semaine 
"""
def angle1_zscore_raw(snap, mem):
    short  = mem.get_window(snap, 30)
    daily  = mem.get_same_slot_history(snap, days_back=2,  tolerance_min=10)
    weekly = mem.get_same_slot_history(snap, days_back=7, tolerance_min=10)
    same_dow = mem.get_same_dow_history(snap, weeks_back=1, tol=10)

    def z4(kpi, val):
        zs = _zscore(val, [getattr(s,kpi) for s in short])
        zd = _zscore(val, [getattr(s,kpi) for s in daily])  if daily  else 0.0
        zw = _zscore(val, [getattr(s,kpi) for s in weekly]) if weekly else 0.0
        zsd = _zscore(val, [getattr(s,kpi) for s in same_dow]) if same_dow else 0.0
        return zs, zd, zw, zsd
    zs_su,zd_su,zw_su,zsd_su = z4("suppression_rate", snap.suppression_rate)
    zs_dl,zd_dl,zw_dl,zsd_dl = z4("delay_mean",       snap.delay_mean)
    zs_wt,zd_wt,zw_wt,zsd_wt  = z4("wait_ratio",       snap.wait_ratio)
    kpi_dom = max([("suppression",abs(zs_su)),("delay",abs(zs_dl)),("wait",abs(zs_wt))],
                  key=lambda x:x[1])[0]
    return {"zs_su":zs_su,"zs_dl":zs_dl,"zs_wt":zs_wt,
            "zd_su":zd_su,"zd_dl":zd_dl,"zd_wt":zd_wt,
            "zw_su":zw_su,"zw_dl":zw_dl,"zw_wt":zw_wt,
            "zsd_su":zsd_su,"zsd_dl":zsd_dl,"zsd_wt":zsd_wt,
            "kpi_dominant":kpi_dom}

"""
🟠 Angle 2 — Slope (pente de dégradation) sur une fenetre de 15 min 
Ce qu'il détecte : est-ce que le retard empire en ce moment, et à quelle vitesse ?
La pente = l'inclinaison de la droite qui passe au mieux à travers ces points. Plus elle monte vite, plus c'est alarmant.
pente = Σ(xi - x̄)(yi - ȳ) / Σ(xi - x̄)² <= formule mathématique
"""

def angle2_slope_raw(snap, mem):
    """
    Pente du delay_mean sur 15 min (régression linéaire).
    Plage : slope en min/snap, *2 → clip=4.0 → score dans [0,1].
    Cohérent : slope+2 min/snap = score 1.0 (dégradation très rapide).
    """
    recent = mem.get_window(snap, 15)
    if len(recent) < 3: return 0.0
    delays = [s.delay_mean for s in recent]
    n = len(delays); xs = list(range(n))
    xm = sum(xs)/n; ym = sum(delays)/n
    num = sum((xs[i]-xm)*(delays[i]-ym) for i in range(n))
    den = sum((xs[i]-xm)**2 for i in range(n))
    return num/den if den > 1e-9 else 0.0

"""
🟢 Angle 3 — Corrélation.
Chaque type de perturbation a une signature propre dans l'espace multi-KPI 
"""
def angle3_correlation_raw(snap, mem):
    history = mem.get_window(snap, 60)
    if len(history) < 5: return 0.0
    kpi_keys = ["suppression_rate","delay_mean","wait_ratio","headway_ratio"]
    centroid = {k: _mean([getattr(s,k) for s in history[:-1]]) for k in kpi_keys}
    current  = {k: getattr(snap,k) for k in kpi_keys}
    return _cosine_distance(centroid, current)

"""
🟢 Angle 4 — Persistance. sur 30 dernières minutes et le threshold_z à définir 
Ce qu'il détecte : combien de snapshots consécutifs sont anormaux ? 
(filtre les fausses alarmes ponctuelles)
"""

def angle4_persistence_raw(snap, mem, threshold_z=1.5):
    key = (snap.line, snap.stop_id, snap.direction)
    recent = mem.get_window(snap, 30)

    comp_series = [_composite(s) for s in recent]
    z = abs(_zscore(_composite(snap), comp_series)) if len(comp_series) >= 3 else 0.0

    if z > threshold_z:
        mem.incr_consecutive(key)
    else:
        mem.reset_consecutive(key)

    return mem.get_consecutive(key)

"""
🔴 Angle 5 — Propagation spatiale. 
Ce qu'il détecte : est-ce que les autres arrêts de la même ligne sont aussi en anomalie ?
"""
def angle5_propagation_raw(snap, mem, all_memories, z_threshold=1.5):
    anomalous = 0
    total = 0

    for (line, stop_id, direction), other_mem in all_memories.items():

        if line != snap.line or direction != snap.direction or stop_id == snap.stop_id:
            continue

        total += 1

        dummy = KPISnapshot(
            snap.timestamp, line, stop_id, direction,
            0, 0, 1.0, 1.0
        )

        other_hist = other_mem.get_window(dummy, 30)

        if len(other_hist) < 5:
            continue

        last = other_hist[-1]

        delay_series = [s.delay_mean for s in other_hist]
        supp_series  = [s.suppression_rate for s in other_hist]

        z_delay = _zscore(last.delay_mean, delay_series)
        z_supp  = _zscore(last.suppression_rate, supp_series)

        if abs(z_supp) > z_threshold or abs(z_delay) > z_threshold:
            anomalous += 1

    return (anomalous / total, total) if total > 0 else (0.0, 0)

# ── Agrégation
WEIGHTS = {
    "z_short":0.18,
    "z_daily":0.10,
    "z_weekly":0.08,
    "z_dow":0.06,
    "slope":0.18,
    "correlation":0.14,
    "persistence":0.20,
    "propagation":0.06
}

def _compute_final_score(vec):
    w = dict(WEIGHTS)
    if not vec.propagation_available:
        b = w["propagation"]/2
        w["z_short"] += b; w["persistence"] += b; w["propagation"] = 0.0
    score = (
            w["z_short"]*vec.z_score_short +
            w["z_daily"]*vec.z_score_daily +
            w["z_weekly"]*vec.z_score_weekly +
            w["z_dow"]*vec.z_score_dow +
            w["slope"]*vec.slope_score +
            w["correlation"]*vec.correlation_score +
            w["persistence"]*vec.persistence_score +
            w["propagation"]*vec.propagation_score
        )
    level = "ROUGE" if score>0.80 else "ORANGE" if score>0.60 else "JAUNE" if score>0.35 else "VERT"
    return round(score,4), level

def _find_dominant(vec):
    w = dict(WEIGHTS)
    if not vec.propagation_available:
        b = w["propagation"]/2; w["z_short"]+=b; w["persistence"]+=b; w["propagation"]=0.0
    c = {"A1_z_court":w["z_short"]*vec.z_score_short,"A1_z_hier":w["z_daily"]*vec.z_score_daily,
         "A1_z_semaine":w["z_weekly"]*vec.z_score_weekly,"A1_z_dow":w["z_dow"]*vec.z_score_dow,
         "A2_slope":w["slope"]*vec.slope_score,
         "A3_correlation":w["correlation"]*vec.correlation_score,
         "A4_persistence":w["persistence"]*vec.persistence_score,
         "A5_propagation":w["propagation"]*vec.propagation_score}
    return max(c, key=c.get)

# ── Agent principal
class TSDDetectorAgent:
    def __init__(self):
        self._memories   = defaultdict(TSMemory)
        self._normalizer = AngleNormalizer()

    def process(self, row):
        snap = KPISnapshot.from_dict(row)
        key = (snap.line, snap.stop_id, snap.direction)
        mem  = self._memories[key]
        norm = self._normalizer
        # 🔥 COLD START FIX
        history = mem.get_history(snap)
        print("history size:", len(mem.get_history(snap)))#CBE
         # ── AJOUT ICI ──────────────────────────────────────────
        short    = mem.get_window(snap, 30)
        daily    = mem.get_same_slot_history(snap, days_back=2, tolerance_min=10)
        weekly   = mem.get_same_slot_history(snap, days_back=7, tolerance_min=10)
        same_dow = mem.get_same_dow_history(snap, weeks_back=1, tol=10)
    
        
        a1 = angle1_zscore_raw(snap, mem)
        sl = angle2_slope_raw(snap, mem)
        co = angle3_correlation_raw(snap, mem)
        pe = angle4_persistence_raw(snap, mem)
        pr, nb = angle5_propagation_raw(snap, mem, self._memories)
        

        mem.push(snap)

        w = KPI_WEIGHTS
        z_supp_court = norm.normalize("z_supp", a1["zs_su"])
        z_delay_court = norm.normalize("z_delay", a1["zs_dl"])
        z_wait_court = norm.normalize("z_wait", a1["zs_wt"])
        
        z_supp_daily = norm.normalize("z_supp", a1["zd_su"])
        z_delay_daily = norm.normalize("z_delay", a1["zd_dl"])
        z_wait_daily = norm.normalize("z_wait", a1["zd_wt"])

        z_weekly_supp = norm.normalize("z_supp", a1["zw_su"])
        z_weekly_delay = norm.normalize("z_delay", a1["zw_dl"])
        z_weekly_wait = norm.normalize("z_wait", a1["zw_wt"])

        z_same_dow_supp = norm.normalize("z_supp", a1["zsd_su"])
        z_same_dow_delay = norm.normalize("z_delay", a1["zsd_dl"])
        z_same_dow_wait = norm.normalize("z_wait", a1["zsd_wt"])
        
        z_short  = (w["suppression_rate"]*z_supp_court) + (w["delay_mean"] *z_delay_court)+ (w["wait_ratio"]*z_wait_court)
        z_daily  = (w["suppression_rate"]*z_supp_daily)+ (w["delay_mean"]*z_delay_daily)+ (w["wait_ratio"]*z_wait_daily)
        z_weekly = (w["suppression_rate"]*z_weekly_supp)  + (w["delay_mean"]*z_weekly_delay)+ (w["wait_ratio"]*z_weekly_wait)
        z_same_dow = (w["suppression_rate"]*z_same_dow_supp)  + (w["delay_mean"]*z_same_dow_delay)+ (w["wait_ratio"]*z_same_dow_wait)

        vec = AnomalyVector(
            z_score_short=z_short, 
            z_score_daily=z_daily, 
            z_score_weekly=z_weekly,
            z_score_dow=z_same_dow,
            slope_score=norm.normalize("slope",sl),
            correlation_score=norm.normalize("correlation",co),
            persistence_score=norm.normalize("persistence",float(pe)),
            propagation_score=norm.normalize("propagation",pr),
            
            consecutive_count=mem.get_consecutive(key),
            propagation_available=(nb > 0),
            z_supp_court = z_supp_court, z_delay_court = z_delay_court, z_wait_court = z_wait_court,
            z_supp_daily = z_supp_daily, z_delay_daily = z_delay_daily, z_wait_daily = z_wait_daily,
            z_weekly_supp = z_weekly_supp, z_weekly_delay = z_weekly_delay, z_weekly_wait = z_weekly_wait
        )
        vec.final_score, vec.level = _compute_final_score(vec)
        vec.dominant_angle = _find_dominant(vec)
        vec.explanation_tokens = {
            "line":snap.line,"stop": snap.stop_id,"direction":snap.direction,
            "timestamp":snap.timestamp.isoformat(),
            "z_suppression_court":round(a1["zs_su"],2),
            "z_delay_court":round(a1["zs_dl"],2),
            "z_wait_court":round(a1["zs_wt"],2),
            "kpi_dominant_court":a1["kpi_dominant"],
            "z_contextuel":round(z_short,3),
            "z_vs_hier":round(z_daily,3),
            "z_vs_semaine":round(z_weekly,3),
            "z_vs_dow":round(z_same_dow,3),
            "vitesse_degradation":round(norm._mu.get("slope",0),3),
            "duree_anomalie_snaps":pe,"propagation_disponible":(nb>0),
            "angle_dominant":vec.dominant_angle,"score_final":vec.final_score,
            "niveau":vec.level,
            "mem_lengtgh"  : len(mem.get_history(snap))#CBE
        }
        return vec
