from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

from data_loading import ProjectDataset, load_project_data
from risk_model import recommend_better_windows, score_closure


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
CITY_OPTIONS = ["Mladá Boleslav", "Kladno", "Kolín", "Příbram", "Beroun", "Mělník"]
CITY_COORDINATES = {
    "Mladá Boleslav": (50.4114, 14.9032),
    "Kladno": (50.1431, 14.1052),
    "Kolín": (50.0281, 15.2016),
    "Příbram": (49.6899, 14.0104),
    "Beroun": (49.9638, 14.0720),
    "Mělník": (50.3510, 14.4741),
}
ROUTE_OFFSETS = [
    ((-0.020, -0.030), (0.022, 0.028)),
    ((-0.026, 0.020), (0.028, -0.018)),
    ((-0.005, -0.040), (0.006, 0.040)),
    ((-0.036, -0.008), (0.034, 0.010)),
    ((-0.018, 0.034), (0.024, -0.030)),
    ((-0.030, -0.024), (0.030, 0.022)),
]
DAYS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
CLOSURE_TYPES = ["partial_lane_closure", "full_road_closure", "detour"]
CLOSURE_TYPE_LABELS = {
    "partial_lane_closure": "Částečná uzavírka pruhu",
    "full_road_closure": "Úplná uzavírka silnice",
    "detour": "Objízdná trasa",
}
RISK_CLASS_LABELS = {
    "LOW": "Nízké",
    "MEDIUM": "Střední",
    "HIGH": "Vysoké",
    "CRITICAL": "Kritické",
}
FALLBACK_WARNING = "No exact city match, using available segment data with lower confidence."


@dataclass
class SegmentSelection:
    options: list[str]
    label_to_value: dict[str, object]
    exact_city_match: bool
    exact_segment_match: bool
    used_fallback: bool
    warning: str | None = None


@st.cache_data(show_spinner=False)
def cached_data() -> ProjectDataset:
    data_dir = DATA_DIR if DATA_DIR.exists() else APP_DIR
    return load_project_data(data_dir)


def format_czk(value: float) -> str:
    return f"{value:,.0f} CZK".replace(",", " ")


def display_risk_class(risk_class: str) -> str:
    return f"{RISK_CLASS_LABELS.get(risk_class, risk_class)} ({risk_class})"


def build_segment_options(data: pd.DataFrame, city: str) -> SegmentSelection:
    if data.empty:
        return SegmentSelection(["Segment 1"], {"Segment 1": None}, False, False, True, FALLBACK_WARNING)

    has_city = "obec" in data.columns
    has_segment = "usek_id" in data.columns
    exact_city_match = False
    segment_source = data
    warning = None

    if has_city:
        city_rows = data[data["obec"].astype(str) == str(city)]
        exact_city_match = not city_rows.empty
        if exact_city_match:
            segment_source = city_rows
        else:
            warning = FALLBACK_WARNING

    if has_segment:
        values = sorted(segment_source["usek_id"].dropna().astype(str).unique())
        if not values:
            values = sorted(data["usek_id"].dropna().astype(str).unique())
        if values:
            return SegmentSelection(
                options=values,
                label_to_value={value: value for value in values},
                exact_city_match=exact_city_match,
                exact_segment_match=True,
                used_fallback=not exact_city_match,
                warning=warning,
            )

    source_index = list(segment_source.index)
    if not source_index:
        source_index = list(data.index)
    labels = [f"Segment {position + 1}" for position in range(len(source_index))]
    return SegmentSelection(
        options=labels,
        label_to_value=dict(zip(labels, source_index)),
        exact_city_match=exact_city_match,
        exact_segment_match=False,
        used_fallback=not exact_city_match or not has_segment,
        warning=warning,
    )


def segment_value_from_label(selection: SegmentSelection, label: str) -> object:
    return selection.label_to_value.get(label)


def confidence_for_selection(exact_city_match: bool, exact_segment_match: bool) -> float:
    confidence = 0.58
    if exact_city_match:
        confidence += 0.17
    if exact_segment_match:
        confidence += 0.15
    return min(0.90, confidence)


