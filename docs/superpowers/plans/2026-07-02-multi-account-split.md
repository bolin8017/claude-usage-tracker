# 多帳號分流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自動偵測 `claude` 換帳號，讓 claude-usage 能依帳號分別呈現額度用量，且完全不干擾 claumon。

**Architecture:** watchdog 每 3 分鐘心跳新增一個 best-effort 腳本，打 profile API 取 `account.uuid`，只在換帳號時往 `~/.claumon/account-timeline.jsonl` 追加一筆邊界。讀取端新增 `account.py` 把時間軸換算成各帳號的 UTC 區間；`chart`/`export` 以區間過濾 snapshot，新增 `accounts` 子指令與圖表換帳號標記。claumon 的 `usage.db` 完全不被碰。

**Tech Stack:** Python 3.9+（標準函式庫 + matplotlib）、pytest、PowerShell（Windows watchdog）。

## Global Constraints

- Python 版本下限：`requires-python = ">=3.9"`；只用標準函式庫 + matplotlib（既有相依），測試用 pytest。
- 時間一律以 UTC 儲存與運算，僅在輸出時轉本地（沿用 `db._utc_to_local`）。
- canonical 帳號鍵 = `account.uuid`；`email` 僅供人類辨識/選擇。
- 時間軸檔：`~/.claumon/account-timeline.jsonl`，append-only，每行一個 JSON，**不含任何 token**。
- 未給 `--account` 時所有現行行為必須不變。
- PowerShell 腳本 best-effort：任何錯誤只記 log、`return`，永不丟例外；不快取、不碰 `usage.db`。
- 程式碼、log、commit 訊息用英文；面向使用者的 CLI 輸出與文件維持繁中（比照現有風格）。
- Conventional Commits；每個 task 結束時 commit，樹保持綠燈。

---

### Task 1: `account.py` — 時間軸讀取與區間換算

**Files:**
- Create: `src/claude_usage_tracker/account.py`
- Create: `tests/test_account.py`
- Modify: `pyproject.toml`（加入 pytest 為 dev 選用相依）

**Interfaces:**
- Consumes: 無（純標準函式庫）。
- Produces:
  - `Boundary(ts: datetime[aware UTC], uuid: str, email: str, display_name: str)`
  - `AccountInfo(uuid, email, display_name, first_seen, last_seen)`
  - `AccountSelectionError(Exception)`
  - `default_timeline_path() -> str`
  - `load_timeline(path: str) -> List[Boundary]`
  - `account_intervals(timeline, now_utc: datetime) -> Dict[str, List[Tuple[datetime, datetime]]]`
  - `list_accounts(timeline) -> List[AccountInfo]`
  - `resolve_account(timeline, query: str) -> str`（回 uuid）
  - `boundaries_for_range(timeline, start_utc, end_utc) -> List[Boundary]`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_account.py`:

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_account.py -q`
Expected: FAIL（`ModuleNotFoundError` / `AttributeError: module 'account' has no attribute ...`）

- [ ] **Step 3: 實作 `account.py`**

Create `src/claude_usage_tracker/account.py`:

