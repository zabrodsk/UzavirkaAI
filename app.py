from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

import folium
import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

from data_loading import PROJECT_FILES, ProjectDataset, load_project_data
from external_data import ExternalZipSummary, load_external_zip_summary
from osm_roads import load_mlada_boleslav_roads
from risk_model import recommend_better_windows, score_closure
from route_analysis import (
    MLADA_BOLESLAV,
    MLADA_BOLESLAV_FAN_SEGMENTS,
    RouteImpact,
    SnappedRoadPoint,
    analyze_closure,
    analyze_osm_closure,
    build_road_network,
    osm_closure_path,
    path_layer_data,
    snap_osm_road_point,
    update_snap_clicks,
)


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
    data_dir = DATA_DIR if any((DATA_DIR / filename).exists() for filename in PROJECT_FILES.values()) else APP_DIR
    return load_project_data(data_dir)


@st.cache_data(show_spinner=False)
def cached_mlada_boleslav_roads() -> list[dict]:
    return load_mlada_boleslav_roads()


@st.cache_data(show_spinner=False)
def cached_external_summary() -> ExternalZipSummary:
    return load_external_zip_summary(APP_DIR, max_files=3)


def apply_brand_styles() -> None:
    st.html(
        """
        <style>
        .brand-title-row {
          display: flex;
          align-items: center;
          gap: 0.65rem;
          margin: 0 0 0.15rem;
        }
        .brand-logo-mark {
          display: inline-block;
          width: 2rem;
          height: 2rem;
          border: 1px solid #0F0F0F;
          border-radius: 0.25rem;
          background: repeating-linear-gradient(135deg, #D9381E 0 0.38rem, #F1EEE7 0.38rem 0.76rem);
          flex: 0 0 auto;
        }
        .brand-title-text {
          color: #0F0F0F;
          font-size: 2.35rem;
          font-weight: 700;
          line-height: 1.15;
        }
        .brand-title-text .brand-dot {
          color: #D9381E;
        }
        </style>
        """
    )


def render_brand_header() -> None:
    st.html(
        """
        <div class="brand-title-row">
          <span class="brand-logo-mark" aria-hidden="true"></span>
          <span class="brand-title-text">uzavírka<span class="brand-dot">.</span>ai</span>
        </div>
        """
    )
    st.caption(
        "MVP pro podporu rozhodování při schvalování uzavírek. "
        "Nehraje si na plnou dopravní simulaci; hodnotí riziko podle zranitelnosti konkrétního úseku."
    )

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


def map_click_signature(click: dict | None) -> tuple[float, float] | None:
    if not click:
        return None
    lat = click.get("lat")
    lng = click.get("lng", click.get("lon"))
    if lat is None or lng is None:
        return None
    return (round(float(lat), 7), round(float(lng), 7))


def closure_state_key(city: str) -> str:
    return f"osm_closure_snaps_{city}"


