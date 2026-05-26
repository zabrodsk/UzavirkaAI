from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt
from typing import Any

import networkx as nx
import pandas as pd


SUPPORT_EDGE_ROLES = {"detour_north", "detour_south", "connector"}
MLADA_BOLESLAV = "Mladá Boleslav"
MLADA_BOLESLAV_FAN_SEGMENTS = [
    "U01 Václava Klementa",
    "U01 Kosmonoská",
    "U01 Jičínská",
    "U01 Ptácká",
    "U01 Pražská",
    "U01 Nádražní",
    "U01 Laurinova",
    "U01 U Stadionu",
]

MLADA_BOLESLAV_FAN_PATHS = {
    "U01 Václava Klementa": [(50.4100, 14.8848), (50.4108, 14.8945), (50.4117, 14.9044), (50.4132, 14.9168), (50.4146, 14.9278)],
    "U01 Kosmonoská": [(50.4117, 14.9044), (50.4160, 14.9015), (50.4213, 14.8999), (50.4285, 14.8991), (50.4360, 14.8972)],
    "U01 Jičínská": [(50.4117, 14.9044), (50.4164, 14.9114), (50.4212, 14.9204), (50.4280, 14.9328), (50.4347, 14.9448)],
    "U01 Ptácká": [(50.4117, 14.9044), (50.4076, 14.8970), (50.4044, 14.8873), (50.4012, 14.8755), (50.3981, 14.8645)],
    "U01 Pražská": [(50.4117, 14.9044), (50.4062, 14.9088), (50.3988, 14.9136), (50.3918, 14.9194), (50.3849, 14.9254)],
    "U01 Nádražní": [(50.4117, 14.9044), (50.4090, 14.9126), (50.4069, 14.9224), (50.4045, 14.9343), (50.4023, 14.9460)],
    "U01 Laurinova": [(50.4117, 14.9044), (50.4142, 14.9098), (50.4176, 14.9154), (50.4209, 14.9208), (50.4252, 14.9273)],
    "U01 U Stadionu": [(50.4117, 14.9044), (50.4135, 14.8975), (50.4144, 14.8895), (50.4166, 14.8807), (50.4190, 14.8722)],
}


@dataclass(frozen=True)
class RoadNetwork:
    city: str
    points: pd.DataFrame
    edges: pd.DataFrame


@dataclass(frozen=True)
class RouteImpact:
    closed_segment: str
    affected_edges: int
    extra_distance_km: float
    extra_time_min: float
    unreachable_share: float
    closure_edges: pd.DataFrame
    impacted_edges: pd.DataFrame
    detour_routes: pd.DataFrame


