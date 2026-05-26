from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components

from data_loading import ProjectDataset, load_project_data
from osm_roads import load_mlada_boleslav_roads
from risk_model import recommend_better_windows, score_closure
from route_analysis import MLADA_BOLESLAV, MLADA_BOLESLAV_FAN_SEGMENTS, RouteImpact, analyze_closure, build_road_network, path_layer_data


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


@st.cache_data(show_spinner=False)
def cached_mlada_boleslav_roads() -> list[dict]:
    return load_mlada_boleslav_roads()


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
            if city == MLADA_BOLESLAV and values == ["U01"]:
                return SegmentSelection(
                    options=MLADA_BOLESLAV_FAN_SEGMENTS,
                    label_to_value={value: "U01" for value in MLADA_BOLESLAV_FAN_SEGMENTS},
                    exact_city_match=exact_city_match,
                    exact_segment_match=True,
                    used_fallback=not exact_city_match,
                    warning=warning,
                )
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
    center = CITY_COORDINATES.get(city, (50.0, 14.5))
    return build_road_network(city, segment_labels, center, points_per_route=points_per_route).points


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


def route_network_for_city(city: str, segment_labels: list[str], data: pd.DataFrame):
    center = CITY_COORDINATES.get(city, (50.0, 14.5))
    route_network = build_road_network(city, segment_labels, center, traffic_data=data, points_per_route=9)
    state_key = f"selected_map_segment_{city}"
    selected = st.session_state.get(state_key) or (segment_labels[0] if segment_labels else None)
    if selected not in segment_labels and segment_labels:
        selected = segment_labels[0]
    impact = analyze_closure(route_network, selected)
    return route_network, route_network.points, path_layer_data(route_network), impact


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


