import sqlite3
from datetime import datetime, timezone

from claude_usage_tracker.db import load_snapshots


def _con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE usage_snapshots "
                "(timestamp TEXT, session_pct REAL, weekly_pct REAL, raw_json TEXT)")
    rows = [
        ("2026-07-02 01:00:00", 10.0, 5.0, "{}"),   # 屬 u1
        ("2026-07-02 03:00:00", 20.0, 6.0, "{}"),   # 屬 u1
        ("2026-07-02 10:00:00", 30.0, 7.0, "{}"),   # 屬 u2
    ]
    con.executemany("INSERT INTO usage_snapshots VALUES (?,?,?,?)", rows)
    return con


def test_no_intervals_returns_all():
    recs = load_snapshots(_con(), all_=True)
    assert len(recs) == 3


def test_utc_intervals_filters_to_account_window():
    u1 = [(datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),
           datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc))]
    recs = load_snapshots(_con(), all_=True, utc_intervals=u1)
    assert [r.session for r in recs] == [10.0, 20.0]


def test_empty_intervals_returns_none():
    recs = load_snapshots(_con(), all_=True, utc_intervals=[])
    assert recs == []
