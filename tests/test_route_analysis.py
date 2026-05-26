import unittest

import pandas as pd

from route_analysis import (
    SnappedRoadPoint,
    analyze_closure,
    analyze_osm_closure,
    build_road_network,
    osm_closure_path,
    path_layer_data,
    snap_osm_road_point,
    update_snap_clicks,
)


class RouteAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = pd.DataFrame(
            [
                {
                    "obec": "Kladno",
                    "usek_id": "U02",
                    "pocet_vozidel_h": 640,
                    "prum_rychlost_kmh": 28,
                    "index_plynulosti_0_100": 42,
                }
            ]
        )
        self.network = build_road_network("Kladno", ["U02"], (50.1431, 14.1052), self.data, points_per_route=5)

    def test_build_road_network_keeps_clickable_closure_points_and_support_edges(self):
        self.assertEqual(set(self.network.points["segment_label"]), {"U02"})
        self.assertEqual(self.network.points.groupby("segment_label").size().to_dict(), {"U02": 5})

        paths = path_layer_data(self.network)

        self.assertIn("closure", set(paths["road_role"]))
        self.assertIn("detour_north", set(paths["road_role"]))
        self.assertIn("detour_south", set(paths["road_role"]))
        self.assertTrue(paths["path"].apply(lambda path: len(path) >= 2).all())

    def test_closing_segment_returns_detour_metrics_and_valid_paths(self):
        impact = analyze_closure(self.network, "U02")

        self.assertGreaterEqual(impact.affected_edges, 2)
        self.assertGreater(impact.extra_distance_km, 0)
        self.assertGreater(impact.extra_time_min, 0)
        self.assertEqual(impact.unreachable_share, 0)
        self.assertFalse(impact.detour_routes.empty)
        self.assertTrue(impact.detour_routes["path"].apply(lambda path: len(path) >= 3).all())

    def test_unknown_closed_segment_returns_empty_impact(self):
        impact = analyze_closure(self.network, "missing")

        self.assertEqual(impact.affected_edges, 0)
        self.assertEqual(impact.extra_distance_km, 0)
        self.assertTrue(impact.detour_routes.empty)

    def test_osm_snap_selects_nearest_road_point(self):
        roads = [{"id": "r1", "name": "Main", "coords": [[50.0, 14.0], [50.0, 14.01]]}]

        snap = snap_osm_road_point(roads, 50.0001, 14.0098)

        self.assertIsNotNone(snap)
        self.assertEqual(snap.road_id, "r1")
        self.assertEqual(snap.index, 1)

    def test_osm_closure_path_requires_same_road(self):
        roads = [
            {"id": "r1", "name": "Main", "coords": [[50.0, 14.0], [50.0, 14.01], [50.0, 14.02]]},
            {"id": "r2", "name": "Side", "coords": [[50.01, 14.0], [50.01, 14.01]]},
        ]
        start = SnappedRoadPoint("r1", "Main", 0, 50.0, 14.0, 0)
        end = SnappedRoadPoint("r1", "Main", 2, 50.0, 14.02, 0)
        other = SnappedRoadPoint("r2", "Side", 1, 50.01, 14.01, 0)

        self.assertEqual(len(osm_closure_path(roads, start, end)), 3)
        self.assertEqual(osm_closure_path(roads, start, other), [])

    def test_snap_click_sequence_resets_on_third_click(self):
        one = SnappedRoadPoint("r1", "Main", 0, 50.0, 14.0, 0)
        two = SnappedRoadPoint("r1", "Main", 1, 50.0, 14.01, 0)
        three = SnappedRoadPoint("r1", "Main", 2, 50.0, 14.02, 0)

        clicks = update_snap_clicks([], one)
        clicks = update_snap_clicks(clicks, two)
        clicks = update_snap_clicks(clicks, three)

        self.assertEqual(clicks, [three])

    def test_osm_closure_returns_detour_metrics(self):
        roads = [
            {"id": "main", "name": "Main", "coords": [[50.0, 14.0], [50.0, 14.01]]},
            {"id": "north-a", "name": "North A", "coords": [[50.0, 14.0], [50.01, 14.0]]},
            {"id": "north-b", "name": "North B", "coords": [[50.01, 14.0], [50.01, 14.01]]},
            {"id": "north-c", "name": "North C", "coords": [[50.01, 14.01], [50.0, 14.01]]},
        ]
        start = SnappedRoadPoint("main", "Main", 0, 50.0, 14.0, 0)
        end = SnappedRoadPoint("main", "Main", 1, 50.0, 14.01, 0)

        impact = analyze_osm_closure(roads, start, end, self.data, "Kladno")

        self.assertGreater(impact.extra_distance_km, 0)
        self.assertGreater(impact.extra_time_min, 0)
        self.assertFalse(impact.detour_routes.empty)
        self.assertEqual(impact.unreachable_share, 0)

    def test_osm_closure_reports_unreachable_without_crashing(self):
        roads = [{"id": "main", "name": "Main", "coords": [[50.0, 14.0], [50.0, 14.01]]}]
        start = SnappedRoadPoint("main", "Main", 0, 50.0, 14.0, 0)
        end = SnappedRoadPoint("main", "Main", 1, 50.0, 14.01, 0)

        impact = analyze_osm_closure(roads, start, end, self.data, "Kladno")

        self.assertEqual(impact.unreachable_share, 1)
        self.assertTrue(impact.detour_routes.empty)


if __name__ == "__main__":
    unittest.main()
