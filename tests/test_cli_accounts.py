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


# ---------------------------------------------------------------------------
# _markers_for tests (Fix 1 + Fix 2)
# ---------------------------------------------------------------------------
from datetime import timedelta


def test_markers_for_maps_boundary_to_local_axis():
    b_utc = datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)
    tl = [acc.Boundary(b_utc, "uuid-xxxxxx-rest", "e@x.com", "E")]
    b_local = b_utc.astimezone().replace(tzinfo=None)          # boundary on the naive-local axis
    markers = cli._markers_for(tl, b_local - timedelta(hours=1), b_local + timedelta(hours=1))
    assert markers == [(b_local, "e@x.com")]


def test_markers_for_uses_short_uuid_when_email_blank():
    b_utc = datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)
    tl = [acc.Boundary(b_utc, "uuid-xxxxxx-rest", "", "")]
    b_local = b_utc.astimezone().replace(tzinfo=None)
    markers = cli._markers_for(tl, b_local - timedelta(hours=1), b_local + timedelta(hours=1))
    assert markers == [(b_local, "uuid-x")]                    # _short() = first 6 chars


def test_markers_for_returns_none_when_no_switch_in_range():
    b_utc = datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)
    tl = [acc.Boundary(b_utc, "uuid-xxxxxx-rest", "e@x.com", "E")]
    b_local = b_utc.astimezone().replace(tzinfo=None)
    assert cli._markers_for(tl, b_local + timedelta(hours=1), b_local + timedelta(hours=2)) is None
