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


def _add_range(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--days", type=int, default=14, help="最近 N 天（預設 14）")
    g.add_argument("--month", help="指定月份 YYYY-MM")
    g.add_argument("--all", action="store_true", help="全部資料")


def _validate_month(month: str) -> None:
    if month:
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            sys.exit("[錯誤] --month 格式須為 YYYY-MM")


def _resolve_intervals(timeline, account, now_utc):
    """把 --account 解析成 (utc_intervals, account_tag)；account 為 None 回 (None, None)。"""
    if not account:
        return None, None
    uuid = resolve_account(timeline, account)      # 失敗丟 AccountSelectionError
    intervals = account_intervals(timeline, now_utc).get(uuid, [])
    return intervals, uuid[:6]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="claude-usage",
        description="claumon 用量資料匯出與視覺化工具")
    ap.add_argument("--version", action="version",
                    version=f"claude-usage-tracker {__version__}")
    ap.add_argument("--db", default=default_db_path(),
                    help="usage.db 路徑（預設 ~/.claumon/usage.db）")
    sub = ap.add_subparsers(dest="command", required=True)

    pe = sub.add_parser("export", help="匯出每月彙整 CSV（token / 成本 / 額度峰值）")
    pe.add_argument("--out", default="exports", help="輸出資料夾（預設 ./exports）")
    pe.add_argument("--month", help="只匯出指定月份 YYYY-MM")
    pe.add_argument("--account", help="只匯出指定帳號（email 或 uuid）的 utilization")

    pc = sub.add_parser("chart", help="輸出時間序列 CSV 與曲線圖")
    pc.add_argument("--out", default="exports", help="輸出資料夾（預設 ./exports）")
    pc.add_argument("--series", default="session,weekly",
                    help="序列：session,weekly,sonnet（可組合，預設 session,weekly）")
    pc.add_argument("--resample", type=int, default=0,
                    help="降採樣間隔（分鐘）；0=原始 2 分鐘密度")
    pc.add_argument("--agg", choices=["max", "mean", "last"], default="max",
                    help="降採樣聚合方式（預設 max=桶內峰值）")
    pc.add_argument("--no-chart", action="store_true", help="只輸出 CSV，不畫圖")
    pc.add_argument("--account", help="只畫指定帳號（email 或 uuid）的用量")
    pc.add_argument("--no-account-markers", action="store_true",
                    help="不在混帳號圖上畫換帳號標記")
    _add_range(pc)

    sub.add_parser("accounts", help="列出時間軸上已知的 Claude 帳號")

    return ap


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


if __name__ == "__main__":
    main()
