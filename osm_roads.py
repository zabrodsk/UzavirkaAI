from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


MLADA_BOLESLAV_BBOX = (50.378, 14.835, 50.455, 14.985)
OSM_CACHE = Path(__file__).parent / "data" / "mlada_boleslav_osm_roads.json"
ROAD_HIGHWAY_EXCLUDES = {
    "bridleway",
    "bus_stop",
    "construction",
    "corridor",
    "cycleway",
    "elevator",
    "footway",
    "path",
    "pedestrian",
    "platform",
    "proposed",
    "raceway",
    "service",
    "steps",
    "track",
}


def load_mlada_boleslav_roads(max_age_hours: int = 168) -> list[dict]:
    cached = _read_cache(max_age_hours)
    if cached:
        return cached
    try:
        roads = _fetch_overpass_roads()
    except (HTTPError, TimeoutError, URLError, OSError, json.JSONDecodeError):
        return []
    if roads:
        _write_cache(roads)
    return roads


def _fetch_overpass_roads() -> list[dict]:
    south, west, north, east = MLADA_BOLESLAV_BBOX
    query = f"""
[out:json][timeout:25];
(
  way["highway"]({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""
    body = urlencode({"data": query}).encode("utf-8")
    payload = None
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            request = Request(url, data=body, headers={"User-Agent": "UzavirkaAI/0.1"})
            with urlopen(request, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (HTTPError, TimeoutError, URLError, OSError):
            payload = None
    if payload is None:
        return []
    return _parse_overpass(payload)


def _parse_overpass(payload: dict) -> list[dict]:
    nodes = {
        element["id"]: (float(element["lat"]), float(element["lon"]))
        for element in payload.get("elements", [])
        if element.get("type") == "node" and "lat" in element and "lon" in element
    }
    roads = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway")
        if highway in ROAD_HIGHWAY_EXCLUDES:
            continue
        coords = [nodes[node_id] for node_id in element.get("nodes", []) if node_id in nodes]
        if len(coords) < 2:
            continue
        name = tags.get("name") or tags.get("ref") or f"OSM way {element['id']}"
        roads.append(
            {
                "id": f"osm-{element['id']}",
                "name": str(name),
                "highway": str(highway),
                "coords": [[lat, lon] for lat, lon in coords],
            }
        )
    roads.sort(key=lambda item: (item["name"], item["id"]))
    return roads


def _read_cache(max_age_hours: int) -> list[dict]:
    if not OSM_CACHE.exists():
        return []
    try:
        payload = json.loads(OSM_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    fetched_at = float(payload.get("fetched_at", 0))
    if time.time() - fetched_at > max_age_hours * 3600:
        return []
    roads = payload.get("roads")
    return roads if isinstance(roads, list) else []


def _write_cache(roads: list[dict]) -> None:
    OSM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    OSM_CACHE.write_text(json.dumps({"fetched_at": time.time(), "roads": roads}, ensure_ascii=False), encoding="utf-8")
