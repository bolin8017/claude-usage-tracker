"""每週用量報表：把 WeekRow 輸出成 CSV 與長條圖。"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import List, Optional

from .usagelog import SURFACES, WeekRow

STATUS_TEXT = {
    "complete": "完整",
    "in_progress": "進行中",
    "incomplete": "可能偏低",
}


def _local(dt: datetime) -> datetime:
    return dt.astimezone().replace(tzinfo=None)


def in_month(row: WeekRow, month: Optional[str]) -> bool:
    """週期歸屬於其結束日（本地時間）所在的月份；month 為 None 表示全部。"""
    return month is None or _local(row.window_end).strftime("%Y-%m") == month


def gap_hours(row: WeekRow) -> float:
    return round((row.window_end - row.last_sample).total_seconds() / 3600, 1)


def write_csv(rows: List[WeekRow], path: str) -> None:
    header = (["account", "week_start", "week_end", "weekly_pct"]
              + [f"{s}_pct" for s in SURFACES]
              + ["resets", "samples", "last_sample", "hours_unsampled_before_reset",
                 "status"])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.email or r.uuid,
                        _local(r.window_start).strftime("%Y-%m-%d %H:%M"),
                        _local(r.window_end).strftime("%Y-%m-%d %H:%M"),
                        r.peak_pct]
                       + [r.breakdown.get(s, "") for s in SURFACES]
                       + [r.resets, r.samples,
                          _local(r.last_sample).strftime("%Y-%m-%d %H:%M"),
                          max(gap_hours(r), 0.0),
                          STATUS_TEXT[r.status]])


def write_chart(rows: List[WeekRow], path: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    accounts = sorted({r.email or r.uuid for r in rows})
    weeks = sorted({r.window_end for r in rows})
    width = 0.8 / len(accounts)
    fig, ax = plt.subplots(figsize=(max(6, 1.4 * len(weeks) + 2), 4.5))
    for i, acct in enumerate(accounts):
        by_week = {r.window_end: r for r in rows if (r.email or r.uuid) == acct}
        xs, ys = [], []
        for x, wk in enumerate(weeks):
            if wk in by_week:
                xs.append(x - 0.4 + width * (i + 0.5))
                ys.append(by_week[wk].peak_pct)
        bars = ax.bar(xs, ys, width=width * 0.9, label=acct)
        for b, y in zip(bars, ys):
            ax.annotate(f"{y:.0f}%", (b.get_x() + b.get_width() / 2, y),
                        ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(weeks)))
    ax.set_xticklabels([_local(w).strftime("%m/%d") for w in weeks])
    ax.set_xlabel("Week ending (local date)")
    ax.set_ylabel("Weekly limit used (%)")
    ax.set_ylim(0, 110)
    ax.set_title(title)
    if len(accounts) > 1:
        ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(rows: List[WeekRow], *, out_dir: str, scope: str, no_chart: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    print(f"{'account':<30}{'week_end':<18}{'weekly':>7}  {'samples':>7}  status")
    for r in rows:
        note = STATUS_TEXT[r.status]
        if r.resets:
            note += f"，週期中途 reset {r.resets} 次"
        if r.status == "incomplete":
            note += f"（最後採樣距重置 {gap_hours(r)} 小時）"
        print(f"{(r.email or r.uuid):<30}{_local(r.window_end).strftime('%Y-%m-%d %H:%M'):<18}"
              f"{r.peak_pct:>6.0f}%  {r.samples:>7}  {note}")

    csv_path = os.path.join(out_dir, f"weekly_{scope}.csv")
    write_csv(rows, csv_path)
    print(f"\n[OK] {csv_path}")
    if not no_chart:
        png_path = os.path.join(out_dir, f"weekly_{scope}.png")
        write_chart(rows, png_path, f"Claude weekly usage ({scope})")
        print(f"[OK] {png_path}")
