import unittest

import pandas as pd

from route_analysis import analyze_closure, build_road_network, path_layer_data


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


if __name__ == "__main__":
    unittest.main()
