import unittest

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
        self.assertGreaterEqual(result.roi["estimated_social_loss_czk"], 0)

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


if __name__ == "__main__":
    unittest.main()