def build_road_network(
    city: str,
    segment_labels: list[str],
    center: tuple[float, float],
    traffic_data: pd.DataFrame | None = None,
    points_per_route: int = 9,
) -> RoadNetwork:
    rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    center_lat, center_lon = center
    points_per_route = max(2, points_per_route)

    labels = segment_labels or ["Segment 1"]
    if city == MLADA_BOLESLAV and labels == ["U01"]:
        labels = MLADA_BOLESLAV_FAN_SEGMENTS
    for index, label in enumerate(labels):
        route_id = f"route-{index + 1}"
        stats = _segment_stats(traffic_data, city, label)

        if city == MLADA_BOLESLAV and label in MLADA_BOLESLAV_FAN_PATHS:
            main_path = MLADA_BOLESLAV_FAN_PATHS[label]
            start = main_path[0]
            end = main_path[-1]
            mid = main_path[len(main_path) // 2]
            normal = _normal_offsets(start, end, 0.010)
            nodes = {
                "w": start,
                "e": end,
                "nw": (mid[0] + normal[0], mid[1] + normal[1]),
                "ne": (end[0] + normal[0], end[1] + normal[1]),
                "sw": (mid[0] - normal[0], mid[1] - normal[1]),
                "se": (end[0] - normal[0], end[1] - normal[1]),
            }
        else:
            shift_lat = 0.0 if len(labels) == 1 else ((index % 3) - 1) * 0.010
            shift_lon = (index // 3) * 0.014
            base_lat = center_lat + shift_lat
            base_lon = center_lon + shift_lon
            nodes = {
                "w": (base_lat, base_lon - 0.032),
                "e": (base_lat, base_lon + 0.032),
                "nw": (base_lat + 0.016, base_lon - 0.018),
                "ne": (base_lat + 0.016, base_lon + 0.018),
                "sw": (base_lat - 0.016, base_lon - 0.018),
                "se": (base_lat - 0.016, base_lon + 0.018),
            }
            main_path = _interpolate(nodes["w"], nodes["e"], points_per_route)

        rows.extend(_snap_rows(route_id, label, main_path))
        edges.append(_edge(route_id, label, "closure", "w", "e", main_path, stats))

        support_specs = [
            ("detour_north", "w", "nw", [nodes["w"], nodes["nw"]]),
            ("detour_north", "nw", "ne", _interpolate(nodes["nw"], nodes["ne"], max(3, points_per_route // 2))),
            ("detour_north", "ne", "e", [nodes["ne"], nodes["e"]]),
            ("detour_south", "w", "sw", [nodes["w"], nodes["sw"]]),
            ("detour_south", "sw", "se", _interpolate(nodes["sw"], nodes["se"], max(3, points_per_route // 2))),
            ("detour_south", "se", "e", [nodes["se"], nodes["e"]]),
            ("connector", "nw", "sw", [nodes["nw"], nodes["sw"]]),
            ("connector", "ne", "se", [nodes["ne"], nodes["se"]]),
        ]
        for role, source, target, path in support_specs:
            edges.append(_edge(route_id, label, role, source, target, path, stats))

    return RoadNetwork(city=city, points=pd.DataFrame(rows), edges=pd.DataFrame(edges))


def path_layer_data(network: RoadNetwork, roles: set[str] | None = None) -> pd.DataFrame:
    edges = network.edges
    if roles is not None:
        edges = edges[edges["road_role"].isin(roles)]
    if edges.empty:
        return pd.DataFrame(columns=["route_id", "segment_label", "road_role", "path"])
    return edges[["edge_id", "route_id", "segment_label", "road_role", "path", "distance_km", "travel_time_min"]].copy()


def analyze_closure(network: RoadNetwork, closed_segment: str | None) -> RouteImpact:
    if not closed_segment or network.edges.empty:
        return _empty_impact(closed_segment or "")

    graph = _graph_from_edges(network.edges)
    closed_edges = network.edges[
        (network.edges["segment_label"].astype(str) == str(closed_segment))
        & (network.edges["road_role"] == "closure")
    ]
    if closed_edges.empty:
        return _empty_impact(closed_segment)

    impacted: list[dict[str, Any]] = []
    detours: list[dict[str, Any]] = []
    unreachable = 0
    total_pairs = 0
    extra_distance = 0.0
    extra_time = 0.0

    for _, closed in closed_edges.iterrows():
        total_pairs += 1
        source = str(closed["source"])
        target = str(closed["target"])
        try:
            baseline_nodes = nx.shortest_path(graph, source, target, weight="travel_time_min")
            baseline_edges = _edges_for_node_path(graph, baseline_nodes)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            baseline_edges = []

        graph_without_closure = graph.copy()
        graph_without_closure.remove_edge(source, target)
        try:
            detour_nodes = nx.shortest_path(graph_without_closure, source, target, weight="travel_time_min")
            detour_edge_rows = _edges_for_node_path(graph_without_closure, detour_nodes)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            unreachable += 1
            continue

        baseline_distance = sum(float(edge["distance_km"]) for edge in baseline_edges) or float(closed["distance_km"])
        baseline_time = sum(float(edge["travel_time_min"]) for edge in baseline_edges) or float(closed["travel_time_min"])
        detour_distance = sum(float(edge["distance_km"]) for edge in detour_edge_rows)
        detour_time = sum(float(edge["travel_time_min"]) for edge in detour_edge_rows)

        extra_distance += max(0.0, detour_distance - baseline_distance)
        extra_time += max(0.0, detour_time - baseline_time)
        detour_path = _join_paths([edge["path"] for edge in detour_edge_rows])
        detours.append(
            {
                "route_id": closed["route_id"],
                "segment_label": closed_segment,
                "path": detour_path,
                "extra_distance_km": round(max(0.0, detour_distance - baseline_distance), 2),
                "extra_time_min": round(max(0.0, detour_time - baseline_time), 1),
            }
        )
        impacted.extend(detour_edge_rows)

    impacted_frame = _dedupe_edges(pd.DataFrame(impacted))
    return RouteImpact(
        closed_segment=closed_segment,
        affected_edges=int(len(impacted_frame)),
        extra_distance_km=round(extra_distance, 2),
        extra_time_min=round(extra_time, 1),
        unreachable_share=round(unreachable / total_pairs, 2) if total_pairs else 0.0,
        closure_edges=closed_edges.copy(),
        impacted_edges=impacted_frame,
        detour_routes=pd.DataFrame(detours),
    )


def _edge(
    route_id: str,
    segment_label: str,
    role: str,
    source: str,
    target: str,
    lat_lon_path: list[tuple[float, float]],
    stats: dict[str, float],
) -> dict[str, Any]:
    path = [[lon, lat] for lat, lon in lat_lon_path]
    distance_km = _path_distance_km(lat_lon_path)
    flow_factor = 1 + max(0.0, 70 - stats["flow_index"]) / 100
    volume_factor = 1 + min(0.45, stats["vehicles_h"] / 2400)
    role_factor = 0.78 if role == "closure" else 1.05
    speed_kmh = max(10.0, stats["speed_kmh"] / flow_factor / volume_factor * role_factor)
    return {
        "edge_id": f"{route_id}:{role}:{source}-{target}",
        "route_id": route_id,
        "segment_label": segment_label,
        "road_role": role,
        "source": f"{route_id}:{source}",
        "target": f"{route_id}:{target}",
        "path": path,
        "distance_km": round(distance_km, 3),
        "travel_time_min": round(distance_km / speed_kmh * 60, 2),
    }


def _snap_rows(route_id: str, label: str, path: list[tuple[float, float]]) -> list[dict[str, Any]]:
    return [
        {
            "route_id": route_id,
            "segment_label": label,
            "snap_id": f"{label}:{index}",
            "lat": lat,
            "lon": lon,
            "order": index,
            "point_role": "snap",
        }
        for index, (lat, lon) in enumerate(path)
    ]


def _segment_stats(traffic_data: pd.DataFrame | None, city: str, label: str) -> dict[str, float]:
    defaults = {"speed_kmh": 38.0, "flow_index": 62.0, "vehicles_h": 360.0}
    if traffic_data is None or traffic_data.empty:
        return defaults

    rows = traffic_data
    if "obec" in rows.columns:
        city_rows = rows[rows["obec"].astype(str) == str(city)]
        if not city_rows.empty:
            rows = city_rows
    if "usek_id" in rows.columns:
        segment_rows = rows[rows["usek_id"].astype(str) == str(label)]
        if not segment_rows.empty:
            rows = segment_rows

    return {
        "speed_kmh": _median_or_default(rows, "prum_rychlost_kmh", defaults["speed_kmh"]),
        "flow_index": _median_or_default(rows, "index_plynulosti_0_100", defaults["flow_index"]),
        "vehicles_h": _median_or_default(rows, "pocet_vozidel_h", defaults["vehicles_h"]),
    }


def _median_or_default(frame: pd.DataFrame, column: str, default: float) -> float:
    if column not in frame.columns:
        return default
    value = pd.to_numeric(frame[column], errors="coerce").median()
    return default if pd.isna(value) else float(value)


def _graph_from_edges(edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for _, row in edges.iterrows():
        attributes = row.to_dict()
        graph.add_edge(str(row["source"]), str(row["target"]), **attributes)
        reverse = attributes.copy()
        reverse["source"] = str(row["target"])
        reverse["target"] = str(row["source"])
        reverse["path"] = list(reversed(attributes["path"]))
        graph.add_edge(str(row["target"]), str(row["source"]), **reverse)
    return graph


def _edges_for_node_path(graph: nx.DiGraph, nodes: list[str]) -> list[dict[str, Any]]:
    rows = []
    for source, target in zip(nodes, nodes[1:]):
        rows.append(graph[source][target].copy())
    return rows


def _dedupe_edges(edges: pd.DataFrame) -> pd.DataFrame:
    if edges.empty:
        return pd.DataFrame(columns=["edge_id", "route_id", "segment_label", "road_role", "path", "distance_km", "travel_time_min"])
    return edges.drop_duplicates("edge_id")[
        ["edge_id", "route_id", "segment_label", "road_role", "path", "distance_km", "travel_time_min"]
    ].reset_index(drop=True)


def _join_paths(paths: list[list[list[float]]]) -> list[list[float]]:
    joined: list[list[float]] = []
    for path in paths:
        if not path:
            continue
        if joined and joined[-1] == path[0]:
            joined.extend(path[1:])
        else:
            joined.extend(path)
    return joined


def _interpolate(start: tuple[float, float], end: tuple[float, float], count: int) -> list[tuple[float, float]]:
    return [
        (start[0] + (end[0] - start[0]) * index / (count - 1), start[1] + (end[1] - start[1]) * index / (count - 1))
        for index in range(count)
    ]


def _normal_offsets(start: tuple[float, float], end: tuple[float, float], size: float) -> tuple[float, float]:
    dlat = end[0] - start[0]
    dlon = end[1] - start[1]
    length = sqrt(dlat * dlat + dlon * dlon)
    if length == 0:
        return size, 0
    return -dlon / length * size, dlat / length * size


def _path_distance_km(path: list[tuple[float, float]]) -> float:
    return sum(_distance_km(path[index - 1], path[index]) for index in range(1, len(path)))


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    radius_km = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    hav = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius_km * atan2(sqrt(hav), sqrt(1 - hav))


def _empty_impact(closed_segment: str) -> RouteImpact:
    empty = pd.DataFrame(columns=["edge_id", "route_id", "segment_label", "road_role", "path", "distance_km", "travel_time_min"])
    return RouteImpact(
        closed_segment=closed_segment,
        affected_edges=0,
        extra_distance_km=0.0,
        extra_time_min=0.0,
        unreachable_share=0.0,
        closure_edges=empty,
        impacted_edges=empty,
        detour_routes=pd.DataFrame(columns=["route_id", "segment_label", "path", "extra_distance_km", "extra_time_min"]),
    )
