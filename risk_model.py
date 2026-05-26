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
            clamp((duration_hours**0.55) / (8**0.55) * 100),
            0.12,
            f"Aktivní rozsah uzavírky je {duration_hours:.1f} hod.; dlouhé uzavírky jsou v riziku tlumené, aby samotná délka nepřebila dopravní kontext.",
        ),
    }


def estimate_traffic_forecast(
    row: pd.Series | dict[str, Any],
    duration_hours: float,
    risk_class: str,
    route_extra_time_min: float = 0,
    route_extra_distance_km: float = 0,
    confidence: float = 0.75,
) -> dict[str, float]:
    vehicles = safe_float(row, "pocet_vozidel_h", 180)
    active_hours = max(0.5, float(duration_hours))
    affected_trips = vehicles * active_hours
    affected_people = affected_trips * 1.2
    delay_minutes = max(RISK_CLASS_DELAYS[risk_class], float(route_extra_time_min or 0))
    person_delay_hours_base = affected_people * delay_minutes / 60
    uncertainty = 0.35 if active_hours <= 12 else 0.45
    extra_distance = max(0.0, float(route_extra_distance_km or 0))
    return {
        "affected_trips": round(affected_trips, 0),
        "affected_people": round(affected_people, 0),
        "delay_minutes_per_trip": round(delay_minutes, 1),
        "person_delay_hours_low": round(person_delay_hours_base * (1 - uncertainty), 1),
        "person_delay_hours_base": round(person_delay_hours_base, 1),
        "person_delay_hours_high": round(person_delay_hours_base * (1 + uncertainty), 1),
        "extra_vehicle_km": round(affected_trips * extra_distance, 1),
        "forecast_confidence": round(clamp(confidence, 0.35, 0.9), 2),
        "avoidable_delay_hours_30pct": round(person_delay_hours_base * 0.30, 1),
    }


def network_impact_adjustment(route_impact: Any | None) -> tuple[float, RiskReason | None]:
    if route_impact is None:
        return 0.0, None
    extra_time = max(0.0, float(getattr(route_impact, "extra_time_min", 0) or 0))
    unreachable_share = max(0.0, float(getattr(route_impact, "unreachable_share", 0) or 0))
    if extra_time <= 0 and unreachable_share <= 0:
        return 0.0, None
    adjustment = min(10.0, extra_time * 0.8 + unreachable_share * 10)
    reason = RiskReason(
        "Dopad objížďky",
        round(adjustment, 1),
        f"Vybraný úsek přidává v lokální simulaci přibližně {extra_time:.1f} min; bez spojení {unreachable_share:.0%}.",
    )
    return adjustment, reason


def external_context_adjustment(summary: Any | None) -> tuple[float, RiskReason | None]:
    if summary is None:
        return 0.0, None
    adjustment = min(5.0, max(0.0, float(getattr(summary, "risk_adjustment", 0) or 0)))
    reason_text = getattr(summary, "reason", None)
    if adjustment <= 0:
        return 0.0, None
    return adjustment, RiskReason("Externí dopravní kontext", round(adjustment, 1), str(reason_text))


def score_closure(
    row: pd.Series | dict[str, Any],
    duration_hours: float,
    closure_type: str,
    affects_bus_route: bool,
    route_impact: Any | None = None,
    external_summary: Any | None = None,
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

    network_adjustment, network_reason = network_impact_adjustment(route_impact)
    if network_reason:
        score += network_adjustment
        reason_rows.append(network_reason)

    external_adjustment, external_reason = external_context_adjustment(external_summary)
    if external_reason:
        score += external_adjustment
        reason_rows.append(external_reason)

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
        roi=estimate_traffic_forecast(
            row,
            duration_hours,
            risk_class,
            route_extra_time_min=float(getattr(route_impact, "extra_time_min", 0) or 0),
            route_extra_distance_km=float(getattr(route_impact, "extra_distance_km", 0) or 0),
            confidence=confidence,
        ),
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
