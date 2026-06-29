"""每月用量彙整：從 daily_aggregates / usage_snapshots 匯出 CSV。"""
from __future__ import annotations

import csv
import os
import sqlite3
from typing import Optional


def _write_csv(path: str, header, rows) -> int:
    # utf-8-sig 讓 Excel 正確辨識編碼與中文
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def _monthly_summary(con: sqlite3.Connection, out_dir: str):
    sql = """
        SELECT substr(date,1,7) AS month,
               SUM(input_tokens), SUM(output_tokens),
               SUM(cache_read_tokens), SUM(cache_create_tokens),
               SUM(input_tokens+output_tokens+cache_read_tokens+cache_create_tokens),
               ROUND(SUM(cost_usd),4),
               SUM(session_count), SUM(message_count), COUNT(*)
        FROM daily_aggregates GROUP BY month ORDER BY month
    """
    header = ["month", "input_tokens", "output_tokens", "cache_read_tokens",
              "cache_create_tokens", "total_tokens", "cost_usd",
              "session_count", "message_count", "active_days"]
    path = os.path.join(out_dir, "monthly_summary.csv")
    return path, _write_csv(path, header, con.execute(sql).fetchall())


def _daily(con: sqlite3.Connection, out_dir: str, month: Optional[str], scope: str):
    sql = ("SELECT date, input_tokens, output_tokens, cache_read_tokens, "
           "cache_create_tokens, "
           "input_tokens+output_tokens+cache_read_tokens+cache_create_tokens, "
           "ROUND(cost_usd,4), session_count, message_count "
           "FROM daily_aggregates ")
    params: tuple = ()
    if month:
        sql += "WHERE substr(date,1,7)=? "
        params = (month,)
    sql += "ORDER BY date"
    header = ["date", "input_tokens", "output_tokens", "cache_read_tokens",
              "cache_create_tokens", "total_tokens", "cost_usd",
              "session_count", "message_count"]
    path = os.path.join(out_dir, f"daily_{scope}.csv")
    return path, _write_csv(path, header, con.execute(sql, params).fetchall())


def _utilization(con: sqlite3.Connection, out_dir: str, month: Optional[str], scope: str):
    sql = ("SELECT substr(timestamp,1,7) AS month, "
           "ROUND(MAX(session_pct),1), ROUND(AVG(session_pct),1), "
           "ROUND(MAX(weekly_pct),1), ROUND(AVG(weekly_pct),1), COUNT(*) "
           "FROM usage_snapshots ")
    params: tuple = ()
    if month:
        sql += "WHERE substr(timestamp,1,7)=? "
        params = (month,)
    sql += "GROUP BY month ORDER BY month"
    header = ["month", "session_pct_max", "session_pct_avg",
              "weekly_pct_max", "weekly_pct_avg", "snapshots"]
    path = os.path.join(out_dir, f"utilization_{scope}.csv")
    return path, _write_csv(path, header, con.execute(sql, params).fetchall())


def run(con: sqlite3.Connection, *, out_dir: str, month: Optional[str]) -> None:
    """匯出月度彙整、每日明細、額度峰值三份 CSV。"""
    scope = month if month else "all"
    os.makedirs(out_dir, exist_ok=True)
    print(f"範圍　：{scope}")
    print(f"輸出至：{out_dir}\n")

    p1, n1 = _monthly_summary(con, out_dir)
    print(f"[OK] {os.path.basename(p1)}  ({n1} 個月)")
    p2, n2 = _daily(con, out_dir, month, scope)
    print(f"[OK] {os.path.basename(p2)}  ({n2} 天)")
    p3, n3 = _utilization(con, out_dir, month, scope)
    print(f"[OK] {os.path.basename(p3)}  ({n3} 個月)")

    if n2 == 0:
        print("\n[提醒] daily 明細為 0 筆：此機器尚無 Claude Code session 的 "
              "token/成本紀錄（額度快照仍正常累積）。")
