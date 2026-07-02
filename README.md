# claude-usage-tracker

從 [claumon](https://github.com/fabioconcina/claumon) 的本機 SQLite 資料庫，匯出 Claude（Pro/Max 訂閱）用量資料並繪製趨勢曲線。

claumon 會在背景持續記錄你的額度使用率（session / weekly）與 Claude Code 的 token / 成本；本工具負責把這些資料**匯出成 CSV** 並**畫成曲線圖**，方便每週 / 每月彙整與長期追蹤。

```
claumon (背景常駐, 每 2 分鐘採樣)  ──►  ~/.claumon/usage.db  ──►  claude-usage-tracker  ──►  CSV + PNG
```

---

## 特色

- **零干擾**：以唯讀模式讀取資料庫，不影響正在運行的 claumon。
- **彈性區間**：最近 N 天 / 指定月份 / 全部。
- **序列自選**：`session`、`weekly`、`sonnet` 任意組合（例如月視圖只看 weekly）。
- **輸出時降採樣**：原始 2 分鐘資料可聚合成每小時 / 每天一點，長區間圖也清晰；不更動原始資料。
- **正確性細節**：偵測資料缺口（關機）自動斷線、峰值標註、峰值/平均統計。
- **純標準函式庫 + matplotlib**，跨平台。

---

## 安裝

需求：Python 3.9+。

### Windows 一鍵安裝（推薦）

連同 Claude Code CLI、claumon 背景常駐（開機啟動＋自動重啟）、本工具一次裝好：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

腳本會自動完成安裝；唯一需手動的是 `claude` 登入（瀏覽器授權），腳本結束時會提示。詳見 [`docs/setup-guide.md`](docs/setup-guide.md)。

安裝時還會一併部署 **Claude OAuth token 自動續期**，讓額度儀表不會因 Claude Code 閒置而斷線 —— 詳見下方[額度儀表為什麼會斷線](#額度儀表為什麼會斷線自動續期)。

### 只安裝本工具

```bash
# 於專案根目錄
pip install -e .
```

安裝後可使用 `claude-usage` 指令。若不想安裝，也可直接用模組方式執行：

```bash
python -m claude_usage_tracker chart --days 7
```

> 本工具讀取 claumon 產生的資料庫。請先依 [`docs/setup-guide.md`](docs/setup-guide.md) 安裝並啟動 claumon（或使用上方一鍵腳本）。

### 移除

```powershell
# 移除 claumon 與本工具（保留 Claude Code）
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
#   -StopOnly   只停掉背景常駐與一直跳的通知，不移除檔案
#   -PurgeData  連同 ~/.claumon 歷史資料一併刪除
```

---

## 使用

### chart — 時間序列 CSV 與曲線圖

```bash
claude-usage chart --days 1                       # 當天，細看 session 鋸齒
claude-usage chart --days 7 --resample 30         # 一週，每 30 分鐘一點
claude-usage chart --month 2026-07 --series weekly --resample 1440 --agg max
                                                  # 整月只看 weekly，每天峰值
claude-usage chart --all --series session,weekly,sonnet --resample 60
```

| 參數 | 說明 |
| --- | --- |
| `--days N` / `--month YYYY-MM` / `--all` | 時間區間（三選一，預設最近 14 天） |
| `--series` | `session` / `weekly` / `sonnet`，逗號組合（預設 `session,weekly`） |
| `--resample <分鐘>` | 輸出時降採樣間隔；`0`（預設）= 原始 2 分鐘密度 |
| `--agg` | 桶內聚合：`max`（峰值，預設）/ `mean` / `last` |
| `--no-chart` | 只輸出 CSV，不畫圖 |
| `--out <資料夾>` | 輸出位置（預設 `./exports`） |

輸出檔名含區間與序列，例如 `usage_chart_2026-07_1440m_weekly.png`，不同設定不互相覆蓋。

### export — 每月彙整 CSV

```bash
claude-usage export --month 2026-06
claude-usage export                  # 全部
```

產出：

| 檔案 | 內容 |
| --- | --- |
| `monthly_summary.csv` | 各月 token / 成本 / session 數彙總 |
| `daily_<scope>.csv` | 每日 token / 成本明細 |
| `utilization_<scope>.csv` | 各月額度峰值與平均 |

### 全域參數

`--db <路徑>`：指定 `usage.db`（預設 `~/.claumon/usage.db`）。

---

## 各區間建議設定

| 看的範圍 | 建議 |
| --- | --- |
| 一天 | `chart --days 1`（不降採樣） |
| 一週 | `chart --days 7 --resample 30` |
| 一個月 | `chart --month YYYY-MM --series weekly --resample 1440 --agg max` |

---

## 重要前提

- 這台電腦要**開著且 claumon 服務在運行**才會持續記錄；關機期間無資料（圖上會以斷線呈現）。
- 額度（utilization）為**帳號層級**，反映整個 Claude 訂閱的消耗；token/成本則僅來自**本機 Claude Code session**。
- 在 Cursor 等其他工具使用 Claude 的量不計入此資料。

詳見 [`docs/setup-guide.md`](docs/setup-guide.md)。

---

## 額度儀表為什麼會斷線（自動續期）

claumon 讀取 `~/.claude/.credentials.json` 去打訂閱用量 API，但它**自己不會續期** token，完全依賴 Claude Code 幫忙刷新。Claude Code 的 access token 約 **8 小時**過期，且其背景 daemon **一閒置就會結束**；因此當你一段時間沒用 Claude Code（例如整晚），token 過期後沒人續期，claumon 的額度儀表就會變空，直到你下次手動 `claude` 登入。

一鍵安裝會部署續期腳本 `claumon-token-refresh.ps1`，由 watchdog **每 3 分鐘心跳時**檢查一次：

- 只在 token **快過期或已過期**（預設剩 ≤120 秒）時才用標準 OAuth refresh grant 刷新，並寫回 `.credentials.json`。
- 緩衝刻意小於 Claude Code daemon 的主動續期時機（過期前約 4 分鐘），因此 **daemon 活著時一定比腳本先動手**，天然避開 refresh token 一次性輪替造成的衝突（兩邊搶著換，晚換者會失效被登出）。腳本只在沒人維持 token 的空窗才補上。
- 原子寫入、寫回前再讀一次比對，中途失敗也不會弄壞憑證檔。

```powershell
# 查看續期紀錄
Get-Content "$env:LOCALAPPDATA\Programs\claumon\claumon-token-refresh.log" -Tail 20
```

> 注意：這只解決 access token 續期。若電腦長時間關機導致 **refresh token 本身**過期，仍需手動 `claude` 登入一次。

---

## 專案結構

```
claude-usage-tracker/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── pyproject.toml
├── requirements.txt
├── docs/
│   └── setup-guide.md          # claumon + Claude Code CLI 部署指南（Windows）
├── scripts/
│   ├── install.ps1             # Windows 一鍵安裝（Claude Code + claumon + 本工具）
│   ├── uninstall.ps1           # 移除 claumon 與本工具（保留 Claude Code）；-StopOnly 只停背景
│   └── claumon-token-refresh.ps1  # Claude OAuth token 自動續期（由 watchdog 每 3 分鐘呼叫）
└── src/
    └── claude_usage_tracker/
        ├── __init__.py
        ├── __main__.py         # python -m claude_usage_tracker
        ├── cli.py              # 命令列入口（export / chart 子命令）
        ├── db.py               # 唯讀資料庫存取（UTC→本地時間）
        ├── export.py           # 每月彙整 CSV
        └── chart.py            # 時間序列 CSV 與曲線圖
```

---

## 授權

[MIT](LICENSE)