def render_osm_folium_picker(city: str, osm_roads: list[dict], data: pd.DataFrame) -> tuple[str | None, bool, RouteImpact]:
    state_key = closure_state_key(city)
    click_key = f"osm_last_click_{city}"
    st.session_state.setdefault(state_key, [])

    if st.button("Vymazat výběr", key=f"clear_osm_selection_{city}"):
        st.session_state[state_key] = []
        st.session_state.pop(click_key, None)
        st.rerun()

    current_snaps: list[SnappedRoadPoint] = st.session_state[state_key]
    start = current_snaps[0] if len(current_snaps) >= 1 else None
    end = current_snaps[1] if len(current_snaps) >= 2 else None
    route_impact = analyze_osm_closure(osm_roads, start, end, traffic_data=data, city=city)
    fmap = build_osm_folium_map(city, osm_roads, current_snaps, route_impact)
    event = st_folium(
        fmap,
        key=f"osm_snap_map_{city}",
        height=640,
        width=None,
        returned_objects=["last_clicked", "last_object_clicked"],
    )
    clicked_payload = None
    if isinstance(event, dict):
        clicked_payload = event.get("last_clicked") or event.get("last_object_clicked")
    signature = map_click_signature(clicked_payload)
    if signature and signature != st.session_state.get(click_key):
        st.session_state[click_key] = signature
        snap = snap_osm_road_point(osm_roads, signature[0], signature[1])
        st.session_state[state_key] = update_snap_clicks(current_snaps, snap)
        st.rerun()

    current_snaps = st.session_state[state_key]
    if len(current_snaps) == 0:
        st.info("Klikněte do mapy pro START bod. Druhý klik nastaví END a spustí simulaci uzavírky.")
        return None, False, _empty_preview_impact(city, data)
    if len(current_snaps) == 1:
        snap = current_snaps[0]
        st.info(f"START přichycen: {snap.road_name} ({snap.road_id}), bod {snap.index}. Klikněte na END na stejné silnici.")
        return None, False, _empty_preview_impact(city, data)

    start, end = current_snaps[:2]
    if start.road_id != end.road_id:
        st.warning(
            "START a END jsou na různých OSM silnicích. Demo v tomto režimu nepočítá přesnou uzavírku; "
            "klikněte potřetí a začněte nový výběr na jedné silnici."
        )
        return None, False, route_impact

    closure_path = osm_closure_path(osm_roads, start, end)
    closure_length = sum(_segment_distance(closure_path[index - 1], closure_path[index]) for index in range(1, len(closure_path)))
    reachable_text = "ano" if route_impact.unreachable_share == 0 else "ne"
    st.success(
        f"Uzavírka: {start.road_name} ({start.road_id}) · délka {closure_length:.2f} km · "
        f"objížďka +{route_impact.extra_distance_km:.2f} km / +{route_impact.extra_time_min:.1f} min · spojení zůstává: {reachable_text}"
    )
    return route_impact.closed_segment, True, route_impact


def build_osm_folium_map(city: str, osm_roads: list[dict], snaps: list[SnappedRoadPoint], impact: RouteImpact) -> folium.Map:
    lat, lon = CITY_COORDINATES.get(city, (50.0, 14.5))
    fmap = folium.Map(location=[lat, lon], zoom_start=13, tiles="OpenStreetMap", control_scale=True)
    closure_path: list[list[float]] = []
    if len(snaps) >= 2 and snaps[0].road_id == snaps[1].road_id:
        closure_path = [[float(point[0]), float(point[1])] for point in osm_closure_path(osm_roads, snaps[0], snaps[1])]

    for road in osm_roads:
        coords = [[float(point[0]), float(point[1])] for point in road.get("coords", []) if len(point) >= 2]
        if len(coords) < 2:
            continue
        folium.PolyLine(
            coords,
            color="#2f3437",
            weight=2,
            opacity=0.42,
            tooltip=str(road.get("name") or road.get("id")),
            interactive=False,
        ).add_to(fmap)

    if not impact.impacted_edges.empty:
        for _, row in impact.impacted_edges.head(80).iterrows():
            folium.PolyLine(_lat_lon_path(row["path"]), color="#F59E0B", weight=4, opacity=0.38, tooltip="Zasažená hrana", interactive=False).add_to(fmap)
    if not impact.detour_routes.empty:
        for _, row in impact.detour_routes.iterrows():
            folium.PolyLine(_lat_lon_path(row["path"]), color="#16A34A", weight=6, opacity=0.82, tooltip="Odhad objížďky", interactive=False).add_to(fmap)
    if len(closure_path) >= 2:
        folium.PolyLine(closure_path, color="#FFFFFF", weight=15, opacity=0.96, tooltip="Vybraný uzavřený úsek", interactive=False).add_to(fmap)
        folium.PolyLine(closure_path, color="#D9381E", weight=10, opacity=1, tooltip="Vybraný uzavřený úsek", interactive=False).add_to(fmap)
    elif not impact.closure_edges.empty:
        for _, row in impact.closure_edges.iterrows():
            folium.PolyLine(_lat_lon_path(row["path"]), color="#FFFFFF", weight=13, opacity=0.9, tooltip="Uzavřený úsek", interactive=False).add_to(fmap)
            folium.PolyLine(_lat_lon_path(row["path"]), color="#D9381E", weight=8, opacity=0.95, tooltip="Uzavřený úsek", interactive=False).add_to(fmap)
    elif len(snaps) >= 2:
        fallback_line = [[snaps[0].lat, snaps[0].lon], [snaps[1].lat, snaps[1].lon]]
        folium.PolyLine(fallback_line, color="#FFFFFF", weight=15, opacity=0.9, dash_array="8,8", tooltip="START a END nejsou na stejné silnici", interactive=False).add_to(fmap)
        folium.PolyLine(fallback_line, color="#D9381E", weight=10, opacity=0.95, dash_array="8,8", tooltip="START a END nejsou na stejné silnici", interactive=False).add_to(fmap)

    for snap, label, color in zip(snaps[:2], ["START", "END"], ["#16A34A", "#D9381E"]):
        folium.Marker(
            location=[snap.lat, snap.lon],
            icon=_endpoint_x_icon(label, color),
            tooltip=f"{label}: {snap.road_name}",
        ).add_to(fmap)
    return fmap


