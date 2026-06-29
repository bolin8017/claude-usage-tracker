"""命令列介面：claude-usage export / chart。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__
from . import chart as chart_mod
from . import export as export_mod
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

    pc = sub.add_parser("chart", help="輸出時間序列 CSV 與曲線圖")
    pc.add_argument("--out", default="exports", help="輸出資料夾（預設 ./exports）")
    pc.add_argument("--series", default="session,weekly",
                    help="序列：session,weekly,sonnet（可組合，預設 session,weekly）")
    pc.add_argument("--resample", type=int, default=0,
                    help="降採樣間隔（分鐘）；0=原始 2 分鐘密度")
    pc.add_argument("--agg", choices=["max", "mean", "last"], default="max",
                    help="降採樣聚合方式（預設 max=桶內峰值）")
    pc.add_argument("--no-chart", action="store_true", help="只輸出 CSV，不畫圖")
    _add_range(pc)
    return ap


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    print(f"資料庫：{args.db}")

    if args.command == "export":
        _validate_month(args.month)
        con = connect_ro(args.db)
        try:
            export_mod.run(con, out_dir=args.out, month=args.month)
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
        recs = load_snapshots(con, month=args.month, days=args.days, all_=args.all)
    finally:
        con.close()

    if not recs:
        sys.exit("[提醒] 指定範圍內沒有任何快照資料。")

    scope = args.month if args.month else ("all" if args.all else f"last{args.days}d")
    chart_mod.run(recs, out_dir=args.out, scope=scope, series=series,
                  resample_min=args.resample, agg=args.agg, no_chart=args.no_chart)


if __name__ == "__main__":
    main()
