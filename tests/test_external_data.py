import zipfile

from external_data import DOPR_COLUMNS, load_external_zip_summary


def write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_missing_zip_folder_does_not_crash(tmp_path):
    summary = load_external_zip_summary(tmp_path / "missing")

    assert summary.zip_count_loaded == 0
    assert summary.usable_for_context is False
    assert summary.warnings


def test_invalid_zip_does_not_crash(tmp_path):
    (tmp_path / "DOPR_D_20260401.zip").write_text("not a zip", encoding="utf-8")

    summary = load_external_zip_summary(tmp_path)

    assert summary.zip_count_loaded == 1
    assert "invalid ZIP" in " ".join(summary.warnings)


def test_simple_csv_inside_zip_is_parsed(tmp_path):
    write_zip(tmp_path / "DOPR_D_20260401.zip", {"sample.csv": "a,b\n1,2\n3,4\n"})

    summary = load_external_zip_summary(tmp_path)

    assert summary.usable_for_context is True
    assert summary.total_record_count_estimate == 2
    assert "a" in summary.detected_columns_or_keys


def test_headerless_dopr_csv_gets_named_columns(tmp_path):
    row = '62401|"2026-04-01 00:01:43.996"|1|1.00|100.00|0.00|0|2|3|""|2\n'
    write_zip(tmp_path / "DOPR_D_20260401.zip", {"DOPR_D_20260401.csv": row})

    summary = load_external_zip_summary(tmp_path)

    assert summary.usable_for_context is True
    assert set(DOPR_COLUMNS).issubset(set(summary.detected_columns_or_keys))
    assert summary.external_context_level == "HIGH"
    assert summary.risk_adjustment <= 5


def test_loader_respects_max_files(tmp_path):
    for day in range(1, 5):
        write_zip(tmp_path / f"DOPR_D_2026040{day}.zip", {"sample.csv": "a,b\n1,2\n"})

    summary = load_external_zip_summary(tmp_path, max_files=3)

    assert summary.zip_count_loaded == 3
    assert len(summary.zip_files) == 3
    assert summary.date_range == ("2026-04-01", "2026-04-03")