def road_network_data(city: str, segment_labels: list[str], points_per_route: int = 7) -> pd.DataFrame:
    lat, lon = CITY_COORDINATES.get(city, (50.0, 14.5))
    rows: list[dict[str, object]] = []
    points_per_route = max(2, points_per_route)

    for idx, label in enumerate(segment_labels):
        start_offset, end_offset = ROUTE_OFFSETS[idx % len(ROUTE_OFFSETS)]
        start_lat, start_lon = lat + start_offset[0], lon + start_offset[1]
        end_lat, end_lon = lat + end_offset[0], lon + end_offset[1]
        route_id = f"route-{idx + 1}"

        for point_index in range(points_per_route):
            ratio = point_index / (points_per_route - 1)
            rows.append(
                {
                    "route_id": route_id,
                    "segment_label": label,
                    "snap_id": f"{label}:{point_index}",
                    "lat": start_lat + (end_lat - start_lat) * ratio,
                    "lon": start_lon + (end_lon - start_lon) * ratio,
                    "order": point_index,
                    "point_role": "snap",
                }
            )

    return pd.DataFrame(rows)


def road_path_data(network: pd.DataFrame) -> pd.DataFrame:
    if network.empty:
        return pd.DataFrame(columns=["route_id", "segment_label", "path"])

    rows: list[dict[str, object]] = []
    for (route_id, segment_label), group in network.sort_values("order").groupby(["route_id", "segment_label"]):
        rows.append(
            {
                "route_id": route_id,
                "segment_label": segment_label,
                "path": group[["lon", "lat"]].values.tolist(),
            }
        )
    return pd.DataFrame(rows)


def get_selected_snap_ids(event: object) -> list[str]:
    if not event:
        return []

    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if not selection:
        return []

    objects = selection.get("objects") if isinstance(selection, dict) else getattr(selection, "objects", None)
    if isinstance(objects, dict):
        snap_ids: list[str] = []
        for selected_objects in objects.values():
            if isinstance(selected_objects, list):
                snap_ids.extend(str(item["snap_id"]) for item in selected_objects if isinstance(item, dict) and item.get("snap_id"))
        if snap_ids:
            return snap_ids

    snap_select = selection.get("road_snap_select") if isinstance(selection, dict) else getattr(selection, "road_snap_select", None)
    if not snap_select:
        return []

    if isinstance(snap_select, list):
        return [str(item["snap_id"]) for item in snap_select if isinstance(item, dict) and item.get("snap_id")]
    if isinstance(snap_select, dict):
        labels = snap_select.get("snap_id")
        if isinstance(labels, list) and labels:
            return [str(label) for label in labels]
        if isinstance(labels, str):
            return [labels]

    return []


def selected_segment_from_snap_points(network: pd.DataFrame, snap_ids: list[str]) -> str | None:
    if len(snap_ids) < 2 or network.empty or "snap_id" not in network.columns:
        return None

    selected = network[network["snap_id"].astype(str).isin([str(snap_id) for snap_id in snap_ids[:2]])]
    if len(selected) < 2:
        return None
    segment_labels = selected["segment_label"].dropna().astype(str).unique()
    if len(segment_labels) != 1:
        return None
    return segment_labels[0]


def render_route_picker(city: str, segment_labels: list[str]) -> tuple[str | None, bool]:
    if not segment_labels:
        st.info("Pro mapový výběr nejsou dostupné žádné úseky. Používám městský průměr.")
        return None, False

    st.subheader("Vyberte uzavíraný úsek na mapě")
    st.caption(
        "Klikněte na dva body na stejné silnici. Body se automaticky snapují na nejbližší demo trasu. "
        "Pokud výběr nejde jednoznačně určit, predikce použije městský průměr."
    )

    routes = road_network_data(city, segment_labels, points_per_route=9)
    paths = road_path_data(routes)
    lat, lon = CITY_COORDINATES.get(city, (50.0, 14.5))

    path_layer = pdk.Layer(
        "PathLayer",
        data=paths,
        id="demo-roads",
        get_path="path",
        get_color=[34, 94, 168, 190],
        get_width=7,
        width_min_pixels=4,
        pickable=False,
    )
    snap_layer = pdk.Layer(
        "ScatterplotLayer",
        data=routes,
        id="snap-points",
        get_position="[lon, lat]",
        get_radius=24,
        radius_units="meters",
        get_fill_color=[44, 162, 95, 210],
        get_line_color=[255, 255, 255],
        line_width_min_pixels=2,
        pickable=True,
        auto_highlight=True,
    )
    city_layer = pdk.Layer(
        "ScatterplotLayer",
        data=pd.DataFrame([{"city": city, "lat": lat, "lon": lon}]),
        id="city-center",
        get_position="[lon, lat]",
        get_radius=55,
        radius_units="meters",
        get_fill_color=[20, 20, 20, 220],
        pickable=False,
    )

    deck = pdk.Deck(
        layers=[path_layer, snap_layer, city_layer],
        initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=12, pitch=0),
        tooltip={"text": "Úsek: {segment_label}\nSnap bod: {snap_id}"},
        map_style=None,
    )
    event = st.pydeck_chart(
        deck,
        width="stretch",
        height=620,
        key=f"road_snap_map_{city}",
        on_select="rerun",
        selection_mode="multi-object",
    )
    snap_ids = get_selected_snap_ids(event)
    selected_segment = selected_segment_from_snap_points(routes, snap_ids)

    if selected_segment:
        st.success(f"Vybraný úsek z mapy: {selected_segment}")
        return selected_segment, True
    if len(snap_ids) == 1:
        st.info("Vyberte ještě druhý bod na stejné silnici. Zatím používám městský průměr.")
    elif len(snap_ids) >= 2:
        st.warning("Vybrané body neleží na stejném demo úseku. Používám městský průměr.")
    else:
        st.info("Zatím není vybraný uzavíraný úsek. Používám městský průměr.")
    return None, False


