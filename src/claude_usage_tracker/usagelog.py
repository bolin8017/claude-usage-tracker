"""每小時用量記錄（~/.claude-usage/usage-log.jsonl）的讀取與每週彙整。

每行一筆 JSON：{ts, uuid, email, display_name, source, raw}。
raw 是 usage API 的原始回應，欄位解析
集中在本模組，API 改欄位時只需改這裡、舊資料可重新解析。

weekly 額度在同一帳號、同一週期內只增不減，所以「週期內的最大值」就是該週用量；
週期以 seven_day.resets_at 辨識（同一週期的 resets_at 會在整點前後幾秒抖動，
因此取最接近的整點當鍵）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

WEEK = timedelta(days=7)
# 最後一筆採樣距週期結束超過這個時間，該週數字可能偏低
COMPLETE_GAP = timedelta(hours=2)
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
    peak_pct: float
    breakdown: Dict[str, float]
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


def weekly_rows(samples: Iterable[Sample], now_utc: datetime) -> List[WeekRow]:
    """依 (帳號, 週期) 分組，每組取 weekly 最大值當該週用量。"""
    groups: Dict[Tuple[str, datetime], List[Sample]] = {}
    for s in samples:
        groups.setdefault((s.uuid, s.window_end), []).append(s)
    rows: List[WeekRow] = []
    for (uuid, wend), grp in groups.items():
        grp.sort(key=lambda s: s.ts)
        peak = max(grp, key=lambda s: (s.weekly_pct, s.ts))
        last = grp[-1].ts
        if now_utc < wend:
            status = "in_progress"
        elif wend - last <= COMPLETE_GAP:
            status = "complete"
        else:
            status = "incomplete"
        rows.append(WeekRow(uuid, grp[-1].email, wend - WEEK, wend, peak.weekly_pct,
                            peak.breakdown, len(grp), last, status))
    rows.sort(key=lambda r: (r.window_end, r.email))
    return rows
