"""每日月報：一個月每天一列，顯示當天結束時的 weekly 使用率。

備註標出當天發生的事件：週期重置（resets_at 換了）與 reset（同一週期內下降）。
事件歸在「事件後第一筆樣本」的日期；判斷時會拿前一筆比對，包含上個月的最後一筆，
所以跨日、跨月都偵測得到。
"""
from __future__ import annotations

import calendar
import csv
import os
from datetime import date, datetime
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from .usagelog import RESET_TOLERANCE, Sample


class Event(NamedTuple):
    kind: str            # "window" = 週期重置, "reset" = 使用者 reset
    prev_peak: float     # 事件前的最高值


class DayRow(NamedTuple):
    day: date
    pct: Optional[float]               # 當天最後一筆；沒資料為 None
    last_sample: Optional[datetime]    # 當天最後一筆的本地時間
    events: Tuple[Event, ...]
    future: bool                       # 今天之後的日子


def _local(dt: datetime) -> datetime:
    return dt.astimezone().replace(tzinfo=None)


def _events_by_day(samples: Sequence[Sample]) -> Dict[date, List[Event]]:
    out: Dict[date, List[Event]] = {}
    prev: Optional[Sample] = None
    win_peak = seg_peak = None
    for s in samples:
        ev = None
        if prev is not None and s.window_end != prev.window_end:
            ev = Event("window", win_peak)
            win_peak = seg_peak = None
        elif prev is not None and s.weekly_pct < seg_peak - RESET_TOLERANCE:
            ev = Event("reset", seg_peak)
            seg_peak = None
        if ev:
            out.setdefault(_local(s.ts).date(), []).append(ev)
        win_peak = s.weekly_pct if win_peak is None else max(win_peak, s.weekly_pct)
        seg_peak = s.weekly_pct if seg_peak is None else max(seg_peak, s.weekly_pct)
        prev = s
    return out


def daily_rows(samples: Sequence[Sample], month: str, today: date) -> List[DayRow]:
    """samples 須為單一帳號、依時間排序；回傳該月每天一列。"""
    y, m = map(int, month.split("-"))
    ndays = calendar.monthrange(y, m)[1]
    last_end = date(y, m, ndays)
    upto = [s for s in samples if _local(s.ts).date() <= last_end]
    events = _events_by_day(upto)
    last_of_day: Dict[date, Sample] = {}
    for s in upto:
        last_of_day[_local(s.ts).date()] = s
    rows = []
    for d in range(1, ndays + 1):
        day = date(y, m, d)
        s = last_of_day.get(day)
        rows.append(DayRow(day, s.weekly_pct if s else None,
                           _local(s.ts) if s else None,
                           tuple(events.get(day, ())), day > today))
    return rows


def note_text(row: DayRow) -> str:
    parts = []
    for e in row.events:
        if e.kind == "window":
            parts.append(f"週期重置（前一週最高 {e.prev_peak:g}%）")
        else:
            parts.append(f"reset（reset 前 {e.prev_peak:g}%）")
    if row.pct is None and not row.future:
        parts.append("無資料")
    return "；".join(parts)


def write_csv(rows: List[DayRow], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "weekly_pct", "last_sample", "note"])
        for r in rows:
            w.writerow([r.day.isoformat(),
                        "" if r.pct is None else r.pct,
                        r.last_sample.strftime("%H:%M") if r.last_sample else "",
                        note_text(r)])


def write_chart(rows: List[DayRow], path: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = [r.day.day for r in rows]
    ys = [float("nan") if r.pct is None else r.pct for r in rows]   # NaN 讓線條在無資料處斷開
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(xs, ys, marker="o", markersize=4, color="#2563eb", linewidth=1.8)
    for r in rows:
        for e in r.events:
            if e.kind == "window":
                ax.axvline(r.day.day - 0.5, color="#9ca3af", linestyle="--", linewidth=1)
                ax.annotate(f"new week\n(prev {e.prev_peak:g}%)", (r.day.day - 0.5, 104),
                            ha="center", va="top", fontsize=7, color="#6b7280")
            else:
                ax.annotate(f"reset\n(was {e.prev_peak:g}%)", (r.day.day, r.pct or 0),
                            xytext=(8, -22), textcoords="offset points",
                            ha="left", fontsize=7, color="#dc2626")
                if r.pct is not None:
                    ax.plot([r.day.day], [r.pct], marker="v", color="#dc2626", markersize=8)
    ax.set_xlim(0.5, len(rows) + 0.5)
    ax.set_xticks(xs)
    ax.tick_params(axis="x", labelsize=8)
    ax.set_ylim(0, 110)
    ax.set_xlabel("Day of month")
    ax.set_ylabel("Weekly limit used at end of day (%)")
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(rows: List[DayRow], *, out_dir: str, month: str, no_chart: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    print(f"{'date':<12}{'weekly':>7}  {'time':<6} note")
    for r in rows:
        if r.future:
            continue
        pct = "－" if r.pct is None else f"{r.pct:.0f}%"
        t = r.last_sample.strftime("%H:%M") if r.last_sample else ""
        print(f"{r.day.isoformat():<12}{pct:>7}  {t:<6} {note_text(r)}")
    csv_path = os.path.join(out_dir, f"usage_daily_{month}.csv")
    write_csv(rows, csv_path)
    print(f"\n[OK] {csv_path}")
    if not no_chart:
        png_path = os.path.join(out_dir, f"usage_daily_{month}.png")
        write_chart(rows, png_path, f"Claude weekly usage by day ({month})")
        print(f"[OK] {png_path}")