```python
"""帳號時間軸：讀 ~/.claumon/account-timeline.jsonl，換算各帳號的用量時間區間。

時間軸由 watchdog 心跳的 claumon-account-track.ps1 追加，只在換帳號時寫入一筆。
本模組純讀取、無副作用；now 由呼叫端傳入以利測試。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, NamedTuple, Tuple


class Boundary(NamedTuple):
    ts: datetime          # aware UTC
    uuid: str
    email: str
    display_name: str


class AccountInfo(NamedTuple):
    uuid: str
    email: str
    display_name: str
    first_seen: datetime  # aware UTC
    last_seen: datetime   # aware UTC


class AccountSelectionError(Exception):
    """--account 對不到唯一帳號時丟出。"""


def default_timeline_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claumon", "account-timeline.jsonl")


def _parse_ts(raw: str) -> datetime:
    # timeline 以 UTC ISO8601（…Z）寫入
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_timeline(path: str) -> List[Boundary]:
    """讀時間軸，回傳依 ts 排序的 Boundary；檔案不存在回 []，壞行跳過。"""
    if not os.path.exists(path):
        return []
    out: List[Boundary] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(Boundary(_parse_ts(d["ts"]), d["uuid"],
                                    d.get("email", ""), d.get("display_name", "")))
            except Exception:
                continue
    out.sort(key=lambda b: b.ts)
    return out


def account_intervals(timeline: List[Boundary],
                      now_utc: datetime) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """每個 uuid 擁有的 UTC 區間 [start, end)；最後一筆到 now_utc。"""
    intervals: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for i, b in enumerate(timeline):
        end = timeline[i + 1].ts if i + 1 < len(timeline) else now_utc
        intervals.setdefault(b.uuid, []).append((b.ts, end))
    return intervals


def list_accounts(timeline: List[Boundary]) -> List[AccountInfo]:
    """時間軸上已知帳號（依 first_seen 排序）；email/display_name 取該帳號最後一筆。"""
    order: List[str] = []
    first: Dict[str, datetime] = {}
    last: Dict[str, datetime] = {}
    email: Dict[str, str] = {}
    name: Dict[str, str] = {}
    for b in timeline:
        if b.uuid not in first:
            first[b.uuid] = b.ts
            order.append(b.uuid)
        last[b.uuid] = b.ts
        email[b.uuid] = b.email
        name[b.uuid] = b.display_name
    return [AccountInfo(u, email[u], name[u], first[u], last[u]) for u in order]


def resolve_account(timeline: List[Boundary], query: str) -> str:
    """把 --account 的輸入解析成唯一 uuid，否則丟 AccountSelectionError。"""
    accounts = list_accounts(timeline)
    if not accounts:
        raise AccountSelectionError("帳號時間軸為空；請確認自動偵測已安裝並至少執行過一次。")
    q = query.strip()
    for a in accounts:                       # 1. uuid 完全相符
        if a.uuid == q:
            return a.uuid
    ql = q.lower()
    exact = [a for a in accounts if a.email.lower() == ql]  # 2. email 完全相符
    if len(exact) == 1:
        return exact[0].uuid
    subs = [a for a in accounts              # 3. email / display_name 子字串
            if ql in a.email.lower() or ql in a.display_name.lower()]
    if len(subs) == 1:
        return subs[0].uuid
    cands = ", ".join(a.email or a.uuid for a in (subs or accounts))
    if len(subs) > 1:
        raise AccountSelectionError(f"'{query}' 對到多個帳號：{cands}")
    raise AccountSelectionError(f"找不到符合 '{query}' 的帳號；可選：{cands}")


def boundaries_for_range(timeline: List[Boundary],
                         start_utc: datetime, end_utc: datetime) -> List[Boundary]:
    """回傳 ts 落在 (start_utc, end_utc] 內的邊界（供圖表換帳號標記用）。"""
    return [b for b in timeline if start_utc < b.ts <= end_utc]
```

- [ ] **Step 4: 加入 pytest 為 dev 相依**

Modify `pyproject.toml`，於 `dependencies = ["matplotlib>=3.5"]` 之後新增：

```toml
[project.optional-dependencies]
dev = ["pytest>=7"]
```

- [ ] **Step 5: 執行測試確認通過**

Run: `python -m pytest tests/test_account.py -q`
Expected: PASS（8 passed）

- [ ] **Step 6: Commit**

```bash
git add src/claude_usage_tracker/account.py tests/test_account.py pyproject.toml
git commit -m "feat(account): add account timeline reader and interval logic"
```

---

### Task 2: `db.py` — `load_snapshots` 支援 UTC 區間過濾

**Files:**
- Modify: `src/claude_usage_tracker/db.py`
- Create: `tests/test_db_filter.py`

**Interfaces:**
- Consumes: `account.account_intervals` 產出的 `List[Tuple[datetime, datetime]]`（aware UTC）。
- Produces: `load_snapshots(con, *, month=None, days=None, all_=False, utc_intervals=None) -> List[Record]`。

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_db_filter.py`:

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_db_filter.py -q`
Expected: FAIL（`load_snapshots() got an unexpected keyword argument 'utc_intervals'`）

- [ ] **Step 3: 實作**

Modify `src/claude_usage_tracker/db.py`。

(a) `_utc_to_local` 下方新增兩個 helper：

```python
def _parse_utc(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _in_intervals(t: datetime, intervals) -> bool:
    return any(s <= t < e for s, e in intervals)
```

(b) 改寫 `load_snapshots` 簽章與尾段迴圈（其餘 SQL 組裝不變）：

