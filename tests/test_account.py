from datetime import datetime, timezone

from claude_usage_tracker import account as acc


def _b(ts, uuid, email="", name=""):
    return acc.Boundary(datetime(*ts, tzinfo=timezone.utc), uuid, email, name)


def _write(tmp_path, lines):
    p = tmp_path / "account-timeline.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_load_timeline_missing_returns_empty(tmp_path):
    assert acc.load_timeline(str(tmp_path / "nope.jsonl")) == []


def test_load_timeline_sorts_and_skips_bad_lines(tmp_path):
    path = _write(tmp_path, [
        '{"ts":"2026-07-02T09:00:00Z","uuid":"u2","email":"b@x.com"}',
        'not-json',
        '{"ts":"2026-07-02T01:00:00Z","uuid":"u1","email":"a@x.com"}',
    ])
    tl = acc.load_timeline(path)
    assert [b.uuid for b in tl] == ["u1", "u2"]
    assert tl[0].ts == datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)


def test_account_intervals_splits_and_last_runs_to_now():
    tl = [_b((2026, 7, 2, 1, 0), "u1"), _b((2026, 7, 2, 9, 0), "u2")]
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    iv = acc.account_intervals(tl, now)
    assert iv["u1"] == [(datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc),
                         datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc))]
    assert iv["u2"] == [(datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc), now)]


def test_account_intervals_same_account_multiple_segments():
    tl = [_b((2026, 7, 2, 1, 0), "u1"),
          _b((2026, 7, 2, 5, 0), "u2"),
          _b((2026, 7, 2, 8, 0), "u1")]
    now = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    iv = acc.account_intervals(tl, now)
    assert len(iv["u1"]) == 2


def test_resolve_account_by_uuid_email_and_substring():
    tl = [_b((2026, 7, 2, 1, 0), "uuid-aaa", "alice@x.com", "Alice"),
          _b((2026, 7, 2, 9, 0), "uuid-bbb", "bob@x.com", "Bob")]
    assert acc.resolve_account(tl, "uuid-bbb") == "uuid-bbb"
    assert acc.resolve_account(tl, "ALICE@x.com") == "uuid-aaa"
    assert acc.resolve_account(tl, "bob") == "uuid-bbb"


def test_resolve_account_ambiguous_and_missing_raise():
    tl = [_b((2026, 7, 2, 1, 0), "u1", "alice@x.com", "Alice"),
          _b((2026, 7, 2, 9, 0), "u2", "alan@x.com", "Alan")]
    import pytest
    with pytest.raises(acc.AccountSelectionError):
        acc.resolve_account(tl, "al")        # 子字串對到兩個
    with pytest.raises(acc.AccountSelectionError):
        acc.resolve_account(tl, "zzz")       # 對不到
    with pytest.raises(acc.AccountSelectionError):
        acc.resolve_account([], "anything")  # 空時間軸


def test_boundaries_for_range_is_exclusive_start_inclusive_end():
    tl = [_b((2026, 7, 2, 1, 0), "u1"),
          _b((2026, 7, 2, 5, 0), "u2"),
          _b((2026, 7, 2, 9, 0), "u1")]
    s = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    e = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    got = acc.boundaries_for_range(tl, s, e)
    assert [b.uuid for b in got] == ["u2", "u1"]  # 排除等於 start 的首筆、含等於 end
