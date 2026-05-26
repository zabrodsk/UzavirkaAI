from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


RISK_CLASS_DELAYS = {
    "LOW": 2,
    "MEDIUM": 5,
    "HIGH": 10,
    "CRITICAL": 20,
}

CLOSURE_MULTIPLIERS = {
    "partial_lane_closure": 0.9,
    "detour": 1.08,
    "full_road_closure": 1.18,
}


@dataclass
class RiskReason:
    name: str
    contribution: float
    explanation: str


@dataclass
class RiskResult:
    score: int
    risk_class: str
    recommendation: str
    reasons: list[RiskReason]
    baseline_class: str
    confidence: float
    roi: dict[str, float]


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def safe_float(row: pd.Series | dict[str, Any], key: str, default: float) -> float:
    try:
        value = row.get(key, default)
    except AttributeError:
        value = default
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_risk(score: float) -> str:
    score = clamp(score)
    if score <= 30:
        return "LOW"
    if score <= 60:
        return "MEDIUM"
    if score <= 80:
        return "HIGH"
    return "CRITICAL"


def recommendation_for_class(risk_class: str) -> str:
    return {
        "LOW": "Schválit.",
        "MEDIUM": "Schválit s opatřeními.",
        "HIGH": "Přesunout termín nebo vyžadovat silná opatření.",
        "CRITICAL": "Neschvalovat bez zásadních změn.",
    }[risk_class]


def baseline_class(hour: int | float) -> str:
    hour = int(hour)
    if hour in {7, 8, 9, 15, 16, 17, 18}:
        return "HIGH"
    if hour in {6, 10, 14, 19}:
        return "MEDIUM"
    return "LOW"


def _peak_hour_risk(hour: float) -> float:
    hour = int(hour)
    if hour in {7, 8, 9, 15, 16, 17, 18}:
        return 100
    if hour in {6, 10, 14, 19}:
        return 55
    return 15


def _components(row: pd.Series | dict[str, Any], duration_hours: float) -> dict[str, tuple[float, float, str]]:
    vehicles = safe_float(row, "pocet_vozidel_h", 180)
    flow = safe_float(row, "index_plynulosti_0_100", 65)
    speed = safe_float(row, "prum_rychlost_kmh", 35)
    free_speed = safe_float(row, "volna_rychlost_kmh", 50)
    collision = safe_float(row, "riziko_kolize_0_100", 45)
    services = safe_float(row, "spoju_den", 80)
    pr_capacity = safe_float(row, "kapacita_pr", 150)
    hour = safe_float(row, "hodina", 12)

    speed_ratio = speed / free_speed if free_speed > 0 else speed / 50

    return {
        "Intenzita dopravy": (
            clamp(vehicles / 600 * 100),
            0.20,
            f"{vehicles:.0f} vozidel za hodinu na vybraném úseku nebo v městském vzoru.",
        ),
        "Plynulost": (
            clamp(100 - flow),
            0.18,
            f"Index plynulosti je {flow:.1f}/100; nižší plynulost zvyšuje citlivost na uzavírku.",
        ),
        "Rychlost": (
            clamp((1 - speed_ratio) * 100),
            0.10,
            f"Průměrná rychlost je {speed:.1f} km/h oproti volné rychlosti {free_speed:.1f} km/h.",
        ),
        "Dopravní špička": (
            _peak_hour_risk(hour),
            0.12,
            f"Plánovaný začátek je v {int(hour):02d}:00.",
        ),
        "Bezpečnost": (
            clamp(collision),
            0.15,
            f"Index kolizního rizika je {collision:.1f}/100.",
        ),
        "Alternativa veřejnou dopravou": (
            clamp(100 - services / 120 * 100),
            0.08,
            f"{services:.0f} spojů veřejné dopravy denně; méně alternativ zvyšuje skóre.",
        ),
        "Alternativa P+R": (
            clamp(100 - pr_capacity / 300 * 100),
            0.05,
            f"Kapacita P+R je {pr_capacity:.0f}; slabší parkovací alternativa zvyšuje skóre.",
        ),
        "Délka uzavírky": (
            clamp(duration_hours / 8 * 100),
            0.12,
            f"Délka uzavírky je {duration_hours:.1f} hod.",
        ),
    }