```python
def load_snapshots(con: sqlite3.Connection, *, month: Optional[str] = None,
                   days: Optional[int] = None, all_: bool = False,
                   utc_intervals: Optional[List[tuple]] = None) -> List[Record]:
    """讀取 usage_snapshots，回傳依時間排序的 Record 清單。

    篩選優先序：month > all_ > days。另可傳 utc_intervals（aware UTC 區間清單）
    只保留落在任一 [start, end) 內的快照；傳入空清單則回空。
    """
    sql = ("SELECT timestamp, session_pct, weekly_pct, raw_json "
           "FROM usage_snapshots ")
    params: list = []
    if month:
        sql += "WHERE substr(timestamp,1,7)=? "
        params.append(month)
    elif not all_ and days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        sql += "WHERE timestamp>=? "
        params.append(cutoff)
    sql += "ORDER BY timestamp"

    out: List[Record] = []
    for ts, s, wk, raw in con.execute(sql, params):
        if utc_intervals is not None and not _in_intervals(_parse_utc(ts), utc_intervals):
            continue
        out.append(Record(_utc_to_local(ts), s, wk, _sonnet_pct(raw)))
    return out
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_db_filter.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/claude_usage_tracker/db.py tests/test_db_filter.py
git commit -m "feat(db): filter snapshots by account UTC intervals"
```

---

### Task 3: `chart.py` — 換帳號標記與帳號檔名短碼

**Files:**
- Modify: `src/claude_usage_tracker/chart.py`

**Interfaces:**
- Consumes: cli 傳入 `markers: Optional[List[Tuple[datetime, str]]]`（本地 naive 時間 + 標籤）與 `account_tag: Optional[str]`。
- Produces: `run(recs, *, out_dir, scope, series, resample_min, agg, no_chart, account_tag=None, markers=None)`；`draw_chart(path, recs, series, title, gap_min, markers=None)`。

（draw_chart 依賴 matplotlib、以人工驗收；本 task 只加參數與繪製邏輯，不新增自動化測試。）

- [ ] **Step 1: 於 `draw_chart` 加 markers 參數並繪製**

Modify `src/claude_usage_tracker/chart.py`：`draw_chart` 簽章改為

```python
def draw_chart(path: str, recs: Sequence[Record], series: Sequence[str],
               title: str, gap_min: float, markers=None) -> None:
```

在 `ax.legend(loc="upper left")` 這行之前插入：

```python
    if markers:
        for mt, label in markers:
            ax.axvline(mt, color="#9ca3af", linestyle="--", linewidth=1,
                       alpha=0.7, zorder=1)
            ax.annotate(label, (mt, 100), textcoords="offset points",
                        xytext=(2, -10), rotation=90, va="top", ha="left",
                        fontsize=7, color="#6b7280")
```

- [ ] **Step 2: 於 `run` 加 account_tag / markers 並串接檔名**

Modify `run`：簽章改為

```python
def run(recs: List[Record], *, out_dir: str, scope: str, series: List[str],
        resample_min: int, agg: str, no_chart: bool,
        account_tag: Optional[str] = None, markers=None) -> None:
```

把檔名組裝改為（新增 `acct` 後綴）：

```python
    tag = "-".join(series)
    acct = f"_{account_tag}" if account_tag else ""
    suffix = scope + (f"_{resample_min}m" if resample_min > 0 else "")
```

`csv_path` 與 `png_path` 兩行改為：

```python
    csv_path = os.path.join(out_dir, f"usage_timeseries_{suffix}_{tag}{acct}.csv")
```

```python
        png_path = os.path.join(out_dir, f"usage_chart_{suffix}_{tag}{acct}.png")
        draw_chart(png_path, recs, series, title, gap_min, markers)
```

- [ ] **Step 3: 冒煙驗證（不畫圖路徑）**

Run:
```bash
python -c "from claude_usage_tracker import chart; from claude_usage_tracker.db import Record; from datetime import datetime; chart.run([Record(datetime(2026,7,2,1,0),10.0,5.0,None),Record(datetime(2026,7,2,2,0),20.0,6.0,None)], out_dir='exports', scope='smoke', series=['session'], resample_min=0, agg='max', no_chart=True, account_tag='ab12cd')"
```
Expected: 印出範圍/序列並 `[OK] exports/usage_timeseries_smoke_session_ab12cd.csv`

- [ ] **Step 4: 清理冒煙輸出並 Commit**

```bash
rm -f exports/usage_timeseries_smoke_session_ab12cd.csv
git add src/claude_usage_tracker/chart.py
git commit -m "feat(chart): add account-switch markers and per-account filename tag"
```

