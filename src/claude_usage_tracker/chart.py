"""用量曲線圖與時間序列匯出。

設計重點：
  - 收集端固定 2 分鐘高密度；resample 只在輸出時聚合，不改原始資料。
  - 偵測資料缺口（例如電腦關機），折線會「斷開」而非假連線。
  - 圖上標出每條線的峰值；呼叫端可取得峰值/平均統計。
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from .db import Record

NAN = float("nan")

# 序列定義；attr 對應 Record 的欄位名
SERIES_DEF = {
    "session": {"label": "Session (5-hour)", "color": "#f59e0b", "attr": "session"},
    "weekly":  {"label": "Weekly (7-day)",   "color": "#2563eb", "attr": "weekly"},
    "sonnet":  {"label": "Weekly Sonnet",    "color": "#10b981", "attr": "sonnet"},
}


def parse_series(raw: str) -> List[str]:
    series = [s.strip().lower() for s in raw.split(",") if s.strip()]
    bad = [s for s in series if s not in SERIES_DEF]
    if bad:
        raise ValueError(
            f"不支援的 series 值：{','.join(bad)}；可選：{','.join(SERIES_DEF)}")
    seen, ordered = set(), []
    for s in series:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered or ["session", "weekly"]


def _agg(values, how: str):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if how == "mean":
        return round(sum(vals) / len(vals), 1)
    if how == "last":
        return vals[-1]
    return max(vals)


def resample(recs: Sequence[Record], minutes: int, how: str) -> List[Record]:
    """依 minutes 分鐘分桶聚合；桶時間取桶起點。"""
    if not minutes or minutes <= 0:
        return list(recs)
    bucket_sec = minutes * 60
    buckets: dict = {}
    for r in recs:
        key = int(r.time.timestamp()) // bucket_sec * bucket_sec
        buckets.setdefault(key, []).append(r)
    out = []
    for key in sorted(buckets):
        grp = buckets[key]
        out.append(Record(datetime.fromtimestamp(key),
                          _agg([g.session for g in grp], how),
                          _agg([g.weekly for g in grp], how),
                          _agg([g.sonnet for g in grp], how)))
    return out


def gap_threshold_minutes(resample_min: int) -> float:
    """超過此間隔視為資料缺口（折線斷開）。"""
    expected = resample_min if resample_min > 0 else 2
    return max(expected * 3, 10)


def series_stats(recs: Sequence[Record], attr: str):
    """回傳 (peak_value, peak_time, avg) 或 None。"""
    pts = [(r.time, getattr(r, attr)) for r in recs if getattr(r, attr) is not None]
    if not pts:
        return None
    peak_time, peak_val = max(pts, key=lambda p: p[1])
    avg = round(sum(v for _, v in pts) / len(pts), 1)
    return peak_val, peak_time, avg


def _build_xy(recs: Sequence[Record], attr: str, gap_min: float):
    xs, ys = [], []
    prev = None
    for r in recs:
        val = getattr(r, attr)
        if prev is not None and (r.time - prev).total_seconds() / 60 > gap_min:
            xs.append(prev + (r.time - prev) / 2)
            ys.append(NAN)
        xs.append(r.time)
        ys.append(NAN if val is None else val)
        prev = r.time
    return xs, ys


def write_timeseries_csv(path: str, recs: Sequence[Record], series: Sequence[str]) -> None:
    attrs = [(s, SERIES_DEF[s]["attr"]) for s in series]
    header = ["local_time"] + [f"{s}_pct" for s in series]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in recs:
            row = [r.time.strftime("%Y-%m-%d %H:%M")]
            row += ["" if getattr(r, a) is None else getattr(r, a) for _, a in attrs]
            w.writerow(row)


def draw_chart(path: str, recs: Sequence[Record], series: Sequence[str],
               title: str, gap_min: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    use_marker = len(recs) <= 80
    fig, ax = plt.subplots(figsize=(12, 6))
    for name in series:
        meta = SERIES_DEF[name]
        xs, ys = _build_xy(recs, meta["attr"], gap_min)
        ax.plot(xs, ys, label=meta["label"], color=meta["color"],
                linewidth=2 if name == "weekly" else 1.5,
                marker="." if use_marker else None, markersize=5)
        st = series_stats(recs, meta["attr"])
        if st:
            pv, pt, _ = st
            ax.scatter([pt], [pv], color=meta["color"], zorder=5, s=28,
                       edgecolor="white", linewidth=0.6)
            ax.annotate(f"{pv:g}%", (pt, pv), textcoords="offset points",
                        xytext=(0, 7), ha="center", fontsize=8, color=meta["color"])

    ax.set_ylim(0, 105)
    ax.set_ylabel("Utilization (%)")
    ax.set_xlabel("Local time")
    ax.set_title(f"Claude Usage — {title}")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left")

    times = [r.time for r in recs]
    span_days = (times[-1] - times[0]).total_seconds() / 86400 if len(times) > 1 else 0
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d" if span_days > 2 else "%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(recs: List[Record], *, out_dir: str, scope: str, series: List[str],
        resample_min: int, agg: str, no_chart: bool) -> None:
    """輸出時間序列 CSV，並（預設）畫出曲線圖。"""
    os.makedirs(out_dir, exist_ok=True)
    raw_n = len(recs)
    if resample_min > 0:
        recs = resample(recs, resample_min, agg)

    gap_min = gap_threshold_minutes(resample_min)
    tag = "-".join(series)
    suffix = scope + (f"_{resample_min}m" if resample_min > 0 else "")
    rng = f"{recs[0].time:%Y-%m-%d %H:%M} ~ {recs[-1].time:%Y-%m-%d %H:%M}"
    title = rng + (f"  ({resample_min}m {agg})" if resample_min > 0 else "")

    csv_path = os.path.join(out_dir, f"usage_timeseries_{suffix}_{tag}.csv")
    write_timeseries_csv(csv_path, recs, series)

    if resample_min > 0:
        print(f"範圍　：{scope}（原始 {raw_n} 筆 → {len(recs)} 點，"
              f"每 {resample_min} 分鐘 / {agg}）")
    else:
        print(f"範圍　：{scope}（{raw_n} 筆快照）")
    print(f"序列　：{', '.join(series)}")
    for name in series:
        st = series_stats(recs, SERIES_DEF[name]["attr"])
        if st:
            pv, pt, avg = st
            print(f"  - {name:<8} 峰值 {pv:g}% @ {pt:%Y-%m-%d %H:%M}，平均 {avg:g}%")
        else:
            print(f"  - {name:<8} 無資料")
    print(f"[OK] {csv_path}")

    if not no_chart:
        png_path = os.path.join(out_dir, f"usage_chart_{suffix}_{tag}.png")
        draw_chart(png_path, recs, series, title, gap_min)
        print(f"[OK] {png_path}")
