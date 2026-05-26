import unittest
from types import SimpleNamespace

import pandas as pd

from risk_model import (
    baseline_class,
    classify_risk,
    recommend_better_windows,
    score_closure,
)


class RiskModelTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame(
            [
                {
                    "usek_id": "U01",
                    "obec": "Mladá Boleslav",
                    "den_v_tydnu": "Po",
                    "hodina": 7,
                    "pocet_vozidel_h": 520,
                    "prum_rychlost_kmh": 22,
                    "index_plynulosti_0_100": 42,
                    "riziko_kolize_0_100": 72,
                    "spoju_den": 94,
                    "kapacita_pr": 271,
                    "dojizdejici_denne": 7920,
                },
                {
                    "usek_id": "U01",
                    "obec": "Mladá Boleslav",
                    "den_v_tydnu": "Po",
                    "hodina": 11,
                    "pocet_vozidel_h": 150,
                    "prum_rychlost_kmh": 40,
                    "index_plynulosti_0_100": 76,
                    "riziko_kolize_0_100": 30,
                    "spoju_den": 94,
                    "kapacita_pr": 271,
                    "dojizdejici_denne": 7920,
                },
            ]
        )

    def test_score_closure_returns_class_recommendation_and_reasons(self):
        result = score_closure(
            self.rows.iloc[0],
            duration_hours=4,
            closure_type="full_road_closure",
            affects_bus_route=True,
        )

        self.assertGreaterEqual(result.score, 61)
        self.assertIn(result.risk_class, {"HIGH", "CRITICAL"})
        self.assertTrue(result.recommendation)
        self.assertGreaterEqual(len(result.reasons), 3)
        self.assertGreaterEqual(result.roi["affected_trips"], 0)
        self.assertNotIn("estimated_social_loss_czk", result.roi)
        self.assertNotIn("value_of_time_czk_h", result.roi)

    def test_baseline_only_uses_peak_hour(self):
        self.assertEqual(baseline_class(7), "HIGH")
        self.assertEqual(baseline_class(11), "LOW")

    def test_recommend_better_windows_prefers_lower_risk_hours(self):
        options = recommend_better_windows(
            self.rows,
            city="Mladá Boleslav",
            usek_id="U01",
            day="Po",
            duration_hours=2,
            closure_type="partial_lane_closure",
            affects_bus_route=False,
            limit=2,
        )

        self.assertEqual(options[0]["start_hour"], 11)
        self.assertLess(options[0]["score"], 61)

    def test_classify_risk_boundaries(self):
        self.assertEqual(classify_risk(30), "LOW")
        self.assertEqual(classify_risk(31), "MEDIUM")
        self.assertEqual(classify_risk(61), "HIGH")
        self.assertEqual(classify_risk(81), "CRITICAL")

    def test_external_context_adjustment_is_capped_at_five_points(self):
        baseline = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False)
        summary = SimpleNamespace(risk_adjustment=99, reason="External context high.")

        result = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False, external_summary=summary)

        self.assertLessEqual(result.score - baseline.score, 5)
        self.assertTrue(any(reason.name == "Externí dopravní kontext" for reason in result.reasons))

    def test_network_impact_adjustment_is_capped_at_ten_points(self):
        baseline = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False)
        impact = SimpleNamespace(extra_time_min=99, unreachable_share=1)

        result = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False, route_impact=impact)

        self.assertLessEqual(result.score - baseline.score, 10)
        self.assertGreaterEqual(result.roi["delay_minutes_per_trip"], 99)
        self.assertTrue(any(reason.name == "Dopad objížďky" for reason in result.reasons))

    def test_risk_model_works_when_external_summary_is_missing(self):
        result = score_closure(self.rows.iloc[0], 4, "full_road_closure", True, external_summary=None)

        self.assertGreaterEqual(result.score, 0)
        self.assertTrue(result.reasons)

    def test_long_term_duration_scales_traffic_forecast(self):
        short = score_closure(self.rows.iloc[1], 8, "partial_lane_closure", False)
        long = score_closure(self.rows.iloc[1], 14 * 8, "partial_lane_closure", False)

        self.assertAlmostEqual(long.roi["affected_trips"], short.roi["affected_trips"] * 14)
        self.assertGreater(long.roi["person_delay_hours_base"], short.roi["person_delay_hours_base"])
        self.assertLess(long.score, 100)

    def test_forecast_range_is_ordered(self):
        result = score_closure(self.rows.iloc[1], 6, "partial_lane_closure", False)

        self.assertLessEqual(result.roi["person_delay_hours_low"], result.roi["person_delay_hours_base"])
        self.assertLessEqual(result.roi["person_delay_hours_base"], result.roi["person_delay_hours_high"])

    def test_route_extra_distance_increases_vehicle_km_forecast(self):
        baseline = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False)
        impact = SimpleNamespace(extra_time_min=3, extra_distance_km=4, unreachable_share=0)

        result = score_closure(self.rows.iloc[1], 2, "partial_lane_closure", False, route_impact=impact)

        self.assertEqual(baseline.roi["extra_vehicle_km"], 0)
        self.assertGreater(result.roi["extra_vehicle_km"], 0)


if __name__ == "__main__":
    unittest.main()