def _endpoint_x_icon(label: str, color: str) -> folium.DivIcon:
    safe_label = escape(label)
    return folium.DivIcon(
        icon_size=(96, 38),
        icon_anchor=(15, 15),
        html=f"""
        <div style="display:flex;align-items:center;gap:6px;pointer-events:none;">
          <span style="display:grid;place-items:center;width:30px;height:30px;border:3px solid #fff;border-radius:999px;background:{color};color:#fff;font-size:30px;line-height:24px;font-weight:900;box-shadow:0 3px 12px rgba(15,23,42,.72);">&times;</span>
          <span style="display:block;padding:4px 7px;border:1px solid rgba(255,255,255,.35);border-radius:6px;background:rgba(15,23,42,.94);color:#fff;font:800 11px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;letter-spacing:.02em;box-shadow:0 3px 12px rgba(15,23,42,.42);">{safe_label}</span>
        </div>
        """,
    )


def _empty_preview_impact(city: str, data: pd.DataFrame) -> RouteImpact:
    route_network = build_road_network(city, MLADA_BOLESLAV_FAN_SEGMENTS, CITY_COORDINATES.get(city, (50.0, 14.5)), traffic_data=data)
    return analyze_closure(route_network, MLADA_BOLESLAV_FAN_SEGMENTS[0])


def _lat_lon_path(path: list[list[float]]) -> list[list[float]]:
    return [[float(lat), float(lon)] for lon, lat in path]


