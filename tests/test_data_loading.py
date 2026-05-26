import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data_loading import load_csv_robust, load_project_data


class DataLoadingTests(unittest.TestCase):
    def test_load_csv_robust_handles_semicolon_decimal_comma_and_medians(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text(
                "obec;pocet_vozidel_h;index_plynulosti_0_100;kapacita_pr\n"
                "Kladno;120;43,6;385\n"
                "Beroun;;66.4;\n",
                encoding="utf-8",
            )

            df, report = load_csv_robust(path)

        self.assertEqual(list(df.columns), ["obec", "pocet_vozidel_h", "index_plynulosti_0_100", "kapacita_pr"])
        self.assertEqual(float(df.loc[0, "index_plynulosti_0_100"]), 43.6)
        self.assertEqual(float(df.loc[1, "pocet_vozidel_h"]), 120.0)
        self.assertEqual(float(df.loc[1, "kapacita_pr"]), 385.0)
        self.assertEqual(report["rows"], 2)
        self.assertGreaterEqual(report["numeric_filled"], 2)

    def test_load_project_data_prefers_segment_data_and_adds_city_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "01_provoz_useky_gps.csv").write_text(
                "usek_id,obec,den_v_tydnu,hodina,pocet_vozidel_h,prum_rychlost_kmh,index_plynulosti_0_100,riziko_kolize_0_100\n"
                "U01,Mladá Boleslav,Po,7,500,23.4,44.7,70.3\n",
                encoding="utf-8",
            )
            (data_dir / "02_obce_kontext.csv").write_text(
                "obec;dojizdejici_denne;spoju_den;kapacita_pr\n"
                "Mladá Boleslav;7920;94;271\n",
                encoding="utf-8",
            )
            (data_dir / "03_simpleml_komplet.csv").write_text(
                "obec,den_v_tydnu,hodina,pocet_vozidel_h,index_plynulosti_0_100\n"
                "Kladno,Po,7,450,55.0\n",
                encoding="utf-8",
            )

            dataset = load_project_data(data_dir)

        df = dataset.data
        self.assertEqual(len(df), 1)
        self.assertIn("spoju_den", df.columns)
        self.assertEqual(df.loc[0, "usek_id"], "U01")
        self.assertEqual(df.loc[0, "kapacita_pr"], 271)
        self.assertIn("01_provoz_useky_gps.csv", dataset.reports)


if __name__ == "__main__":
    unittest.main()
