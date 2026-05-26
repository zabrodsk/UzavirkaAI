import unittest

import pandas as pd

from app import (
    CITY_OPTIONS,
    build_segment_options,
    choose_row,
    confidence_for_selection,
    get_selected_snap_ids,
    road_path_data,
    road_network_data,
    selected_segment_from_snap_points,
    segment_value_from_label,
)


class AppSelectionTests(unittest.TestCase):
    def test_city_options_are_fixed_demo_cities(self):
        self.assertEqual(
            CITY_OPTIONS,
            ["Mladá Boleslav", "Kladno", "Kolín", "Příbram", "Beroun", "Mělník"],
        )

    def test_build_segment_options_filters_by_city_when_possible(self):
        data = pd.DataFrame(
            [
                {"obec": "Mladá Boleslav", "usek_id": "U01"},
                {"obec": "Kladno", "usek_id": "U02"},
                {"obec": "Kladno", "usek_id": "U03"},
            ]
        )

        selection = build_segment_options(data, "Kladno")

        self.assertEqual(selection.options, ["U02", "U03"])
        self.assertFalse(selection.used_fallback)

    def test_build_segment_options_falls_back_to_all_segments_for_missing_city(self):
        data = pd.DataFrame(
            [
                {"obec": "Beroun", "usek_id": "U06"},
                {"obec": "Mělník", "usek_id": "U03"},
            ]
        )

        selection = build_segment_options(data, "Kolín")

        self.assertEqual(selection.options, ["U03", "U06"])
        self.assertTrue(selection.used_fallback)
        self.assertIn("No exact city match", selection.warning)

    def test_build_segment_options_uses_index_labels_without_usek_id(self):
        data = pd.DataFrame(
            [
                {"obec": "Kladno", "hodina": 7},
                {"obec": "Kladno", "hodina": 8},
            ]
        )

        selection = build_segment_options(data, "Kladno")

        self.assertEqual(selection.options, ["Segment 1", "Segment 2"])
        self.assertEqual(segment_value_from_label(selection, "Segment 2"), 1)

    def test_choose_row_uses_selected_segment_and_survives_missing_columns(self):
        data = pd.DataFrame(
            [
                {"hodina": 7, "pocet_vozidel_h": 100},
                {"hodina": 9, "pocet_vozidel_h": 300},
            ]
        )

        row = choose_row(data, city="Kladno", segment_value=1, day="Po", hour=8)

        self.assertEqual(row["pocet_vozidel_h"], 300)

    def test_confidence_is_lower_for_fallback_selection(self):
        self.assertGreater(confidence_for_selection(True, True), confidence_for_selection(False, True))
        self.assertGreater(confidence_for_selection(True, True), confidence_for_selection(True, False))

    def test_road_network_data_builds_clickable_snap_points(self):
        routes = road_network_data("Kladno", ["U02", "U03"], points_per_route=5)

        self.assertEqual(set(routes["segment_label"]), {"U02", "U03"})
        self.assertEqual(set(routes["point_role"]), {"snap"})
        self.assertEqual(routes.groupby("segment_label").size().to_dict(), {"U02": 5, "U03": 5})
        self.assertTrue({"lat", "lon", "order", "snap_id"}.issubset(routes.columns))

    def test_road_path_data_builds_full_map_line_paths(self):
        points = road_network_data("Kladno", ["U02"], points_per_route=5)
        paths = road_path_data(points)

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths.loc[0, "segment_label"], "U02")
        self.assertEqual(len(paths.loc[0, "path"]), 5)

    def test_get_selected_snap_ids_handles_streamlit_selection_shapes(self):
        event = {"selection": {"road_snap_select": [{"snap_id": "U02:1"}, {"snap_id": "U02:4"}]}}
        self.assertEqual(get_selected_snap_ids(event), ["U02:1", "U02:4"])

        event = {"selection": {"road_snap_select": {"snap_id": ["U03:2", "U03:5"]}}}
        self.assertEqual(get_selected_snap_ids(event), ["U03:2", "U03:5"])

        event = {"selection": {"objects": {"snap-points": [{"snap_id": "U04:1"}, {"snap_id": "U04:3"}]}}}
        self.assertEqual(get_selected_snap_ids(event), ["U04:1", "U04:3"])

        self.assertEqual(get_selected_snap_ids({"selection": {}}), [])

    def test_selected_segment_from_two_snapped_points(self):
        network = road_network_data("Kladno", ["U02", "U03"], points_per_route=5)

        self.assertEqual(selected_segment_from_snap_points(network, ["U02:1", "U02:4"]), "U02")
        self.assertIsNone(selected_segment_from_snap_points(network, ["U02:1"]))
        self.assertIsNone(selected_segment_from_snap_points(network, ["U02:1", "U03:3"]))


if __name__ == "__main__":
    unittest.main()
