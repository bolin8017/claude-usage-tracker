import sqlite3
from datetime import datetime, timezone

from claude_usage_tracker.export import _utilization


def _con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE usage_snapshots "
                "(timestamp TEXT, session_pct REAL, weekly_pct REAL, raw_json TEXT)")
    con.executemany("INSERT INTO usage_snapshots VALUES (?,?,?,?)", [
        ("2026-07-02 01:00:00", 10.0, 5.0, "{}"),
        ("2026-07-02 10:00:00", 30.0, 7.0, "{}"),
    ])
    return con


def _snap_count(path):
    import csv
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return sum(int(r["snapshots"]) for r in rows)


def test_utilization_unfiltered_counts_all(tmp_path):
    path, _ = _utilization(_con(), str(tmp_path), None, "all")
    assert _snap_count(path) == 2


def test_utilization_filtered_by_interval(tmp_path):
    iv = [(datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),
           datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc))]
    path, _ = _utilization(_con(), str(tmp_path), None, "all", utc_intervals=iv)
    assert _snap_count(path) == 1


def test_utilization_empty_interval_yields_no_rows(tmp_path):
    path, _ = _utilization(_con(), str(tmp_path), None, "all", utc_intervals=[])
    assert _snap_count(path) == 0


def test_account_tag_in_filename(tmp_path):
    import os
    path, _ = _utilization(_con(), str(tmp_path), None, "all", account_tag="ab12cd")
    assert os.path.basename(path) == "utilization_all_ab12cd.csv"