def _segment_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import atan2, cos, radians, sin, sqrt

    lat1, lon1 = a
    lat2, lon2 = b
    radius_km = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    hav = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius_km * atan2(sqrt(hav), sqrt(1 - hav))


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
  <style>
    html, body {{ margin: 0; padding: 0; background: #0f1117; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    #map {{ height: 640px; width: 100%; border-radius: 8px; overflow: hidden; }}
    .snap-panel {{ position: absolute; left: 14px; bottom: 14px; z-index: 900; max-width: 390px; background: rgba(15, 23, 42, 0.92); border: 1px solid rgba(148, 163, 184, 0.35); border-top: 4px solid #D9381E; border-radius: 8px; padding: 12px 14px; box-shadow: 0 18px 40px rgba(0,0,0,0.35); }}
    .snap-title {{ font-weight: 800; font-size: 15px; margin-bottom: 4px; }}
    .snap-body {{ color: #cbd5e1; font-size: 13px; line-height: 1.45; }}
    .snap-actions {{ display: flex; align-items: center; gap: 8px; margin-top: 8px; }}
    .snap-actions button {{ height: 30px; min-width: 72px; background: #D9381E; border: 0; border-radius: 6px; color: #fff; font-size: 12px; font-weight: 700; }}
    .status-token {{ color: #e2e8f0; font-size: 12px; }}
    .endpoint {{ font-weight: 650; }}
    .leaflet-container {{ background: #111827; }}
    .leaflet-container.crosshair-mode {{ cursor: crosshair; }}
    .endpoint-x-marker {{ background: transparent; border: 0; }}
    .endpoint-x {{ display: flex; align-items: center; gap: 6px; pointer-events: none; }}
    .endpoint-cross {{ display: grid; place-items: center; width: 30px; height: 30px; border: 3px solid #ffffff; border-radius: 999px; background: #D9381E; color: #ffffff; font-size: 30px; line-height: 24px; font-weight: 900; box-shadow: 0 3px 12px rgba(15, 23, 42, 0.7); }}
    .endpoint-x.start .endpoint-cross {{ background: #16a34a; }}
    .endpoint-tag {{ display: block; padding: 4px 7px; border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; background: rgba(15, 23, 42, 0.94); color: #ffffff; font-size: 11px; font-weight: 800; letter-spacing: 0.02em; box-shadow: 0 3px 12px rgba(15, 23, 42, 0.42); }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="snap-panel">
    <div class="snap-title" id="snap-title">Vyberte uzavírku dvěma body</div>
    <div class="snap-body" id="snap-body">
      1) KLIK - START · 2) KLIK - END · body se přichytí na nejbližší road point.
      <div style="margin-top: 6px;" id="snap-status" class="status-token"></div>
      <div style="margin-top: 6px;" id="snap-meta" class="status-token"></div>
      <div class="snap-actions">
        <button type="button" id="clear-btn">Clear</button>
      </div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const payload = {payload};
    const map = L.map('map', {{ zoomControl: true }}).setView(payload.center, 13);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap'
    }}).addTo(map);
    map.getContainer().classList.add('crosshair-mode');

    const roadLayers = new Map();
    let selectedDetour = null;
    let selectedStart = null;
    let selectedEnd = null;
    let startMarker = null;
    let endMarker = null;
    let closureLayer = null;

    for (const road of payload.roads) {{
      const layer = L.polyline(road.coords, {{ color: '#0F0F0F', weight: 2, opacity: 0.46 }}).addTo(map);
      layer.bindTooltip(road.label || road.id, {{ sticky: true }});
      roadLayers.set(road.id, {{ road, layer }});
    }}

    function latLngDistanceKm(a, b) {{
      const toRad = value => value * Math.PI / 180;
      const earthR = 6371.0;
      const dLat = toRad(b.lat - a.lat);
      const dLon = toRad(b.lon - a.lon);
      const lat1 = toRad(a.lat);
      const lat2 = toRad(b.lat);
      const sinDLat = Math.sin(dLat / 2);
      const sinDLon = Math.sin(dLon / 2);
      const aa = sinDLat * sinDLat + Math.cos(lat1) * Math.cos(lat2) * sinDLon * sinDLon;
      return 2 * earthR * Math.asin(Math.min(1, Math.sqrt(aa)));
    }}

    function latLngToPair(latlng) {{
      return [latlng.lat, latlng.lng];
    }}

    function closestPointOnSegment(p, a, b) {{
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      if (dx === 0 && dy === 0) {{
        return {{ point: a, t: 0, distance: p.distanceTo(a) }};
      }}
      const rawT = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy);
      const t = Math.max(0, Math.min(1, rawT));
      const point = L.point(a.x + t * dx, a.y + t * dy);
      return {{ point, t, distance: p.distanceTo(point) }};
    }}

    function bringLayerGroupToFront(group) {{
      if (!group) return;
      group.eachLayer(layer => {{
        if (layer.bringToFront) layer.bringToFront();
      }});
    }}

    function clearSelection(showDefault = true) {{
      selectedStart = null;
      selectedEnd = null;
      if (startMarker) {{
        map.removeLayer(startMarker);
        startMarker = null;
      }}
      if (endMarker) {{
        map.removeLayer(endMarker);
        endMarker = null;
      }}
      if (selectedDetour) {{
        map.removeLayer(selectedDetour);
        selectedDetour = null;
      }}
      if (closureLayer) {{
        map.removeLayer(closureLayer);
        closureLayer = null;
      }}
      if (showDefault) {{
        document.getElementById('snap-status').textContent = 'START čeká na klik';
        document.getElementById('snap-meta').textContent = '';
      }}
    }}

    function drawEndpointMarker(position, kind) {{
      const label = kind === 'start' ? 'START' : 'END';
      const icon = L.divIcon({{
        className: 'endpoint-x-marker',
        html: `<div class="endpoint-x ${{kind}}"><span class="endpoint-cross">×</span><span class="endpoint-tag">${{label}}</span></div>`,
        iconSize: [92, 36],
        iconAnchor: [15, 15],
      }});
      const marker = L.marker(position, {{ icon, keyboard: false }}).addTo(map);
      if (kind === 'start') {{
        startMarker = marker;
      }} else {{
        endMarker = marker;
      }}
      return marker;
    }}

    function nearestRoadPoint(latlng) {{
      const px = map.latLngToLayerPoint(latlng);
      let best = null;
      for (const [roadId, entry] of roadLayers.entries()) {{
        const coords = entry.road.coords;
        if (coords.length === 1) {{
          const coord = coords[0];
          const p = map.latLngToLayerPoint(L.latLng(coord[0], coord[1]));
          const distance = px.distanceTo(p);
          if (best === null || distance < best.distance) {{
            best = {{
              roadId,
              road: entry.road,
              layer: entry.layer,
              index: 0,
              segmentIndex: 0,
              t: 0,
              position: 0,
              snappedPoint: L.latLng(coord[0], coord[1]),
              snapDistance: distance,
            }};
          }}
          continue;
        }}
        for (let index = 1; index < coords.length; index++) {{
          const aCoord = coords[index - 1];
          const bCoord = coords[index];
          const a = map.latLngToLayerPoint(L.latLng(aCoord[0], aCoord[1]));
          const b = map.latLngToLayerPoint(L.latLng(bCoord[0], bCoord[1]));
          const closest = closestPointOnSegment(px, a, b);
          if (best === null || closest.distance < best.distance) {{
            const snappedPoint = map.layerPointToLatLng(closest.point);
            best = {{
              roadId,
              road: entry.road,
              layer: entry.layer,
              index,
              segmentIndex: index - 1,
              t: closest.t,
              position: index - 1 + closest.t,
              snappedPoint,
              snapDistance: closest.distance,
            }};
          }}
        }}
      }}
      return best;
    }}

    function closureSegment(startSnap, endSnap) {{
      if (!startSnap || !endSnap || startSnap.roadId !== endSnap.roadId) return null;
      const points = startSnap.road.coords.map(point => ({{
        lat: point[0],
        lon: point[1],
      }}));
      if (points.length === 0) return null;
      if (Math.abs(startSnap.position - endSnap.position) < 0.0001) {{
        return [latLngToPair(startSnap.snappedPoint), latLngToPair(endSnap.snappedPoint)];
      }}
      const forward = startSnap.position <= endSnap.position;
      const first = forward ? startSnap : endSnap;
      const last = forward ? endSnap : startSnap;
      const segmentCoords = [latLngToPair(first.snappedPoint)];
      const firstVertex = Math.floor(first.position) + 1;
      const lastVertex = Math.floor(last.position);
      for (let index = firstVertex; index <= lastVertex; index++) {{
        if (index > 0 && index < points.length) {{
          segmentCoords.push([points[index].lat, points[index].lon]);
        }}
      }}
      segmentCoords.push(latLngToPair(last.snappedPoint));
      if (!forward) segmentCoords.reverse();
      return segmentCoords;
    }}

    function setPanelText(kind, text, meta) {{
      document.getElementById('snap-title').textContent = kind;
      document.getElementById('snap-status').textContent = text;
      document.getElementById('snap-meta').textContent = meta || '';
    }}

    function applyClosure() {{
      if (closureLayer) {{
        map.removeLayer(closureLayer);
        closureLayer = null;
      }}
      if (selectedDetour) {{
        map.removeLayer(selectedDetour);
        selectedDetour = null;
      }}
      if (!selectedStart || !selectedEnd) return;
      const segmentCoords = closureSegment(selectedStart, selectedEnd);
      if (segmentCoords && segmentCoords.length > 1) {{
        if (selectedStart.road.detour.length > 1) {{
          selectedDetour = L.polyline(selectedStart.road.detour, {{ color: '#4F7F5B', weight: 7, opacity: 0.92 }}).addTo(map);
          selectedDetour.bringToFront();
        }}
        closureLayer = L.layerGroup([
          L.polyline(segmentCoords, {{ color: '#ffffff', weight: 15, opacity: 0.96 }}),
          L.polyline(segmentCoords, {{ color: '#D9381E', weight: 10, opacity: 1 }}),
        ]).addTo(map);
        bringLayerGroupToFront(closureLayer);
        if (startMarker) startMarker.setZIndexOffset(1000);
        if (endMarker) endMarker.setZIndexOffset(1000);
        const startToEnd = latLngDistanceKm(selectedStart.snappedPoint, selectedEnd.snappedPoint).toFixed(2);
        const deltaStart = selectedStart.snapDistance.toFixed(1);
        const deltaEnd = selectedEnd.snapDistance.toFixed(1);
        setPanelText(
          'Úsek nalezen (stejná silnice)',
          `START: ${{selectedStart.road.label || selectedStart.road.id}} a END na stejné silnici.`,
          `Uzavřený kus: ${{(segmentCoords.length - 1)}} segmentů · delka úseku ${{startToEnd}} km · snap distance ${{deltaStart}}/${{deltaEnd}} px`
        );
      }} else {{
        const fallbackLine = [latLngToPair(selectedStart.snappedPoint), latLngToPair(selectedEnd.snappedPoint)];
        closureLayer = L.layerGroup([
          L.polyline(fallbackLine, {{ color: '#ffffff', weight: 15, opacity: 0.92, dashArray: '10,8' }}),
          L.polyline(fallbackLine, {{ color: '#D9381E', weight: 10, opacity: 0.95, dashArray: '10,8' }}),
        ]).addTo(map);
        bringLayerGroupToFront(closureLayer);
        if (startMarker) startMarker.setZIndexOffset(1000);
        if (endMarker) endMarker.setZIndexOffset(1000);
        const startToEnd = latLngDistanceKm(selectedStart.snappedPoint, selectedEnd.snappedPoint).toFixed(2);
        const deltaStart = selectedStart.snapDistance.toFixed(1);
        const deltaEnd = selectedEnd.snapDistance.toFixed(1);
        setPanelText(
          'Různé ulice - fallback režim',
          `Nejsou stejné OSM road_id: ${{selectedStart.road.label || selectedStart.road.id}} → ${{selectedEnd.road.label || selectedEnd.road.id}}. Prohlížecí podklad připojuje přímý segment.`,
          `Fyzická délka zóna: ${{startToEnd}} km · snap distance ${{deltaStart}}/${{deltaEnd}} px`
        );
      }}
    }}

    function handleMapClick(event) {{
      const snap = nearestRoadPoint(event.latlng);
      if (!snap) return;
      if (selectedStart && selectedEnd) {{
        clearSelection(false);
      }}
      if (!selectedStart) {{
        selectedStart = snap;
        drawEndpointMarker(snap.snappedPoint, 'start');
        setPanelText('START nastaven', 'Klikněte na bod END', `START: ${{snap.road.label || snap.road.id}}, index ${{snap.index}}, snap ${{Math.round(snap.snapDistance)}}px`);
        return;
      }}
      selectedEnd = snap;
      drawEndpointMarker(snap.snappedPoint, 'end');
      applyClosure();
    }}

    map.on('click', handleMapClick);
    document.getElementById('clear-btn').addEventListener('click', () => clearSelection(true));

    if (payload.roads.length) {{
      const bounds = L.latLngBounds(payload.roads.flatMap(r => r.coords));
      map.fitBounds(bounds.pad(0.18));
      clearSelection(false);
      setPanelText('Zvolte START bod', 'Klikněte pro START bod, poté END bod.', '');
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

    st.subheader("1. Klikněte START, 2. Klikněte END")
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
        osm_roads = cached_mlada_boleslav_roads()
        if not osm_roads:
            st.warning("OpenStreetMap roads could not be loaded, so the map is using the smaller demo road fan.")
        if osm_roads:
            st.caption(f"Načteno {len(osm_roads)} sjízdných OpenStreetMap geometrií v okně Mladé Boleslavi.")
            return render_osm_folium_picker(city, osm_roads, data)

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
    st.set_page_config(page_title="uzavírka.ai", page_icon=":construction:", layout="wide")
    apply_brand_styles()
    dataset = cached_data()
    external_summary = cached_external_summary()
    data = dataset.data

    render_brand_header()

    with st.sidebar:
        st.header("Plánovaná uzavírka")
        city = st.selectbox("Město / obec", CITY_OPTIONS, index=0)

        segment_selection = build_segment_options(data, city)
        if segment_selection.warning:
            st.warning(segment_selection.warning)
        day = st.selectbox("Den v týdnu", DAYS, index=0)
        start_hour = st.slider("Plánovaný začátek", 0, 23, 10)
        closure_mode_label = st.radio("Režim uzavírky", ["Krátkodobá", "Dlouhodobá"], horizontal=True)
        if closure_mode_label == "Krátkodobá":
            closure_days = 1
            active_hours_per_day = st.slider("Délka uzavírky v hodinách", 0.5, 12.0, 3.0, 0.5)
        else:
            closure_days = st.slider("Počet dní", 2, 90, 14)
            active_hours_per_day = st.slider("Aktivní hodin denně", 1.0, 24.0, 8.0, 0.5)
        duration_hours = float(closure_days) * float(active_hours_per_day)
        closure_type_label = st.selectbox("Typ uzavírky", list(CLOSURE_TYPE_LABELS.values()))
        closure_type = next(key for key, value in CLOSURE_TYPE_LABELS.items() if value == closure_type_label)
        affects_bus_route = st.checkbox("Ovlivní autobusovou linku", value=False)

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
        route_impact=route_impact if exact_map_segment else None,
        external_summary=external_summary,
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
    highlighted_reasons = reasons[reasons["Důvod"].isin(["Dopad objížďky", "Externí dopravní kontext"])]
    for _, reason in highlighted_reasons.iterrows():
        st.info(f"{reason['Důvod']}: +{reason['Příspěvek']} bodů. {reason['Vysvětlení']}")

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
    if closure_mode_label == "Dlouhodobá":
        st.caption(
            "Pro dlouhodobou uzavírku jde o predikci vhodnějších denních pracovních oken, "
            "ne o jednorázové schvalovací okno."
        )
    windows = recommend_better_windows(
        data,
        city=city,
        usek_id=str(segment_value) if exact_map_segment and segment_selection.exact_segment_match and segment_value is not None else None,
        day=day,
        duration_hours=active_hours_per_day,
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

    st.subheader("Predikce dopadu na dopravu")
    st.caption(
        "Predikce, ne garance. Rozsah nízký / očekávaný / vysoký vychází z nejistoty modelu. "
        "Používá agregovaná dopravní data a lokální graf, ne živou navigaci."
    )
    forecast_col_1, forecast_col_2, forecast_col_3, forecast_col_4 = st.columns(4)
    forecast_col_1.metric("Dotčené jízdy", f"{result.roi['affected_trips']:,.0f}".replace(",", " "))
    forecast_col_2.metric("Osobohodiny zdržení", f"{result.roi['person_delay_hours_base']:,.1f}".replace(",", " "))
    forecast_col_3.metric("Zdržení na jízdu", f"{result.roi['delay_minutes_per_trip']:.1f} min")
    forecast_col_4.metric("Spolehlivost predikce", f"{result.roi['forecast_confidence']:.0%}")
    forecast_range = pd.DataFrame(
        [
            {"Scénář": "Nízký odhad", "Osobohodiny zdržení": result.roi["person_delay_hours_low"]},
            {"Scénář": "Očekávaný odhad", "Osobohodiny zdržení": result.roi["person_delay_hours_base"]},
            {"Scénář": "Vysoký odhad", "Osobohodiny zdržení": result.roi["person_delay_hours_high"]},
        ]
    )
    st.dataframe(forecast_range, hide_index=True, width="stretch")
    st.info(
        f"Plánovací přínos: pokud načasování nebo opatření sníží dopad o 30 %, "
        f"predikce ukazuje přibližně {result.roi['avoidable_delay_hours_30pct']:,.1f} "
        "ušetřených osobohodin zdržení.".replace(",", " ")
    )
    st.caption(
        f"Rozsah uzavírky: {closure_days} dní × {active_hours_per_day:.1f} aktivních hodin denně. "
        "Dotčené jízdy = počet vozidel za hodinu × aktivní hodiny. "
        "Zdržení na jízdu je vyšší z hodnot: odhad podle třídy rizika nebo přidaný čas z mapové simulace."
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
        st.markdown("**Use of external ZIP data**")
        st.write(
            "ZIP soubory z Dopravního portálu jsou použity jako volitelný regionální kontext. "
            "Nejsou brány jako historické výsledky uzavírek."
        )
        zip_cols = st.columns(4)
        zip_cols[0].metric("ZIP načteno", external_summary.zip_count_loaded)
        zip_cols[1].metric("Odhad záznamů", f"{external_summary.total_record_count_estimate:,}".replace(",", " "))
        zip_cols[2].metric("Kontext", external_summary.external_context_level)
        zip_cols[3].metric("Úprava skóre", f"+{external_summary.risk_adjustment}")
        st.write(f"Soubory: {', '.join(external_summary.zip_files) if external_summary.zip_files else 'žádné'}")
        st.write(f"Datumový rozsah: {external_summary.date_range[0] or '?'} až {external_summary.date_range[1] or '?'}")
        if external_summary.parsed_files:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Parsed files": external_summary.parsed_files,
                    }
                ),
                hide_index=True,
                width="stretch",
            )
        if external_summary.detected_columns_or_keys:
            st.caption("Detekované sloupce/klíče: " + ", ".join(external_summary.detected_columns_or_keys[:20]))
        if external_summary.reason:
            st.info(external_summary.reason)
        if external_summary.warnings:
            st.warning("Varování ZIP: " + " | ".join(external_summary.warnings[:5]))

    st.subheader("Etická poznámka")
    st.write(
        "Toto MVP používá pouze agregovaná dopravní a obecní kontextová data. Nesleduje jednotlivce. "
        "AI skóre je poradní, zobrazuje spolehlivost odhadu a případy s nedostatkem dat mají projít ruční kontrolou úředníka."
    )


if __name__ == "__main__":
    main()