def estimate_roi(row: pd.Series | dict[str, Any], duration_hours: float, risk_class: str, value_of_time_czk_h: float = 200) -> dict[str, float]:
    vehicles = safe_float(row, "pocet_vozidel_h", 180)
    affected_people = vehicles * duration_hours * 1.2
    delay_minutes = RISK_CLASS_DELAYS[risk_class]
    social_loss = affected_people * (delay_minutes / 60) * value_of_time_czk_h
    return {
        "affected_people": round(affected_people, 0),
        "delay_minutes": delay_minutes,
        "value_of_time_czk_h": value_of_time_czk_h,
        "estimated_social_loss_czk": round(social_loss, 0),
        "possible_savings_30pct_czk": round(social_loss * 0.30, 0),
    }


def score_closure(
    row: pd.Series | dict[str, Any],
    duration_hours: float,
    closure_type: str,
    affects_bus_route: bool,
    value_of_time_czk_h: float = 200,
) -> RiskResult:
    duration_hours = max(0.5, float(duration_hours))
    multiplier = CLOSURE_MULTIPLIERS.get(closure_type, 1.0)
    components = _components(row, duration_hours)

    reason_rows: list[RiskReason] = []
    base_score = 0.0
    for name, (risk, weight, explanation) in components.items():
        contribution = risk * weight
        base_score += contribution
        reason_rows.append(RiskReason(name, round(contribution * multiplier, 1), explanation))

    score = base_score * multiplier
    if affects_bus_route:
        score += 8
        reason_rows.append(RiskReason("Dopad na autobus", 8.0, "Uzavírka ovlivňuje autobusovou linku nebo spolehlivost zastávek."))

    final_score = int(round(clamp(score)))
    risk_class = classify_risk(final_score)
    present_fields = sum(1 for key in ("pocet_vozidel_h", "index_plynulosti_0_100", "prum_rychlost_kmh", "riziko_kolize_0_100", "spoju_den", "kapacita_pr") if key in row and not pd.isna(row[key]))
    confidence = round(clamp(0.45 + present_fields * 0.07, 0.45, 0.9), 2)

    return RiskResult(
        score=final_score,
        risk_class=risk_class,
        recommendation=recommendation_for_class(risk_class),
        reasons=sorted(reason_rows, key=lambda item: item.contribution, reverse=True),
        baseline_class=baseline_class(safe_float(row, "hodina", 12)),
        confidence=confidence,
        roi=estimate_roi(row, duration_hours, risk_class, value_of_time_czk_h),
    )


def recommend_better_windows(
    data: pd.DataFrame,
    city: str,
    usek_id: str | None,
    day: str,
    duration_hours: float,
    closure_type: str,
    affects_bus_route: bool,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if data.empty:
        return []

    candidates = data.copy()
    if "obec" in candidates.columns:
        candidates = candidates[candidates["obec"].astype(str) == str(city)]
    if usek_id and "usek_id" in candidates.columns:
        candidates = candidates[candidates["usek_id"].astype(str) == str(usek_id)]
    if day and "den_v_tydnu" in candidates.columns:
        same_day = candidates[candidates["den_v_tydnu"].astype(str) == str(day)]
        if not same_day.empty:
            candidates = same_day

    options: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        hour = int(safe_float(row, "hodina", 12))
        result = score_closure(row, duration_hours, closure_type, affects_bus_route)
        options.append(
            {
                "start_hour": hour,
                "end_hour": min(24, int(hour + duration_hours)),
                "score": result.score,
                "risk_class": result.risk_class,
                "off_peak_bonus": 0 if 10 <= hour <= 13 else 1,
            }
        )

    options.sort(key=lambda item: (item["score"], item["off_peak_bonus"], item["start_hour"]))
    for item in options:
        item.pop("off_peak_bonus", None)
    return options[:limit]
