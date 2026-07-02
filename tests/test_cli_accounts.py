from datetime import datetime, timezone

import pytest

from claude_usage_tracker import cli
from claude_usage_tracker import account as acc


def _tl():
    return [acc.Boundary(datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc),
                         "uuid-aaaaaa-rest", "alice@x.com", "Alice"),
            acc.Boundary(datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc),
                         "uuid-bbbbbb-rest", "bob@x.com", "Bob")]


def test_resolve_intervals_none_when_no_account():
    assert cli._resolve_intervals(_tl(), None, datetime.now(timezone.utc)) == (None, None)


def test_resolve_intervals_returns_intervals_and_tag():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    iv, tag = cli._resolve_intervals(_tl(), "alice", now)
    assert tag == "uuid-a"
    assert iv == [(datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc),
                   datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc))]


def test_resolve_intervals_unknown_raises():
    with pytest.raises(acc.AccountSelectionError):
        cli._resolve_intervals(_tl(), "zzz", datetime.now(timezone.utc))