def render_leaflet_draw_snap_map(city: str, paths: pd.DataFrame, impact_by_segment: dict[str, RouteImpact], osm_roads: list[dict] | None = None) -> None:
    closure_paths = paths[paths["road_role"] == "closure"]
    roads = []
    visual_roads = osm_roads or []
    if visual_roads:
        for road in visual_roads:
            roads.append(
                {
                    "id": str(road["id"]),
                    "label": str(road["name"]),
                    "coords": road["coords"],
                    "extraKm": 0,
                    "extraMin": 0,
                    "affected": 0,
                    "detour": [],
                }
            )
    for _, row in ([] if visual_roads else closure_paths.iterrows()):
        segment_label = str(row["segment_label"])
        if visual_roads and not segment_label.startswith("U01"):
            continue
        impact = impact_by_segment.get(segment_label)
        roads.append(
            {
                "id": segment_label,
                "label": segment_label,
                "coords": [[lat, lon] for lon, lat in row["path"]],
                "extraKm": impact.extra_distance_km if impact else 0,
                "extraMin": impact.extra_time_min if impact else 0,
                "affected": impact.affected_edges if impact else 0,
                "detour": [[lat, lon] for lon, lat in impact.detour_routes.iloc[0]["path"]] if impact and not impact.detour_routes.empty else [],
            }
        )
    lat, lon = CITY_COORDINATES.get(city, (50.0, 14.5))
    payload = json.dumps({"center": [lat, lon], "roads": roads}, ensure_ascii=False)
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css" />
  <style>
    html, body {{ margin: 0; padding: 0; background: #0f1117; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    #map {{ height: 640px; width: 100%; border-radius: 8px; overflow: hidden; }}
    .snap-panel {{ position: absolute; left: 14px; bottom: 14px; z-index: 900; max-width: 360px; background: rgba(15, 23, 42, 0.92); border: 1px solid rgba(148, 163, 184, 0.35); border-radius: 8px; padding: 12px 14px; box-shadow: 0 18px 40px rgba(0,0,0,0.35); }}
    .snap-title {{ font-weight: 800; font-size: 15px; margin-bottom: 4px; }}
    .snap-body {{ color: #cbd5e1; font-size: 13px; line-height: 1.45; }}
    .leaflet-container {{ background: #111827; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="snap-panel">
    <div class="snap-title" id="snap-title">Draw across a road</div>
    <div class="snap-body" id="snap-body">Use the polyline tool, draw over the road you want to close, then finish the line. The nearest road snaps red; the recomputed detour turns green.</div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
  <script>
    const payload = {payload};
    const map = L.map('map', {{ zoomControl: true }}).setView(payload.center, 13);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);

    const roadLayers = new Map();
    let selectedRoad = null;
    let selectedDetour = null;
    let drawnLayer = null;
    const drawnItems = new L.FeatureGroup().addTo(map);

    for (const road of payload.roads) {{
      const layer = L.polyline(road.coords, {{ color: '#2563eb', weight: 7, opacity: 0.78 }}).addTo(map);
      layer.bindTooltip(road.id, {{ sticky: true }});
      roadLayers.set(road.id, {{ road, layer }});
    }}

    const drawControl = new L.Control.Draw({{
      draw: {{
        polyline: {{ shapeOptions: {{ color: '#f8fafc', weight: 4, dashArray: '8 8' }} }},
        polygon: false,
        rectangle: false,
        circle: false,
        circlemarker: false,
        marker: false
      }},
      edit: {{ featureGroup: drawnItems, edit: false, remove: true }}
    }});
    map.addControl(drawControl);

    function distanceToSegment(p, a, b) {{
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      if (dx === 0 && dy === 0) return p.distanceTo(a);
      const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)));
      return p.distanceTo(L.point(a.x + t * dx, a.y + t * dy));
    }}

    function roadDistance(drawLatLngs, roadCoords) {{
      const drawPoints = drawLatLngs.map(ll => map.latLngToLayerPoint(ll));
      const roadPoints = roadCoords.map(c => map.latLngToLayerPoint(L.latLng(c[0], c[1])));
      let best = Infinity;
      for (const p of drawPoints) {{
        for (let i = 1; i < roadPoints.length; i++) {{
          best = Math.min(best, distanceToSegment(p, roadPoints[i - 1], roadPoints[i]));
        }}
      }}
      return best;
    }}

    function selectRoad(roadId) {{
      if (selectedRoad) selectedRoad.layer.setStyle({{ color: '#2563eb', weight: 7, opacity: 0.78 }});
      if (selectedDetour) map.removeLayer(selectedDetour);
      selectedRoad = roadLayers.get(roadId);
      if (!selectedRoad) return;
      selectedRoad.layer.setStyle({{ color: '#dc2626', weight: 12, opacity: 0.95 }});
      selectedRoad.layer.bringToFront();
      if (selectedRoad.road.detour.length > 1) {{
        selectedDetour = L.polyline(selectedRoad.road.detour, {{ color: '#16a34a', weight: 9, opacity: 0.9 }}).addTo(map);
      }}
      document.getElementById('snap-title').textContent = 'Snapped closure: ' + selectedRoad.road.label;
      if (selectedRoad.road.detour.length > 1) {{
        document.getElementById('snap-body').textContent = `Red is the snapped closed road. Green is the local detour. Affected edges: ${{selectedRoad.road.affected}} · added distance: ${{selectedRoad.road.extraKm.toFixed(2)}} km · added time: ${{selectedRoad.road.extraMin.toFixed(1)}} min.`;
      }} else {{
        document.getElementById('snap-body').textContent = 'Red is the exact OpenStreetMap road nearest to your drawing. This visual layer contains every fetched drivable road in the Mladá Boleslav map window.';
      }}
    }}

    map.on(L.Draw.Event.CREATED, event => {{
      if (drawnLayer) drawnItems.removeLayer(drawnLayer);
      drawnLayer = event.layer;
      drawnItems.addLayer(drawnLayer);
      const drawLatLngs = drawnLayer.getLatLngs();
      let best = null;
      for (const [roadId, entry] of roadLayers.entries()) {{
        const distance = roadDistance(drawLatLngs, entry.road.coords);
        if (!best || distance < best.distance) best = {{ roadId, distance }};
      }}
      if (best) selectRoad(best.roadId);
    }});

    if (payload.roads.length) {{
      const bounds = L.latLngBounds(payload.roads.flatMap(r => r.coords));
      map.fitBounds(bounds.pad(0.18));
      selectRoad(payload.roads[0].id);
    }}
  </script>
</body>
</html>
"""
    components.html(html, height=660, scrolling=False)


def render_route_picker(city: str, segment_labels: list[str], data: pd.DataFrame) -> tuple[str | None, bool, RouteImpact]:
    if not segment_labels:
        st.info("Pro mapový výběr nejsou dostupné žádné úseky. Používám městský průměr.")
        return None, False, analyze_closure(build_road_network(city, [], CITY_COORDINATES.get(city, (50.0, 14.5))), None)

    st.subheader("Vyberte uzavíraný úsek na mapě")
    st.caption(
        "Klikněte na dva body na stejné silnici. Aplikace úsek uzavře v lokálním grafu, "
        "spočítá objízdnou trasu a ukáže, kam se doprava pravděpodobně přelije. "
        "Jde o rozhodovací simulátor pro úředníky, ne o plnou produkční dopravní simulaci."
    )

    route_network, routes, paths, route_impact = route_network_for_city(city, segment_labels, data)
    lat, lon = CITY_COORDINATES.get(city, (50.0, 14.5))
    candidate_paths = paths[paths["road_role"] == "closure"]
    support_paths = paths[paths["road_role"] != "closure"]
    if city == MLADA_BOLESLAV:
        impact_by_segment = {str(label): analyze_closure(route_network, str(label)) for label in candidate_paths["segment_label"].dropna().unique()}
        osm_roads = cached_mlada_boleslav_roads()
        if not osm_roads:
            st.warning("OpenStreetMap roads could not be loaded, so the map is using the smaller demo road fan.")
        else:
            st.caption(f"Loaded {len(osm_roads)} drivable OpenStreetMap road geometries in the Mladá Boleslav map window.")
        render_leaflet_draw_snap_map(city, paths, impact_by_segment, osm_roads=osm_roads)
        active_segment = str(route_impact.closed_segment or segment_labels[0])
        st.info("Draw across a road in the map above. The browser snaps your drawing to the nearest road and paints it red.")
        return active_segment, True, route_impact

    support_layer = pdk.Layer(
        "PathLayer",
        data=support_paths,
        id="support-roads",
        get_path="path",
        get_color=[132, 142, 158, 120],
        get_width=5,
        width_min_pixels=2,
        pickable=False,
    )
    candidate_layer = pdk.Layer(
        "PathLayer",
        data=candidate_paths,
        id="candidate-roads",
        get_path="path",
        get_color=[34, 94, 168, 190],
        get_width=7,
        width_min_pixels=4,
        pickable=False,
    )
    impacted_layer = pdk.Layer(
        "PathLayer",
        data=route_impact.impacted_edges,
        id="impacted-roads",
        get_path="path",
        get_color=[245, 158, 11, 210],
        get_width=9,
        width_min_pixels=5,
        pickable=False,
    )
    detour_layer = pdk.Layer(
        "PathLayer",
        data=route_impact.detour_routes,
        id="detour-routes",
        get_path="path",
        get_color=[22, 163, 74, 230],
        get_width=12,
        width_min_pixels=6,
        pickable=False,
    )
    closure_layer = pdk.Layer(
        "PathLayer",
        data=route_impact.closure_edges,
        id="closure-road",
        get_path="path",
        get_color=[220, 38, 38, 240],
        get_width=14,
        width_min_pixels=7,
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
        layers=[support_layer, candidate_layer, impacted_layer, detour_layer, closure_layer, snap_layer, city_layer],
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
    state_key = f"selected_map_segment_{city}"
    if selected_segment and selected_segment != st.session_state.get(state_key):
        st.session_state[state_key] = selected_segment
        st.rerun()

    active_segment = str(route_impact.closed_segment or segment_labels[0])
    if selected_segment or st.session_state.get(state_key):
        st.success(f"Analyzovaný uzavřený úsek: {active_segment}")
        return active_segment, True, route_impact
    if len(snap_ids) == 1:
        st.info("Vyberte ještě druhý bod na stejné silnici. Zatím analyzuji první dostupný úsek jako náhled.")
    elif len(snap_ids) >= 2:
        st.warning("Vybrané body neleží na stejném úseku. Zatím analyzuji první dostupný úsek jako náhled.")
    else:
        st.info("Červeně je uzavíraný úsek, oranžově zasažené hrany a zeleně objízdná trasa. Kliknutím na dva body vyberete jiný úsek.")
    return active_segment, True, route_impact


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

    segment_label, exact_map_segment, route_impact = render_route_picker(city, segment_selection.options, data)
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

    st.subheader("Dopad na síť a objíždění")
    network_col_1, network_col_2, network_col_3, network_col_4 = st.columns(4)
    network_col_1.metric("Zasažené hrany", route_impact.affected_edges)
    network_col_2.metric("Navíc vzdálenost", f"{route_impact.extra_distance_km:.2f} km")
    network_col_3.metric("Navíc čas", f"{route_impact.extra_time_min:.1f} min")
    network_col_4.metric("Bez spojení", f"{route_impact.unreachable_share:.0%}")
    st.caption(
        "Síťový výpočet používá lokální graf z dostupných úseků a jejich dopravních ukazatelů. "
        "Ukazuje relativní dopad a směr přelivu dopravy pro demo, ne garantovanou navigační trasu."
    )
    if not route_impact.detour_routes.empty:
        detour_rows = route_impact.detour_routes[["segment_label", "extra_distance_km", "extra_time_min"]].rename(
            columns={
                "segment_label": "Uzavřený úsek",
                "extra_distance_km": "Navíc km",
                "extra_time_min": "Navíc min",
            }
        )
        st.dataframe(detour_rows, hide_index=True, width="stretch")

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
