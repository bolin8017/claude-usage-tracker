"""claumon SQLite 資料庫存取（唯讀）。

所有時間在資料庫中以 UTC 儲存；本模組對外一律回傳 *naive 本地時間*，
方便下游直接寫入 CSV 或繪圖。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple, Optional


class Record(NamedTuple):
    """單筆額度快照（時間已轉為 naive 本地時間）。"""
    time: datetime
    session: Optional[float]
    weekly: Optional[float]
    sonnet: Optional[float]


def default_db_path() -> str:
    """claumon 預設資料庫位置 ~/.claumon/usage.db。"""
    return os.path.join(os.path.expanduser("~"), ".claumon", "usage.db")


def connect_ro(db_path: str) -> sqlite3.Connection:
    """以唯讀模式開啟資料庫，避免干擾正在運行的 claumon 服務。"""
    if not os.path.exists(db_path):
        sys.exit(f"[錯誤] 找不到資料庫：{db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _utc_to_local(ts_str: str) -> datetime:
    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def _parse_utc(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _in_intervals(t: datetime, intervals) -> bool:
    return any(s <= t < e for s, e in intervals)


def _sonnet_pct(raw_json: str) -> Optional[float]:
    try:
        sd = json.loads(raw_json).get("seven_day_sonnet")
        if sd and sd.get("utilization") is not None:
            return round(float(sd["utilization"]), 1)
    except Exception:
        pass
    return None


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
