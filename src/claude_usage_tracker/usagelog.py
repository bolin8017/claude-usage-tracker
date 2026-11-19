"""每小時用量記錄（~/.claude-usage/usage-log.jsonl）的讀取、每週彙整與 claumon 歷史匯入。

每行一筆 JSON：{ts, uuid, email, display_name, source, raw}。
raw 是 usage API 的原始回應，欄位解析
集中在本模組，API 改欄位時只需改這裡、舊資料可重新解析。

weekly 額度在同一帳號、同一週期內只增不減，除非 Anthropic 在週期中途把額度歸零
（實測 claumon 歷史中出現過三次，2 分鐘內從 61～98% 掉到 0%，resets_at 不變）。
因此每個週期以「下降」切成數段，各段最大值相加才是該週實際用量。
週期以 seven_day.resets_at 辨識（同一週期的 resets_at 會在整點前後幾秒抖動，
因此取最接近的整點當鍵）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
import sqlite3
from bisect import bisect_right
from collections import Counter
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .account import Boundary

WEEK = timedelta(days=7)
# 最後一筆採樣距週期結束超過這個時間，該週數字可能偏低
COMPLETE_GAP = timedelta(hours=2)
# 比前一段最大值低超過這個百分點，視為週期中途歸零（API 數值為整數，容許 1 點誤差）
RESET_TOLERANCE = 1.0
# 報表的用途分類（seven_day_breakdown.rows[].key）
SURFACES = ("claude_code", "chat", "cowork", "other")


def default_log_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude-usage", "usage-log.jsonl")


class Sample(NamedTuple):
    ts: datetime                 # aware UTC
    uuid: str
    email: str
    weekly_pct: float
    window_end: datetime         # aware UTC，取整點
    breakdown: Dict[str, float]  # surface key -> percent_of_limit


class WeekRow(NamedTuple):
    uuid: str
    email: str
    window_start: datetime       # aware UTC
    window_end: datetime         # aware UTC
    used_pct: float              # 各段最大值相加
    segment_peaks: Tuple[float, ...]  # 中途歸零時有多段
    breakdown: Dict[str, float]  # 各段最大值那筆的分類相加
    samples: int
    last_sample: datetime        # aware UTC
    status: str                  # complete / in_progress / incomplete


def _parse_iso(raw: str) -> datetime:
    s = raw.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s) if "." not in s else _parse_frac(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_frac(s: str) -> datetime:
    # Python 3.9/3.10 的 fromisoformat 只吃 3 或 6 位小數；統一補/截成 6 位
    head, rest = s.split(".", 1)
    i = 0
    while i < len(rest) and rest[i].isdigit():
        i += 1
    frac = (rest[:i] + "000000")[:6]
    return datetime.fromisoformat(f"{head}.{frac}{rest[i:]}")


def round_to_hour(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


def parse_usage(raw: dict) -> Optional[Tuple[float, datetime, Dict[str, float]]]:
    """從 usage API 回應取出 (weekly_pct, window_end, breakdown)；沒有 weekly 資料回 None。"""
    sd = raw.get("seven_day") or {}
    pct, reset = sd.get("utilization"), sd.get("resets_at")
    if pct is None or not reset:
        return None
    breakdown: Dict[str, float] = {}
    for row in (raw.get("seven_day_breakdown") or {}).get("rows") or []:
        if row.get("key") and row.get("percent_of_limit") is not None:
            breakdown[row["key"]] = float(row["percent_of_limit"])
    return float(pct), round_to_hour(_parse_iso(reset)), breakdown


def load_samples(path: str) -> List[Sample]:
    """讀記錄檔；檔案不存在回 []，壞行與沒有 weekly 資料的行略過。依時間排序。"""
    if not os.path.exists(path):
        return []
    out: List[Sample] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                parsed = parse_usage(d.get("raw") or {})
                if parsed is None:
                    continue
                pct, wend, bd = parsed
                out.append(Sample(_parse_iso(d["ts"]), d["uuid"], d.get("email", ""),
                                  pct, wend, bd))
            except Exception:
                continue
    out.sort(key=lambda s: s.ts)
    return out


def split_segments(grp: List[Sample]) -> List[List[Sample]]:
    """依時間排序的同一週期樣本，遇到比本段最大值低超過 RESET_TOLERANCE 就切新段。"""
    segs: List[List[Sample]] = []
    cur: List[Sample] = []
    best = None
    for s in grp:
        if cur and s.weekly_pct < best - RESET_TOLERANCE:
            segs.append(cur)
            cur, best = [], None
        cur.append(s)
        best = s.weekly_pct if best is None else max(best, s.weekly_pct)
    if cur:
        segs.append(cur)
    return segs


def weekly_rows(samples: Iterable[Sample], now_utc: datetime) -> List[WeekRow]:
    """依 (帳號, 週期) 分組；週期內各段（中途歸零切段）最大值相加為該週用量。"""
    groups: Dict[Tuple[str, datetime], List[Sample]] = {}
    for s in samples:
        groups.setdefault((s.uuid, s.window_end), []).append(s)
    rows: List[WeekRow] = []
    for (uuid, wend), grp in groups.items():
        grp.sort(key=lambda s: s.ts)
        peaks = [max(seg, key=lambda s: (s.weekly_pct, s.ts)) for seg in split_segments(grp)]
        breakdown: Dict[str, float] = {}
        for p in peaks:
            for k, v in p.breakdown.items():
                breakdown[k] = breakdown.get(k, 0.0) + v
        last = grp[-1].ts
        if now_utc < wend:
            status = "in_progress"
        elif wend - last <= COMPLETE_GAP:
            status = "complete"
        else:
            status = "incomplete"
        rows.append(WeekRow(uuid, grp[-1].email, wend - WEEK, wend,
                            sum(p.weekly_pct for p in peaks),
                            tuple(p.weekly_pct for p in peaks),
                            breakdown, len(grp), last, status))
    rows.sort(key=lambda r: (r.window_end, r.email))
    return rows


# ---------------------------------------------------------------------------
# claumon 歷史匯入
# ---------------------------------------------------------------------------

class WindowAttribution(NamedTuple):
    window_end: datetime
    owner: Optional[Boundary]    # None = 沒有過半數，不匯入
    share: float                 # 多數帳號占該週期快照的比例
    snapshots: int


def _owner_index(timeline: List[Boundary]):
    times = [b.ts for b in timeline]

    def owner(t: datetime) -> Optional[Boundary]:
        i = bisect_right(times, t)
        return timeline[i - 1] if i else None
    return owner


def claumon_records(con: sqlite3.Connection, timeline: List[Boundary],
                    only_uuid: Optional[str] = None
                    ) -> Tuple[List[dict], List[WindowAttribution]]:
    """把 claumon 的 usage_snapshots 轉成記錄檔格式。

    帳號歸屬以「週期」為單位：claumon 把 token 快取在記憶體，切換帳號後要等舊 token
    快過期才重讀憑證檔，所以單筆快照依時間軸歸屬會在切換後錯好幾小時。但一個週期
    （resets_at）只屬於一個帳號，因此整個週期歸給時間軸上占多數的帳號；沒有過半數的
    週期不匯入。

    密度降到與每小時記錄相同：每小時留最後一筆，另外保留每段（中途歸零前）的最大值。
    raw 只留 weekly 相關欄位，避免匯入資料過大。
    """
    owner_at = _owner_index(timeline)
    by_window: Dict[datetime, List[Tuple[datetime, dict, Optional[Boundary]]]] = {}
    for ts, raw_json in con.execute(
            "SELECT timestamp, raw_json FROM usage_snapshots ORDER BY id"):
        try:
            raw = json.loads(raw_json)
            t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        parsed = parse_usage(raw)
        if parsed is None:
            continue
        by_window.setdefault(parsed[1], []).append((t, raw, owner_at(t)))

    records: List[dict] = []
    report: List[WindowAttribution] = []
    for wend in sorted(by_window):
        items = sorted(by_window[wend], key=lambda x: x[0])
        votes = Counter(o.uuid for _, _, o in items if o is not None)
        if not votes:
            report.append(WindowAttribution(wend, None, 0.0, len(items)))
            continue
        top, n = votes.most_common(1)[0]
        share = n / len(items)
        owner = next(o for _, _, o in reversed(items) if o is not None and o.uuid == top)
        if share <= 0.5:
            report.append(WindowAttribution(wend, None, share, len(items)))
            continue
        report.append(WindowAttribution(wend, owner, share, len(items)))
        if only_uuid and owner.uuid != only_uuid:
            continue
        pcts = [parse_usage(r)[0] for _, r, _ in items]
        for i, (t, raw, _) in enumerate(items):
            last = i + 1 == len(items)
            hour_end = last or items[i + 1][0].replace(minute=0, second=0) != t.replace(minute=0, second=0)
            seg_end = not last and pcts[i + 1] < pcts[i] - RESET_TOLERANCE
            if not (hour_end or seg_end):
                continue
            records.append({
                "ts": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "uuid": owner.uuid,
                "email": owner.email,
                "display_name": owner.display_name,
                "source": "claumon",
                "raw": {k: raw.get(k) for k in ("seven_day", "seven_day_breakdown")},
            })
    return records, report


def has_claumon_import(path: str) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        return any('"source":"claumon"' in line for line in f)


def append_records(path: str, records: List[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