---

### Task 4: `export.py` — utilization 依帳號過濾（token/成本維持全量）

**Files:**
- Modify: `src/claude_usage_tracker/export.py`
- Create: `tests/test_export_filter.py`

**Interfaces:**
- Consumes: cli 傳入 `utc_intervals`（aware UTC 區間）與 `account_tag`。
- Produces:
  - `run(con, *, out_dir, month, utc_intervals=None, account_tag=None)`
  - `_utilization(con, out_dir, month, scope, utc_intervals=None, account_tag=None) -> (path, nrows)`

- [ ] **Step 1: 寫失敗測試**

Create `tests/test_export_filter.py`:

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_export_filter.py -q`
Expected: FAIL（`_utilization() got an unexpected keyword argument 'utc_intervals'`）

- [ ] **Step 3: 實作**

Modify `src/claude_usage_tracker/export.py`。

(a) 改寫 `_utilization`（用 WHERE 清單組裝，字典序比較對 `YYYY-MM-DD HH:MM:SS` UTC 字串成立）：

```python
def _utilization(con: sqlite3.Connection, out_dir: str, month: Optional[str], scope: str,
                 utc_intervals=None, account_tag: Optional[str] = None):
    sql = ("SELECT substr(timestamp,1,7) AS month, "
           "ROUND(MAX(session_pct),1), ROUND(AVG(session_pct),1), "
           "ROUND(MAX(weekly_pct),1), ROUND(AVG(weekly_pct),1), COUNT(*) "
           "FROM usage_snapshots ")
    where: list = []
    params: list = []
    if month:
        where.append("substr(timestamp,1,7)=?")
        params.append(month)
    if utc_intervals is not None:
        if utc_intervals:
            ors = []
            for s, e in utc_intervals:
                ors.append("(timestamp>=? AND timestamp<?)")
                params.append(s.strftime("%Y-%m-%d %H:%M:%S"))
                params.append(e.strftime("%Y-%m-%d %H:%M:%S"))
            where.append("(" + " OR ".join(ors) + ")")
        else:
            where.append("0=1")  # 空區間 → 不回任何列
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "GROUP BY month ORDER BY month"
    header = ["month", "session_pct_max", "session_pct_avg",
              "weekly_pct_max", "weekly_pct_avg", "snapshots"]
    acct = f"_{account_tag}" if account_tag else ""
    path = os.path.join(out_dir, f"utilization_{scope}{acct}.csv")
    return path, _write_csv(path, header, con.execute(sql, params).fetchall())
