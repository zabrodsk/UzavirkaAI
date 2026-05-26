from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

import pandas as pd


DOPR_COLUMNS = [
    "IdDetektor",
    "DatumCas",
    "Intenzita",
    "IntenzitaN",
    "Obsazenost",
    "Rychlost",
    "Stav",
    "TypVozidla",
    "Trvani100",
    "RychlostHistorie",
    "TypVozidla10",
]
SEARCH_DIRS = ["", "data", "external_data", "downloads", "opendata"]


@dataclass(frozen=True)
class ExternalZipSummary:
    zip_count_loaded: int
    parsed_files: list[str]
    file_types: dict[str, int]
    total_record_count_estimate: int
    date_range: tuple[str | None, str | None]
    detected_columns_or_keys: list[str]
    warnings: list[str]
    usable_for_context: bool
    external_context_level: str
    risk_adjustment: int
    reason: str | None
    zip_files: list[str]


def load_external_zip_summary(zip_dir: str | Path, max_files: int = 3) -> ExternalZipSummary:
    root = Path(zip_dir)
    warnings: list[str] = []
    parsed_files: list[str] = []
    file_types: dict[str, int] = {}
    columns: set[str] = set()
    dates: list[str] = []
    total_records = 0
    signals = {"records": 0, "low_speed": 0, "high_occupancy": 0, "non_ok_state": 0}
    zip_files = _candidate_zips(root)[: max(0, max_files)]

    for path in zip_files:
        inferred_date = _date_from_name(path.name)
        if inferred_date:
            dates.append(inferred_date)
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    suffix = Path(info.filename).suffix.lower().lstrip(".") or "unknown"
                    file_types[suffix] = file_types.get(suffix, 0) + 1
                    try:
                        parsed = _parse_member(archive, info)
                    except Exception as exc:  # pragma: no cover - defensive summary path
                        warnings.append(f"{path.name}:{info.filename}: {exc}")
                        continue
                    if parsed["parsed"]:
                        parsed_files.append(f"{path.name}:{info.filename}")
                        total_records += int(parsed["record_count"])
                        columns.update(str(item) for item in parsed["columns_or_keys"])
                        for key in signals:
                            signals[key] += int(parsed["signals"].get(key, 0))
                    elif suffix in {"csv", "json", "xml", "txt"}:
                        warnings.append(f"{path.name}:{info.filename}: unsupported or empty {suffix}")
        except zipfile.BadZipFile:
            warnings.append(f"{path.name}: invalid ZIP file")
        except OSError as exc:
            warnings.append(f"{path.name}: {exc}")

    level, adjustment = _context_level(signals)
    usable = bool(parsed_files and total_records)
    reason = None
    if usable and adjustment:
        reason = (
            "External regional traffic data from downloaded ZIP files indicates elevated "
            f"disruption context ({level}), adding {adjustment} risk points."
        )
    elif zip_files and not usable:
        reason = "External ZIP files were inspected but not used for scoring because no clean compatible traffic fields were found."

    if not root.exists():
        warnings.append(f"{root}: folder does not exist")
    return ExternalZipSummary(
        zip_count_loaded=len(zip_files),
        parsed_files=parsed_files,
        file_types=file_types,
        total_record_count_estimate=total_records,
        date_range=(min(dates), max(dates)) if dates else (None, None),
        detected_columns_or_keys=sorted(columns),
        warnings=warnings,
        usable_for_context=usable,
        external_context_level=level if usable else "LOW",
        risk_adjustment=adjustment if usable else 0,
        reason=reason,
        zip_files=[path.name for path in zip_files],
    )


def _candidate_zips(root: Path) -> list[Path]:
    candidates: dict[str, Path] = {}
    for folder in SEARCH_DIRS:
        base = root / folder if folder else root
        if not base.exists() or not base.is_dir():
            continue
        for path in base.glob("DOPR_D_*.zip"):
            candidates[str(path.resolve())] = path
    return sorted(candidates.values(), key=lambda item: item.name)


