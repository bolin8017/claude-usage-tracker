import json
from datetime import datetime, timedelta, timezone

from claude_usage_tracker import usagelog as ul
from claude_usage_tracker import weekly as wk

UTC = timezone.utc


def _raw(pct, reset, code=None, chat=None):
    rows = []
    if code is not None:
        rows.append({"key": "claude_code", "percent_of_limit": code})
    if chat is not None:
        rows.append({"key": "chat", "percent_of_limit": chat})
    return {"five_hour": None,
            "seven_day": {"utilization": pct, "resets_at": reset},
            "seven_day_breakdown": {"rows": rows}}


def _line(ts, pct, reset, uuid="u-1", email="a@x.com", **kw):
    return json.dumps({"ts": ts, "uuid": uuid, "email": email, "display_name": "A",
                       "source": "api", "raw": _raw(pct, reset, **kw)})


def test_resets_at_jitter_across_the_hour_lands_in_one_window():
    a = ul.parse_usage(_raw(10.0, "2026-11-21T20:59:59.757459+00:00"))
    b = ul.parse_usage(_raw(11.0, "2026-11-21T21:00:00.210303+00:00"))
    assert a[1] == b[1] == datetime(2026, 11, 21, 21, 0, tzinfo=UTC)


def test_parse_iso_accepts_variable_fraction_and_z():
    assert ul._parse_iso("2026-11-21T21:00:00.2+00:00") == \
        datetime(2026, 11, 21, 21, 0, 0, 200000, tzinfo=UTC)
    assert ul._parse_iso("2026-11-19T06:16:32Z") == \
        datetime(2026, 11, 19, 6, 16, 32, tzinfo=UTC)


def test_parse_usage_without_weekly_returns_none():
    assert ul.parse_usage({"seven_day": None}) is None
    assert ul.parse_usage({}) is None


def test_parse_usage_breakdown():
    pct, _, bd = ul.parse_usage(_raw(47.0, "2026-11-21T21:00:00Z", code=40, chat=7))
    assert pct == 47.0 and bd == {"claude_code": 40.0, "chat": 7.0}


def test_load_samples_skips_bad_and_empty_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text("\n".join([
        _line("2026-11-19T02:00:00Z", 20, "2026-11-21T21:00:00Z"),
        "not json",
        json.dumps({"ts": "2026-11-19T03:00:00Z", "uuid": "u-1",
                    "raw": {"seven_day": None}}),
        "",
        _line("2026-11-19T01:00:00Z", 10, "2026-11-21T21:00:00Z"),
    ]) + "\n", encoding="utf-8")
    s = ul.load_samples(str(p))
    assert [x.weekly_pct for x in s] == [10, 20]          # sorted by time


def test_load_samples_missing_file(tmp_path):
    assert ul.load_samples(str(tmp_path / "nope.jsonl")) == []


def _sample(ts, pct, wend, uuid="u-1", bd=None):
    return ul.Sample(ts, uuid, f"{uuid}@x.com", pct, wend, bd or {})


def test_weekly_rows_peak_status_and_account_split():
    wend = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
    samples = [
        _sample(wend - timedelta(days=3), 40, wend, bd={"claude_code": 40}),
        _sample(wend - timedelta(hours=1), 89, wend, bd={"claude_code": 80, "chat": 9}),
        _sample(wend - timedelta(days=2), 46, wend, uuid="u-2"),
    ]
    rows = ul.weekly_rows(samples, now_utc=wend + timedelta(days=1))
    by = {r.uuid: r for r in rows}
    assert by["u-1"].peak_pct == 89 and by["u-1"].status == "complete"
    assert by["u-1"].breakdown == {"claude_code": 80, "chat": 9}
    assert by["u-1"].window_start == wend - timedelta(days=7)
    assert by["u-2"].status == "incomplete"                 # last sample 2 days before reset


def test_weekly_rows_in_progress():
    wend = datetime(2026, 11, 21, 21, 0, tzinfo=UTC)
    rows = ul.weekly_rows([_sample(wend - timedelta(days=2), 52, wend)],
                          now_utc=wend - timedelta(days=1))
    assert rows[0].status == "in_progress"


def test_in_month_uses_local_end_date():
    wend = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
    row = ul.weekly_rows([_sample(wend - timedelta(hours=1), 1, wend)], wend)[0]
    local_month = wend.astimezone().strftime("%Y-%m")
    assert wk.in_month(row, local_month)
    assert wk.in_month(row, None)
    assert not wk.in_month(row, "2020-01")


def test_write_csv_columns(tmp_path):
    import csv
    wend = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
    rows = ul.weekly_rows([_sample(wend - timedelta(hours=1), 89.0, wend,
                                   bd={"claude_code": 80.0, "chat": 9.0})], wend)
    p = tmp_path / "w.csv"
    wk.write_csv(rows, str(p))
    with open(p, encoding="utf-8-sig") as f:
        r = list(csv.DictReader(f))[0]
    assert r["weekly_pct"] == "89.0" and r["claude_code_pct"] == "80.0"
    assert r["cowork_pct"] == "" and r["status"] == "完整"