def choose_row(data: pd.DataFrame, city: str, segment_value: object, day: str, hour: int) -> pd.Series:
    candidates = data.copy()
    if "obec" in candidates.columns:
        city_rows = candidates[candidates["obec"].astype(str) == str(city)]
        if not city_rows.empty:
            candidates = city_rows

    if segment_value is not None and "usek_id" in candidates.columns:
        narrowed = candidates[candidates["usek_id"].astype(str) == str(segment_value)]
        if not narrowed.empty:
            candidates = narrowed
    elif segment_value is not None and "usek_id" not in data.columns:
        try:
            return data.loc[int(segment_value)]
        except (KeyError, TypeError, ValueError):
            pass

    if "den_v_tydnu" in candidates.columns:
        same_day = candidates[candidates["den_v_tydnu"].astype(str) == str(day)]
        if not same_day.empty:
            candidates = same_day

    if candidates.empty:
        return data.iloc[0]

    if "hodina" not in candidates.columns:
        return candidates.iloc[0]

    candidates = candidates.copy()
    candidates["hour_distance"] = (pd.to_numeric(candidates["hodina"], errors="coerce").fillna(hour) - hour).abs()
    return candidates.sort_values(["hour_distance", "hodina"]).iloc[0]


def main() -> None:
    st.set_page_config(page_title="Uzavírka AI", page_icon=":construction:", layout="wide")
    dataset = cached_data()
    data = dataset.data

    st.title("Uzavírka AI")
    st.caption(
        "MVP pro podporu rozhodování při schvalování uzavírek. "
        "Nehraje si na plnou dopravní simulaci; hodnotí riziko podle zranitelnosti konkrétního úseku."
    )

    with st.sidebar:
        st.header("Plánovaná uzavírka")
        city = st.selectbox("Město / obec", CITY_OPTIONS, index=0)

        segment_selection = build_segment_options(data, city)
        if segment_selection.warning:
            st.warning(segment_selection.warning)
        day = st.selectbox("Den v týdnu", DAYS, index=0)
        start_hour = st.slider("Plánovaný začátek", 0, 23, 10)
        duration_hours = st.slider("Délka uzavírky v hodinách", 0.5, 12.0, 3.0, 0.5)
        closure_type_label = st.selectbox("Typ uzavírky", list(CLOSURE_TYPE_LABELS.values()))
        closure_type = next(key for key, value in CLOSURE_TYPE_LABELS.items() if value == closure_type_label)
        affects_bus_route = st.checkbox("Ovlivní autobusovou linku", value=False)
        value_of_time = st.number_input("Hodnota času (Kč/hod)", min_value=50, max_value=1000, value=200, step=25)

    segment_label, exact_map_segment = render_route_picker(city, segment_selection.options)
    segment_value = segment_value_from_label(segment_selection, segment_label) if exact_map_segment and segment_label else None

    selected_row = choose_row(data, city, segment_value, day, start_hour)
    selected_row = selected_row.copy()
    selected_row["hodina"] = start_hour
    selected_row["den_v_tydnu"] = day

    result = score_closure(
        selected_row,
        duration_hours=duration_hours,
        closure_type=closure_type,
        affects_bus_route=affects_bus_route,
        value_of_time_czk_h=value_of_time,
    )
    result.confidence = min(
        result.confidence,
        confidence_for_selection(segment_selection.exact_city_match, exact_map_segment and segment_selection.exact_segment_match),
    )

    score_col, class_col, rec_col, confidence_col = st.columns(4)
    score_col.metric("Rizikové skóre", f"{result.score}/100")
    class_col.metric("Třída rizika", display_risk_class(result.risk_class))
    rec_col.metric("Doporučení", result.recommendation)
    confidence_col.metric("Spolehlivost", f"{result.confidence:.0%}")

    st.subheader("Proč bylo riziko přiděleno")
    reasons = pd.DataFrame(
        [{"Důvod": reason.name, "Příspěvek": reason.contribution, "Vysvětlení": reason.explanation} for reason in result.reasons]
    )
    st.bar_chart(reasons.set_index("Důvod")["Příspěvek"])
    st.dataframe(reasons, hide_index=True, width="stretch")

    baseline_col, model_col = st.columns(2)
    baseline_col.subheader("Baseline")
    baseline_col.metric("Třída podle špičky", display_risk_class(result.baseline_class))
    baseline_col.write("Baseline rozlišuje jen to, zda uzavírka začíná v dopravní špičce.")
    model_col.subheader("ClosureImpact")
    model_col.metric("Třída podle modelu", display_risk_class(result.risk_class))
    model_col.write(
        "ClosureImpact navíc využívá plynulost konkrétního úseku, počet vozidel, rychlost, "
        "bezpečnostní riziko, délku uzavírky, typ uzavírky a dostupné dopravní alternativy."
    )

    st.subheader("Lepší časová okna")
    windows = recommend_better_windows(
        data,
        city=city,
        usek_id=str(segment_value) if exact_map_segment and segment_selection.exact_segment_match and segment_value is not None else None,
        day=day,
        duration_hours=duration_hours,
        closure_type=closure_type,
        affects_bus_route=affects_bus_route,
        limit=3,
    )
    if windows:
        st.dataframe(
            pd.DataFrame(windows).rename(
                columns={
                    "start_hour": "Začátek",
                    "end_hour": "Konec",
                    "score": "Rizikové skóre",
                    "risk_class": "Třída rizika",
                }
            ).assign(**{"Třída rizika": lambda frame: frame["Třída rizika"].map(display_risk_class)}),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("V aktuálních datech není pro vybrané město nebo úsek dostupné srovnatelné méně rizikové okno.")

    st.subheader("Odhad ekonomického dopadu")
    roi_col_1, roi_col_2, roi_col_3 = st.columns(3)
    roi_col_1.metric("Dotčené osoby", f"{result.roi['affected_people']:,.0f}".replace(",", " "))
    roi_col_2.metric("Odhad společenské ztráty", format_czk(result.roi["estimated_social_loss_czk"]))
    roi_col_3.metric("Možná úspora při snížení rizika o 30 %", format_czk(result.roi["possible_savings_30pct_czk"]))
    st.caption(
        "Vzorec: dotčené osoby = počet vozidel × délka uzavírky × 1,2 cestujícího. "
        "Zpoždění v minutách závisí na třídě rizika; výchozí hodnota času je 200 Kč/hod."
    )

    with st.expander("Report kvality dat"):
        st.write(f"Načteno {len(data)} řádků ze souborů: {', '.join(dataset.source_files)}")
        report_rows = []
        for filename, report in dataset.reports.items():
            report_rows.append(
                {
                    "Soubor": filename,
                    "Řádky": report["rows"],
                    "Sloupce": len(report["columns"]),
                    "Kódování": report["encoding"],
                    "Doplněné číselné hodnoty": report["numeric_filled"],
                    "Opravené extrémní hodnoty": report["clipped_values"],
                }
            )
        st.dataframe(pd.DataFrame(report_rows), hide_index=True, width="stretch")

    st.subheader("Etická poznámka")
    st.write(
        "Toto MVP používá pouze agregovaná dopravní a obecní kontextová data. Nesleduje jednotlivce. "
        "AI skóre je poradní, zobrazuje spolehlivost odhadu a případy s nedostatkem dat mají projít ruční kontrolou úředníka."
    )


if __name__ == "__main__":
    main()