def _date_from_name(name: str) -> str | None:
    match = re.search(r"DOPR_D_(\d{4})(\d{2})(\d{2})", name)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _parse_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    suffix = Path(info.filename).suffix.lower()
    if suffix == ".csv":
        return _parse_csv_member(archive, info)
    if suffix == ".json":
        return _parse_json_member(archive, info)
    if suffix == ".xml":
        return _parse_xml_member(archive, info)
    if suffix == ".txt":
        text = _read_member_text(archive, info, max_bytes=120_000)
        return {"parsed": bool(text.strip()), "record_count": 1 if text.strip() else 0, "columns_or_keys": ["text"], "signals": {}}
    return {"parsed": False, "record_count": 0, "columns_or_keys": [], "signals": {}}


def _parse_csv_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    text = _read_member_text(archive, info, max_bytes=1_800_000)
    if not text.strip():
        return {"parsed": False, "record_count": 0, "columns_or_keys": [], "signals": {}}
    first_line = text.splitlines()[0]
    delimiter = "|" if first_line.count("|") >= first_line.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        return {"parsed": False, "record_count": 0, "columns_or_keys": [], "signals": {}}
    headerless_dopr = delimiter == "|" and len(rows[0]) == len(DOPR_COLUMNS) and _looks_numeric(rows[0][0])
    if headerless_dopr:
        columns = DOPR_COLUMNS
        data_rows = rows
    else:
        columns = [str(item).strip() or f"column_{index + 1}" for index, item in enumerate(rows[0])]
        data_rows = rows[1:]
    frame = pd.DataFrame(data_rows, columns=columns[: len(data_rows[0])] if data_rows else columns)
    signals = _traffic_signals(frame)
    return {
        "parsed": True,
        "record_count": _estimate_csv_records(archive, info, len(data_rows)),
        "columns_or_keys": columns,
        "signals": signals,
    }


def _parse_json_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    payload = json.loads(_read_member_text(archive, info, max_bytes=1_800_000))
    keys = set()
    record_count = 1
    if isinstance(payload, list):
        record_count = len(payload)
        for item in payload[:100]:
            if isinstance(item, dict):
                keys.update(item.keys())
    elif isinstance(payload, dict):
        keys.update(payload.keys())
    return {"parsed": True, "record_count": record_count, "columns_or_keys": sorted(keys), "signals": {"records": record_count}}


def _parse_xml_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    root = ET.fromstring(_read_member_text(archive, info, max_bytes=1_800_000))
    tags = {root.tag}
    count = 0
    for count, element in enumerate(root.iter(), start=1):
        tags.add(element.tag)
        if count >= 2000:
            break
    return {"parsed": True, "record_count": count, "columns_or_keys": sorted(tags), "signals": {"records": count}}


def _read_member_text(archive: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int) -> str:
    with archive.open(info) as handle:
        raw = handle.read(max_bytes)
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace")


def _estimate_csv_records(archive: zipfile.ZipFile, info: zipfile.ZipInfo, sampled_rows: int) -> int:
    if info.file_size <= 1_800_000:
        return max(0, sampled_rows)
    sample_size = min(info.file_size, 1_800_000)
    return max(sampled_rows, int(sampled_rows * info.file_size / sample_size))


def _traffic_signals(frame: pd.DataFrame) -> dict[str, int]:
    signals = {"records": len(frame), "low_speed": 0, "high_occupancy": 0, "non_ok_state": 0}
    if "Rychlost" in frame.columns:
        speed = pd.to_numeric(frame["Rychlost"].astype(str).str.replace(",", "."), errors="coerce")
        signals["low_speed"] = int((speed < 20).sum())
    if "Obsazenost" in frame.columns:
        occupancy = pd.to_numeric(frame["Obsazenost"].astype(str).str.replace(",", "."), errors="coerce")
        signals["high_occupancy"] = int((occupancy > 70).sum())
    if "Stav" in frame.columns:
        state = pd.to_numeric(frame["Stav"], errors="coerce")
        signals["non_ok_state"] = int((state.fillna(0) != 0).sum())
    return signals


def _context_level(signals: dict[str, int]) -> tuple[str, int]:
    records = max(1, int(signals.get("records", 0)))
    elevated = int(signals.get("low_speed", 0)) + int(signals.get("high_occupancy", 0)) + int(signals.get("non_ok_state", 0))
    share = elevated / records
    if elevated >= 100 or share >= 0.10:
        return "HIGH", 5
    if elevated >= 20 or share >= 0.03:
        return "MEDIUM", 3
    return "LOW", 0


def _looks_numeric(value: object) -> bool:
    try:
        float(str(value).strip().replace(",", "."))
        return True
    except ValueError:
        return False