def test_midweek_reset_keeps_peak_and_counts_resets():
    wend = datetime(2026, 11, 21, 21, 0, tzinfo=UTC)
    t0 = wend - timedelta(days=5)
    samples = [
        _sample(t0, 90.0, wend, bd={"claude_code": 90.0}),
        _sample(t0 + timedelta(hours=1), 98.0, wend, bd={"claude_code": 95.0, "chat": 3.0}),
        _sample(t0 + timedelta(hours=2), 0.0, wend, bd={"claude_code": 0.0}),   # reset
        _sample(t0 + timedelta(hours=3), 52.0, wend, bd={"claude_code": 52.0}),
        _sample(t0 + timedelta(hours=4), 51.5, wend, bd={"claude_code": 51.5}),  # within tolerance
    ]
    row = ul.weekly_rows(samples, now_utc=wend)[0]
    assert row.peak_pct == 98.0 and row.resets == 1
    assert row.breakdown == {"claude_code": 95.0, "chat": 3.0}


def test_split_segments_tolerates_one_point_jitter():
    wend = datetime(2026, 11, 21, 21, 0, tzinfo=UTC)
    t0 = wend - timedelta(days=1)
    grp = [_sample(t0 + timedelta(hours=i), p, wend) for i, p in enumerate([10.0, 11.0, 10.0, 12.0])]
    assert len(ul.split_segments(grp)) == 1


# ---------------------------------------------------------------------------
# claumon import
# ---------------------------------------------------------------------------
import sqlite3

from claude_usage_tracker.account import Boundary


def _claumon_db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE usage_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT, raw_json TEXT)")
    con.executemany("INSERT INTO usage_snapshots (timestamp, raw_json) VALUES (?, ?)",
                    [(t, json.dumps(_raw(p, r))) for t, p, r in rows])
    return con


def test_claumon_import_attributes_whole_window_to_majority_owner():
    # Timeline says B took over at 03:00, but claumon kept polling A's window
    # until 05:00 (cached token). The whole A window must still go to A.
    tl = [Boundary(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), "uuid-a", "a@x.com", "A"),
          Boundary(datetime(2026, 11, 2, 3, 0, tzinfo=UTC), "uuid-b", "b@x.com", "B")]
    a_win, b_win = "2026-11-07T21:00:00.2+00:00", "2026-11-05T15:00:00+00:00"
    rows = [(f"2026-11-0{d} {h:02d}:00:00", 10.0 + d * 24 + h, a_win)
            for d in (1, 2) for h in range(0, 24) if (d, h) <= (2, 4)]
    rows += [(f"2026-11-02 {h:02d}:30:00", 1.0 + h, b_win) for h in range(5, 10)]
    recs, report = ul.claumon_records(_claumon_db(rows), tl)
    owners = {w.window_end.hour: (w.owner.uuid, round(w.share, 2)) for w in report}
    assert owners[21][0] == "uuid-a" and owners[21][1] < 1.0   # some votes went to B
    assert owners[15] == ("uuid-b", 1.0)
    assert {r["uuid"] for r in recs if r["raw"]["seven_day"]["resets_at"] == a_win} == {"uuid-a"}


def test_claumon_import_keeps_hourly_last_and_pre_reset_peak():
    tl = [Boundary(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), "uuid-a", "a@x.com", "A")]
    win = "2026-11-21T21:00:00+00:00"
    rows = [("2026-11-17 18:30:20", 97.0, win),
            ("2026-11-17 18:36:20", 98.0, win),   # peak right before the reset
            ("2026-11-17 18:38:21", 0.0, win),
            ("2026-11-17 18:58:00", 1.0, win),    # last of the hour
            ("2026-11-17 19:10:00", 2.0, win)]
    recs, _ = ul.claumon_records(_claumon_db(rows), tl)
    assert [r["raw"]["seven_day"]["utilization"] for r in recs] == [98.0, 1.0, 2.0]


def test_claumon_import_only_uuid_and_no_majority():
    tl = [Boundary(datetime(2026, 11, 1, 0, 0, tzinfo=UTC), "uuid-a", "a@x.com", "A"),
          Boundary(datetime(2026, 11, 1, 2, 0, tzinfo=UTC), "uuid-b", "b@x.com", "B")]
    win = "2026-11-07T21:00:00+00:00"
    rows = [("2026-11-01 01:00:00", 1.0, win), ("2026-11-01 03:00:00", 2.0, win)]  # 50/50
    recs, report = ul.claumon_records(_claumon_db(rows), tl)
    assert recs == [] and report[0].owner is None
    rows.append(("2026-11-01 04:00:00", 3.0, win))                                 # B majority
    recs, _ = ul.claumon_records(_claumon_db(rows), tl, only_uuid="uuid-a")
    assert recs == []


def test_append_and_detect_import(tmp_path):
    p = str(tmp_path / "log.jsonl")
    assert not ul.has_claumon_import(p)
    ul.append_records(p, [{"ts": "2026-11-01T00:00:00Z", "uuid": "u", "email": "é@x.com",
                           "display_name": "É", "source": "claumon",
                           "raw": _raw(5.0, "2026-11-07T21:00:00Z")}])
    assert ul.has_claumon_import(p)
    assert ul.load_samples(p)[0].email == "é@x.com"
