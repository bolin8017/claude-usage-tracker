import csv
from datetime import date, datetime, timedelta, timezone

import pytest

from claude_usage_tracker import cli
from claude_usage_tracker import daily as dl
from claude_usage_tracker import usagelog as ul


def _at(day, hour, minute=0):
    """Aware datetime for a LOCAL wall-clock time (tests run in any timezone)."""
    return datetime(2026, 11, day, hour, minute).astimezone().astimezone(timezone.utc)


W1 = datetime(2026, 11, 14, 21, 0, tzinfo=timezone.utc)
W2 = W1 + timedelta(days=7)


def _s(t, pct, wend, uuid="u-1"):
    return ul.Sample(t, uuid, f"{uuid}@x.com", pct, wend, {})


def _rows(samples, today=date(2026, 11, 19)):
    return {r.day: r for r in dl.daily_rows(samples, "2026-11", today)}


def test_end_of_day_value_and_last_sample_time():
    rows = _rows([_s(_at(16, 9), 40, W2), _s(_at(16, 23, 30), 58, W2)])
    r = rows[date(2026, 11, 16)]
    assert r.pct == 58 and r.last_sample.strftime("%H:%M") == "23:30"
    assert dl.note_text(r) == ""


def test_month_has_every_day_with_no_data_and_future_days():
    rows = _rows([_s(_at(16, 9), 40, W2)])
    assert len(rows) == 30
    assert dl.note_text(rows[date(2026, 11, 11)]) == "無資料"
    assert rows[date(2026, 11, 25)].future and dl.note_text(rows[date(2026, 11, 25)]) == ""


def test_reset_after_midnight_compares_with_previous_day():
    samples = [_s(_at(17, 23, 50), 98, W2),          # last of 11/17
               _s(_at(18, 2, 38), 0, W2),            # reset, first of 11/18
               _s(_at(18, 23, 58), 27, W2)]
    r = _rows(samples)[date(2026, 11, 18)]
    assert r.pct == 27
    assert dl.note_text(r) == "reset（reset 前 98%）"


def test_window_rollover_reports_previous_window_peak():
    samples = [_s(_at(14, 10), 80, W1), _s(_at(14, 23), 89, W1),
               _s(_at(15, 9), 3, W2), _s(_at(15, 23), 25, W2)]
    r = _rows(samples)[date(2026, 11, 15)]
    assert r.pct == 25 and dl.note_text(r) == "週期重置（前一週最高 89%）"


def test_event_detected_against_previous_month():
    prev_month = datetime(2026, 10, 31, 22, 0).astimezone().astimezone(timezone.utc)
    samples = [_s(prev_month, 89, W1 - timedelta(days=14)),
               _s(_at(1, 9), 2, W1 - timedelta(days=7))]
    r = _rows(samples)[date(2026, 11, 1)]
    assert dl.note_text(r) == "週期重置（前一週最高 89%）"


def test_small_dip_is_not_a_reset():
    samples = [_s(_at(16, 9), 50, W2), _s(_at(16, 10), 49, W2)]
    assert dl.note_text(_rows(samples)[date(2026, 11, 16)]) == ""


def test_write_csv(tmp_path):
    rows = dl.daily_rows([_s(_at(18, 2, 38), 27, W2)], "2026-11", date(2026, 11, 19))
    p = tmp_path / "d.csv"
    dl.write_csv(rows, str(p))
    with open(p, encoding="utf-8-sig") as f:
        got = {r["date"]: r for r in csv.DictReader(f)}
    assert got["2026-11-18"]["weekly_pct"] == "27" and got["2026-11-18"]["last_sample"] == "02:38"
    assert got["2026-11-01"]["weekly_pct"] == "" and got["2026-11-01"]["note"] == "無資料"


def test_monthly_requires_account_when_log_has_several(tmp_path, monkeypatch):
    import json
    p = tmp_path / "log.jsonl"
    lines = []
    for uuid in ("u-1", "u-2"):
        lines.append(json.dumps({"ts": "2026-11-16T01:00:00Z", "uuid": uuid,
                                 "email": f"{uuid}@x.com", "raw": {"seven_day": {
                                     "utilization": 5.0, "resets_at": "2026-11-21T21:00:00Z"}}}))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--log", str(p), "monthly", "--month", "2026-11", "--no-chart",
                  "--out", str(tmp_path)])
    assert "--account" in str(e.value)
    cli.main(["--log", str(p), "monthly", "--month", "2026-11", "--no-chart",
              "--account", "u-2", "--out", str(tmp_path)])
    assert (tmp_path / "usage_daily_2026-11.csv").exists()