```

(b) 改寫 `run` 簽章與尾段：

```python
def run(con: sqlite3.Connection, *, out_dir: str, month: Optional[str],
        utc_intervals=None, account_tag: Optional[str] = None) -> None:
    """匯出月度彙整、每日明細、額度峰值三份 CSV。

    --account（utc_intervals）只作用於 utilization；token/成本（daily_aggregates）
    為日粒度、本機層級，維持全量不切。
    """
    scope = month if month else "all"
    os.makedirs(out_dir, exist_ok=True)
    print(f"範圍　：{scope}")
    print(f"輸出至：{out_dir}\n")

    p1, n1 = _monthly_summary(con, out_dir)
    print(f"[OK] {os.path.basename(p1)}  ({n1} 個月)")
    p2, n2 = _daily(con, out_dir, month, scope)
    print(f"[OK] {os.path.basename(p2)}  ({n2} 天)")
    p3, n3 = _utilization(con, out_dir, month, scope, utc_intervals, account_tag)
    print(f"[OK] {os.path.basename(p3)}  ({n3} 個月)")

    if utc_intervals is not None:
        print("\n[說明] --account 只作用於 utilization（額度快照）；"
              "token/成本為日粒度、本機層級，不依帳號分。")

    if n2 == 0:
        print("\n[提醒] daily 明細為 0 筆：此機器尚無 Claude Code session 的 "
              "token/成本紀錄（額度快照仍正常累積）。")
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_export_filter.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/claude_usage_tracker/export.py tests/test_export_filter.py
git commit -m "feat(export): filter utilization by account, keep token/cost whole"
```

---

### Task 5: `cli.py` — `--account`、`accounts` 子指令、串接標記

**Files:**
- Modify: `src/claude_usage_tracker/cli.py`
- Create: `tests/test_cli_accounts.py`

**Interfaces:**
- Consumes: `account.*`（Task 1）、`load_snapshots(utc_intervals=...)`（Task 2）、
  `chart.run(account_tag=, markers=)`（Task 3）、`export.run(utc_intervals=, account_tag=)`（Task 4）。
- Produces: `_resolve_intervals(timeline, account, now_utc) -> Tuple[Optional[list], Optional[str]]`
  （回 `(utc_intervals, account_tag)`；`account` 為 None 時回 `(None, None)`）。

- [ ] **Step 1: 寫失敗測試（純函式 `_resolve_intervals`）**

Create `tests/test_cli_accounts.py`:

```python
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_cli_accounts.py -q`
Expected: FAIL（`module 'cli' has no attribute '_resolve_intervals'`）

- [ ] **Step 3: 實作 cli 變更**

Modify `src/claude_usage_tracker/cli.py`。

(a) import 區塊改為：

```python
"""命令列介面：claude-usage export / chart / accounts。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import __version__
from . import chart as chart_mod
from . import export as export_mod
from .account import (AccountSelectionError, account_intervals,
                      boundaries_for_range, default_timeline_path,
                      list_accounts, load_timeline, resolve_account)
from .db import connect_ro, default_db_path, load_snapshots
```

(b) `_validate_month` 之後新增純函式：

```python
def _resolve_intervals(timeline, account, now_utc):
    """把 --account 解析成 (utc_intervals, account_tag)；account 為 None 回 (None, None)。"""
    if not account:
        return None, None
    uuid = resolve_account(timeline, account)      # 失敗丟 AccountSelectionError
    intervals = account_intervals(timeline, now_utc).get(uuid, [])
    return intervals, uuid[:6]
```

(c) `build_parser`：`export` 與 `chart` 各加 `--account`，`chart` 加 `--no-account-markers`，並新增 `accounts` 子指令。於 `pe.add_argument("--month", ...)` 之後：

```python
    pe.add_argument("--account", help="只匯出指定帳號（email 或 uuid）的 utilization")
```

於 `pc.add_argument("--no-chart", ...)` 之後、`_add_range(pc)` 之前：

```python
    pc.add_argument("--account", help="只畫指定帳號（email 或 uuid）的用量")
    pc.add_argument("--no-account-markers", action="store_true",
                    help="不在混帳號圖上畫換帳號標記")
```

於 `return ap` 之前：

```python
    sub.add_parser("accounts", help="列出時間軸上已知的 Claude 帳號")
```

(d) `main` 改為（涵蓋 accounts / export / chart 三路）：

```python
def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "accounts":
        _run_accounts(args.db)
        return

    print(f"資料庫：{args.db}")
    timeline = load_timeline(default_timeline_path())
    try:
        utc_intervals, account_tag = _resolve_intervals(
            timeline, args.account, datetime.now(timezone.utc))
    except AccountSelectionError as e:
        sys.exit(f"[錯誤] {e}")

    if args.command == "export":
        _validate_month(args.month)
        con = connect_ro(args.db)
        try:
            export_mod.run(con, out_dir=args.out, month=args.month,
                           utc_intervals=utc_intervals, account_tag=account_tag)
        finally:
            con.close()
        return

    # chart
    _validate_month(args.month)
    try:
        series = chart_mod.parse_series(args.series)
    except ValueError as e:
        sys.exit(f"[錯誤] {e}")

    con = connect_ro(args.db)
    try:
        recs = load_snapshots(con, month=args.month, days=args.days,
                              all_=args.all, utc_intervals=utc_intervals)
    finally:
        con.close()

    if not recs:
        sys.exit("[提醒] 指定範圍內沒有任何快照資料。")

    markers = None
    if not args.account and not args.no_account_markers and timeline:
        start_utc = recs[0].time.astimezone(timezone.utc)
        end_utc = recs[-1].time.astimezone(timezone.utc)
        bs = boundaries_for_range(timeline, start_utc, end_utc)
        if bs:
            markers = [(b.ts.astimezone().replace(tzinfo=None), b.email or b.uuid[:6])
                       for b in bs]

    scope = args.month if args.month else ("all" if args.all else f"last{args.days}d")
    chart_mod.run(recs, out_dir=args.out, scope=scope, series=series,
                  resample_min=args.resample, agg=args.agg, no_chart=args.no_chart,
                  account_tag=account_tag, markers=markers)
```

(e) `main` 之前新增 `_run_accounts`：

```python
def _run_accounts(db_path: str) -> None:
    timeline = load_timeline(default_timeline_path())
    accounts = list_accounts(timeline)
    if not accounts:
        print("尚無帳號時間軸資料。可能：自動偵測尚未執行、未安裝偵測腳本，或目前為 API 金鑰模式。")
        return
    intervals = account_intervals(timeline, datetime.now(timezone.utc))
    con = connect_ro(db_path)
    try:
        print(f"{'email':<32}{'uuid':<10}{'first_seen':<18}{'last_seen':<18}snapshots")
        for a in accounts:
            cnt = len(load_snapshots(con, all_=True,
                                     utc_intervals=intervals.get(a.uuid, [])))
            fs = a.first_seen.astimezone().strftime("%Y-%m-%d %H:%M")
            ls = a.last_seen.astimezone().strftime("%Y-%m-%d %H:%M")
            print(f"{a.email:<32}{a.uuid[:8]:<10}{fs:<18}{ls:<18}{cnt}")
    finally:
        con.close()
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_cli_accounts.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 全套測試 + CLI 冒煙**

Run: `python -m pytest -q`
Expected: PASS（全部）

Run: `python -m claude_usage_tracker accounts`
Expected: 若本機尚無時間軸，印「尚無帳號時間軸資料…」；不報錯。

- [ ] **Step 6: Commit**

```bash
git add src/claude_usage_tracker/cli.py tests/test_cli_accounts.py
git commit -m "feat(cli): add --account filter, accounts subcommand, switch markers"
```

---

### Task 6: `claumon-account-track.ps1` + install/watchdog 接線（人工驗收）

**Files:**
- Create: `scripts/claumon-account-track.ps1`
- Modify: `scripts/install.ps1`

**Interfaces:**
- Consumes: `~/.claude/.credentials.json`（`claudeAiOauth.accessToken`）、profile API。
- Produces: append `~/.claumon/account-timeline.jsonl`（`{"ts","uuid","email","display_name"}`）。

- [ ] **Step 1: 建立偵測腳本**

Create `scripts/claumon-account-track.ps1`:

```powershell
<#
.SYNOPSIS
    偵測目前登入的 Claude 帳號，換帳號時追加一筆到 account-timeline.jsonl。

.DESCRIPTION
    claumon 讀 ~/.claude/.credentials.json 打訂閱用量 API，但憑證檔內沒有帳號識別碼，
    無法從本機判斷「這是哪個帳號」或「換帳號 vs 一般 token 續期」。本腳本用當前 access
    token 打 profile API 取穩定的 account.uuid，只有當它與時間軸最後一筆不同（或時間軸為空）
    時，才追加一筆邊界。供 claude-usage 依帳號分流用量。

    全程 best-effort：任何錯誤只記 log、不丟例外，不影響 watchdog 其餘工作。
    不快取、不碰 usage.db、不寫入任何 token。

.PARAMETER CredentialsPath
    憑證檔路徑。預設 ~/.claude/.credentials.json。

.PARAMETER TimelinePath
    帳號時間軸路徑。預設 ~/.claumon/account-timeline.jsonl。
#>
[CmdletBinding()]
param(
    [string]$CredentialsPath = (Join-Path $env:USERPROFILE '.claude\.credentials.json'),
    [string]$TimelinePath    = (Join-Path $env:USERPROFILE '.claumon\account-timeline.jsonl')
)

$ErrorActionPreference = 'Stop'

$ProfileUrl = 'https://api.anthropic.com/api/oauth/profile'
$LogFile    = Join-Path $PSScriptRoot 'claumon-account-track.log'

function Write-Log($msg) {
    $line = '[{0:yyyy-MM-ddTHH:mm:ssZ}] {1}' -f (Get-Date).ToUniversalTime(), $msg
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

# log 只留最近 200 行，避免無限成長
try {
    if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 256KB)) {
        $tail = Get-Content $LogFile -Tail 200
        Set-Content -Path $LogFile -Value $tail -Encoding UTF8
    }
} catch {}

if (-not (Test-Path $CredentialsPath)) {
    Write-Log "no credentials file at $CredentialsPath (never logged in?) - skip"
    return
}

try {
    $oauth = (Get-Content -Path $CredentialsPath -Raw -Encoding UTF8 | ConvertFrom-Json).claudeAiOauth
} catch {
    Write-Log "credentials unreadable/parse failed: $($_.Exception.Message) - skip"
    return
}
if (-not $oauth -or -not $oauth.accessToken) {
    Write-Log 'no claudeAiOauth.accessToken (API-key mode or different auth) - skip'
    return
}

# 打 profile API 取穩定 account.uuid
try {
    $headers = @{
        Authorization    = "Bearer $($oauth.accessToken)"
        'anthropic-beta' = 'oauth-2025-04-20'
    }
    $prof = Invoke-RestMethod -Method Get -Uri $ProfileUrl -Headers $headers -TimeoutSec 20
} catch {
    Write-Log "profile API call failed: $($_.Exception.Message) - skip"
    return
}
$acct = $prof.account
if (-not $acct -or -not $acct.uuid) {
    Write-Log 'profile response missing account.uuid - skip'
    return
}

# 讀時間軸最後一筆 uuid；相同就不寫
$lastUuid = $null
if (Test-Path $TimelinePath) {
    try {
        $lastLine = Get-Content -Path $TimelinePath -Tail 1 -Encoding UTF8
        if ($lastLine) { $lastUuid = ($lastLine | ConvertFrom-Json).uuid }
    } catch {}
}
if ($lastUuid -eq $acct.uuid) { return }   # 帳號沒變，正常路徑

# 換帳號（或首筆）→ append 一行
$entry = [ordered]@{
    ts           = '{0:yyyy-MM-ddTHH:mm:ssZ}' -f (Get-Date).ToUniversalTime()
    uuid         = $acct.uuid
    email        = $acct.email
    display_name = $acct.display_name
} | ConvertTo-Json -Compress
try {
    $dir = Split-Path -Parent $TimelinePath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    Add-Content -Path $TimelinePath -Value $entry -Encoding UTF8
    Write-Log "account switch recorded: $($acct.email) ($($acct.uuid))"
} catch {
    Write-Log "timeline append failed: $($_.Exception.Message)"
}
```

- [ ] **Step 2: install.ps1 — 複製偵測腳本**

Modify `scripts/install.ps1`：在既有的 token 續期腳本複製區塊（`$refreshDst = "$dir\claumon-token-refresh.ps1"` 那段 `if/else` 結束）之後，新增：

```powershell
    # 一併安裝帳號偵測腳本，供每次心跳偵測換帳號、寫入 account-timeline.jsonl。
    $acctSrc = Join-Path $PSScriptRoot 'claumon-account-track.ps1'
    $acctDst = "$dir\claumon-account-track.ps1"
    if (Test-Path $acctSrc) {
        Copy-Item -Force $acctSrc $acctDst
        Write-Host "已安裝帳號偵測腳本：$acctDst"
    } else {
        Write-Warning "找不到 claumon-account-track.ps1，略過帳號偵測（多帳號分流將無資料）。"
    }
```

- [ ] **Step 3: install.ps1 — watchdog 呼叫偵測腳本**

Modify `scripts/install.ps1`：在產生的 watchdog here-string 內，續期腳本呼叫之後、`'@` 收尾之前，新增帳號偵測呼叫。把

```powershell
$refresh = Join-Path $PSScriptRoot 'claumon-token-refresh.ps1'
if (Test-Path $refresh) { try { & $refresh } catch {} }
'@ | Set-Content -Path $watchdog -Encoding UTF8
```

改為

```powershell
$refresh = Join-Path $PSScriptRoot 'claumon-token-refresh.ps1'
if (Test-Path $refresh) { try { & $refresh } catch {} }
# Detect Claude account switches so claude-usage can split usage per account.
$acct = Join-Path $PSScriptRoot 'claumon-account-track.ps1'
if (Test-Path $acct) { try { & $acct } catch {} }
'@ | Set-Content -Path $watchdog -Encoding UTF8
```

- [ ] **Step 4: 人工驗收（在本機實跑）**

執行偵測腳本一次並確認時間軸出現當前帳號：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\claumon-account-track.ps1
Get-Content "$env:USERPROFILE\.claumon\account-timeline.jsonl" -Tail 3
```

Expected: 出現一行含 `ts / uuid / email / display_name`（email 為你目前登入的帳號）。
再跑一次腳本，時間軸**不應**新增第二行（帳號未變）。
接著 `python -m claude_usage_tracker accounts` 應列出該帳號。

- [ ] **Step 5: Commit**

```bash
git add scripts/claumon-account-track.ps1 scripts/install.ps1
git commit -m "feat(install): detect account switches on watchdog heartbeat"
```

---

### Task 7: 文件

**Files:**
- Modify: `README.md`
- Modify: `docs/setup-guide.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: README — 新增「多帳號分流」小節**

Modify `README.md`：於「重要前提」小節之後、「額度儀表為什麼會斷線」之前插入：

```markdown
## 多帳號分流

若你在同一台機器 `claude` 登入不同帳號，claumon 的**即時**額度儀表會自動切到新帳號
（它每次都即時讀憑證檔）；但它寫的 `usage.db` **沒有帳號欄位**，歷史會混在一起。

本工具透過 watchdog 每 3 分鐘心跳執行的 `claumon-account-track.ps1` 偵測換帳號
（打 profile API 取穩定的 `account.uuid`），只在換帳號時把邊界追加到
`~/.claumon/account-timeline.jsonl`。之後即可依帳號檢視：

```bash
claude-usage accounts                              # 列出已知帳號
claude-usage chart --all --account alice@x.com     # 只看該帳號的用量
claude-usage export --account alice@x.com          # utilization 依帳號切
```

- `--account` 接受 email、uuid，或 email/顯示名稱的子字串（需唯一）。
- 未給 `--account` 時行為不變；混帳號的圖會在切換點標出換帳號（`--no-account-markers` 可關）。
- **注意**：偵測開始前的舊資料視為「未歸屬」，`--account` 不含；邊界精度 ≈ 3 分鐘心跳。
- export 的 `--account` **只作用於 `utilization_*.csv`**（額度快照）；`monthly_summary.csv` /
  `daily_*.csv`（token/成本）為日粒度、本機層級，維持全量不切。
```
```

- [ ] **Step 2: README — 專案結構補一列**

Modify `README.md`：在專案結構的 `scripts/` 區塊，`claumon-token-refresh.ps1` 那列之後新增：

```
│   └── claumon-account-track.ps1   # 換帳號偵測，寫 account-timeline.jsonl（watchdog 每 3 分鐘呼叫）
```

（並把上一列 `claumon-token-refresh.ps1` 結尾的樹狀符號由 `└──` 改為 `├──`。）

- [ ] **Step 3: setup-guide 補一句**

Modify `docs/setup-guide.md`：在說明 watchdog / 續期腳本部署的段落，補一句：

```markdown
安裝也會部署 `claumon-account-track.ps1`，由 watchdog 每 3 分鐘偵測 `claude` 是否換了帳號，
換帳號時寫入 `~/.claumon/account-timeline.jsonl`，供 `claude-usage --account` / `accounts` 分流用量。
```

（若 setup-guide 無對應段落，則附加於文末的「背景常駐」相關章節。）

- [ ] **Step 4: CHANGELOG 補一條**

Modify `CHANGELOG.md`：於最新版本區塊的 Added 清單新增：

```markdown
- 多帳號分流：自動偵測 `claude` 換帳號，`chart`/`export` 新增 `--account`、新增 `accounts`
  子指令、混帳號圖表換帳號標記。
```

（若 CHANGELOG 結構不同，依其既有格式加入等義條目。）

- [ ] **Step 5: Commit**

```bash
git add README.md docs/setup-guide.md CHANGELOG.md
git commit -m "docs: document multi-account split usage"
```

---

## Self-Review 紀錄

**Spec coverage：**
- §A 偵測腳本 → Task 6 Step 1；§B install/watchdog → Task 6 Step 2-3。
- §C account.py → Task 1；§D db 過濾 → Task 2；§E cli（--account/accounts/檔名短碼）→ Task 5；
  export 只切 utilization → Task 4；§F 圖表標記 → Task 3（繪製）+ Task 5（cli 串接）；
  §G 文件 → Task 7；§H 測試 → Task 1/2/4/5 內含。
- 全部 spec 區塊皆有對應 task。

**Placeholder scan：** 無 TBD/TODO；每個 code step 均含完整程式碼與確切指令。

**Type consistency：**
- `load_snapshots(..., utc_intervals=...)` 於 Task 2 定義，Task 4/5 一致引用。
- `account_intervals` 回 `Dict[uuid -> List[(start,end)]]`；Task 5 以 `.get(uuid, [])` 取用一致。
- `chart.run(..., account_tag=, markers=)` 於 Task 3 定義，Task 5 一致呼叫；
  `export.run(..., utc_intervals=, account_tag=)` 於 Task 4 定義，Task 5 一致呼叫。
- `_resolve_intervals` 回 `(utc_intervals, account_tag)`，Task 5 內部一致使用。
```
