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
