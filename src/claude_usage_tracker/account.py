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
