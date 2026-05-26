from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import pandas as pd


PROJECT_FILES = {
    "traffic": "01_provoz_useky_gps.csv",
    "context": "02_obce_kontext.csv",
    "simpleml": "03_simpleml_komplet.csv",
}

NUMERIC_COLUMNS = {
    "hodina",
    "volna_rychlost_kmh",
    "pocet_vozidel_h",
    "prum_rychlost_kmh",
    "index_plynulosti_0_100",
    "riziko_kolize_0_100",
    "pocet_obyvatel",
    "dojizdejici_denne",
    "spoju_den",
    "kapacita_pr",
    "podil_cest_autem_procent",
    "prum_index_plynulosti",
    "index_dostupnosti_0_100",
    "vzdalenost_trat_km",
    "vzdalenost_praha_km",
    "ma_velkeho_zamestnavatele",
}

PLAUSIBLE_RANGES = {
    "hodina": (0, 23),
    "volna_rychlost_kmh": (0, 130),
    "prum_rychlost_kmh": (0, 130),
    "pocet_vozidel_h": (0, 50000),
    "index_plynulosti_0_100": (0, 100),
    "riziko_kolize_0_100": (0, 100),
    "pocet_obyvatel": (0, 500000),
    "dojizdejici_denne": (0, 500000),
    "spoju_den": (0, 5000),
    "kapacita_pr": (0, 50000),
    "podil_cest_autem_procent": (0, 100),
    "prum_index_plynulosti": (0, 100),
    "index_dostupnosti_0_100": (0, 100),
    "vzdalenost_trat_km": (0, 500),
    "vzdalenost_praha_km": (0, 500),
    "ma_velkeho_zamestnavatele": (0, 1),
}


@dataclass
class ProjectDataset:
    data: pd.DataFrame
    reports: dict[str, dict]
    source_files: list[str]


def normalize_column_name(name: object) -> str:
    text = unicodedata.normalize("NFKD", str(name).strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _read_csv_with_fallbacks(path: Path) -> tuple[pd.DataFrame, str]:
    errors: list[str] = []
    for encoding in ("utf-8", "utf-8-sig", "cp1250", "latin1"):
        try:
            df = pd.read_csv(path, sep=None, engine="python", encoding=encoding, dtype=str)
            return df, encoding
        except Exception as exc:  # pragma: no cover - diagnostic fallback path
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"Could not read {path}: {'; '.join(errors)}")


def _to_number(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.strip()
        .str.replace("\u00a0", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    cleaned = cleaned.mask(cleaned.str.lower().isin({"", "nan", "none", "null"}))
    return pd.to_numeric(cleaned, errors="coerce")


def load_csv_robust(path: str | Path) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    raw, encoding = _read_csv_with_fallbacks(path)
    raw.columns = [normalize_column_name(col) for col in raw.columns]
    df = raw.copy()

    missing_before = int(df.isna().sum().sum())
    numeric_filled = 0
    clipped = 0
    converted_columns: list[str] = []

    for col in df.columns:
        if col not in NUMERIC_COLUMNS:
            if df[col].dtype == "object":
                df[col] = df[col].astype("string").str.strip()
            continue

        values = _to_number(df[col])
        converted_columns.append(col)
        missing_numeric = int(values.isna().sum())
        median = values.median()
        if pd.isna(median):
            median = 0
        if missing_numeric:
            values = values.fillna(median)
            numeric_filled += missing_numeric

        if col in PLAUSIBLE_RANGES:
            lo, hi = PLAUSIBLE_RANGES[col]
            before = values.copy()
            values = values.clip(lower=lo, upper=hi)
            clipped += int((before != values).sum())

        if col in {"hodina", "pocet_obyvatel", "dojizdejici_denne", "spoju_den", "kapacita_pr", "ma_velkeho_zamestnavatele"}:
            df[col] = values.round().astype("int64")
        else:
            df[col] = values.astype(float)

    report = {
        "path": str(path),
        "encoding": encoding,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "missing_before": missing_before,
        "missing_after": int(df.isna().sum().sum()),
        "numeric_columns": converted_columns,
        "numeric_filled": numeric_filled,
        "clipped_values": clipped,
    }
    return df, report


def load_project_data(data_dir: str | Path = "data") -> ProjectDataset:
    data_dir = Path(data_dir)
    reports: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}

    for key, filename in PROJECT_FILES.items():
        path = data_dir / filename
        if path.exists():
            frames[key], reports[filename] = load_csv_robust(path)

    if not frames:
        raise FileNotFoundError(
            f"No Olympiad CSV files found in {data_dir}. Expected one of: {', '.join(PROJECT_FILES.values())}"
        )

    if "traffic" in frames:
        data = frames["traffic"].copy()
        data["data_source"] = PROJECT_FILES["traffic"]
        if "context" in frames and "obec" in data.columns:
            context = frames["context"].copy()
            context_cols = [
                col
                for col in context.columns
                if col == "obec" or col not in data.columns
            ]
            data = data.merge(context[context_cols], on="obec", how="left")
    else:
        data = frames["simpleml"].copy()
        data["data_source"] = PROJECT_FILES["simpleml"]
        if "context" in frames and "obec" in data.columns:
            context = frames["context"].copy()
            context_cols = [col for col in context.columns if col == "obec" or col not in data.columns]
            data = data.merge(context[context_cols], on="obec", how="left")

    for col in NUMERIC_COLUMNS.intersection(data.columns):
        if data[col].isna().any():
            median = data[col].median()
            if pd.isna(median):
                median = 0
            data[col] = data[col].fillna(median)

    return ProjectDataset(
        data=data.reset_index(drop=True),
        reports=reports,
        source_files=[PROJECT_FILES[key] for key in frames.keys()],
    )


def print_quality_report(dataset: ProjectDataset) -> None:
    print("Data quality report")
    print(f"Combined rows: {len(dataset.data)}")
    print(f"Combined columns: {len(dataset.data.columns)}")
    for filename, report in dataset.reports.items():
        print(
            f"- {filename}: {report['rows']} rows, {len(report['columns'])} columns, "
            f"filled {report['numeric_filled']} numeric values, clipped {report['clipped_values']}"
        )


if __name__ == "__main__":
    project = load_project_data()
    print_quality_report(project)
