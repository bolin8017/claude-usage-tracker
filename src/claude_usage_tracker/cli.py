"""命令列介面：claude-usage weekly / import-claumon（每小時記錄），export / chart / accounts（舊 claumon 資料庫）。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import __version__
from . import chart as chart_mod
from . import export as export_mod
from . import usagelog
from . import weekly as weekly_mod
from .account import (AccountSelectionError, Boundary, account_intervals,
                      boundaries_for_range, default_timeline_path,
                      list_accounts, load_timeline, resolve_account)
from .db import connect_ro, default_db_path, load_snapshots


_SHORT_UUID_LEN = 6


def _short(uuid: str) -> str:
    return uuid[:_SHORT_UUID_LEN]


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
    return intervals, _short(uuid)


def _markers_for(timeline, first_local, last_local):
    """Chart account-switch markers for the plotted local-time span.

    first_local / last_local are naive local datetimes (the Record time axis).
    Returns a list of (naive_local_time, label) or None if no switch falls in range.
    """
    start_utc = first_local.astimezone(timezone.utc)
    end_utc = last_local.astimezone(timezone.utc)
    bs = boundaries_for_range(timeline, start_utc, end_utc)
    if not bs:
        return None
    return [(b.ts.astimezone().replace(tzinfo=None), b.email or _short(b.uuid)) for b in bs]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="claude-usage",
        description="Claude 訂閱用量記錄的每週報表，以及舊 claumon 資料的匯出與視覺化")
    ap.add_argument("--version", action="version",
                    version=f"claude-usage-tracker {__version__}")
    ap.add_argument("--db", default=default_db_path(),
                    help="usage.db 路徑（預設 ~/.claumon/usage.db）")
    ap.add_argument("--log", default=usagelog.default_log_path(),
                    help="每小時用量記錄檔（預設 ~/.claude-usage/usage-log.jsonl）")
    sub = ap.add_subparsers(dest="command", required=True)

    pw = sub.add_parser("weekly", help="每週 weekly 額度用量報表（CSV + 長條圖）")
    gw = pw.add_mutually_exclusive_group()
    gw.add_argument("--month", help="週期結束日落在此月份 YYYY-MM（預設本月）")
    gw.add_argument("--all", action="store_true", help="全部週期")
    pw.add_argument("--account", help="只看指定帳號（email、uuid 或其子字串）")
    pw.add_argument("--out", default="exports", help="輸出資料夾（預設 ./exports）")
    pw.add_argument("--no-chart", action="store_true", help="只輸出 CSV，不畫圖")

    pi = sub.add_parser("import-claumon",
                        help="把舊 claumon 資料庫的 weekly 歷史匯入記錄檔（只需執行一次）")
    pi.add_argument("--account", help="只匯入指定帳號（email、uuid 或其子字串）")


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
            print(f"{a.email:<32}{_short(a.uuid):<8}{fs:<18}{ls:<18}{cnt}")
    finally:
        con.close()


def _filter_account(samples, query):
    """依 --account 篩選樣本；對不到唯一帳號丟 AccountSelectionError。"""
    seen = {}
    for s in samples:
        seen[s.uuid] = Boundary(s.ts, s.uuid, s.email, "")
    uuid = resolve_account(list(seen.values()), query)
    return [s for s in samples if s.uuid == uuid]


def _run_weekly(args) -> None:
    _validate_month(args.month)
    samples = usagelog.load_samples(args.log)
    if not samples:
        sys.exit(f"[提醒] 記錄檔沒有資料：{args.log}")
    if args.account:
        try:
            samples = _filter_account(samples, args.account)
        except AccountSelectionError as e:
            sys.exit(f"[錯誤] {e}")
    month = None if args.all else (args.month or datetime.now().strftime("%Y-%m"))
    rows = [r for r in usagelog.weekly_rows(samples, datetime.now(timezone.utc))
            if weekly_mod.in_month(r, month)]
    if not rows:
        sys.exit("[提醒] 指定範圍內沒有任何週期資料。")
    scope = month or "all"
    if args.account:
        scope += f"_{_short(rows[0].uuid)}"
    print(f"記錄檔：{args.log}\n")
    weekly_mod.run(rows, out_dir=args.out, scope=scope, no_chart=args.no_chart)


def _run_import(args) -> None:
    if usagelog.has_claumon_import(args.log):
        print(f"記錄檔已含 claumon 匯入資料，略過：{args.log}")
        return
    timeline = load_timeline(default_timeline_path())
    if not timeline:
        sys.exit("[錯誤] 找不到帳號時間軸 ~/.claumon/account-timeline.jsonl，"
                 "無法判斷快照屬於哪個帳號。")
    only = None
    if args.account:
        try:
            only = resolve_account(timeline, args.account)
        except AccountSelectionError as e:
            sys.exit(f"[錯誤] {e}")
    con = connect_ro(args.db)
    try:
        records, report = usagelog.claumon_records(con, timeline, only_uuid=only)
    finally:
        con.close()

    print(f"{'week_end':<18}{'account':<32}{'share':>6}  snapshots")
    for w in report:
        who = (w.owner.email or w.owner.uuid) if w.owner else "（沒有過半數，不匯入）"
        print(f"{w.window_end.astimezone().strftime('%Y-%m-%d %H:%M'):<18}{who:<32}"
              f"{w.share:>6.0%}  {w.snapshots}")
    usagelog.append_records(args.log, records)
    print(f"\n[OK] 已匯入 {len(records)} 筆到 {args.log}")


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "weekly":
        _run_weekly(args)
        return
    if args.command == "import-claumon":
        _run_import(args)
        return

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
        markers = _markers_for(timeline, recs[0].time, recs[-1].time)

    scope = args.month if args.month else ("all" if args.all else f"last{args.days}d")
    chart_mod.run(recs, out_dir=args.out, scope=scope, series=series,
                  resample_min=args.resample, agg=args.agg, no_chart=args.no_chart,
                  account_tag=account_tag, markers=markers)


if __name__ == "__main__":
    main()
