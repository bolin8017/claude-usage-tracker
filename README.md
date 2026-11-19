# claude-usage-tracker

每小時記錄一次 Claude（Pro/Max 訂閱）帳號的 weekly 額度使用率（本週用量占每週額度的百分比），並整理成每週報表（CSV 與長條圖），方便附在月報裡。

```
排程 ClaudeUsageLogger（登入時與每小時各一次）
  └─ usage-logger.ps1 ── 額度 API ──► ~/.claude-usage/usage-log.jsonl ──► claude-usage weekly ──► CSV + PNG
```

---

## 特色

- **輕量**：沒有常駐程式，也不掃描 `~/.claude` 資料夾。排程每小時啟動一次，讀一個憑證檔、打兩支 API（用量與帳號資訊），寫一行紀錄就結束。
- **涵蓋全部用量**：額度 API 回傳的是帳號層級的數字，不只本機的 Claude Code，在其他電腦或在 Claude 對話（Chats）的用量也都算在內。報表會用 API 提供的分類（Claude Code、Chats、Cowork、Other）列出各自占多少。
- **依帳號分開**：每筆紀錄都帶帳號 uuid 與 email，報表依帳號分列。
- **支援 claude-swap**：指定 `-SwapProfile` 後，不管當下切到哪個帳號，都只記錄指定的那個帳號。
- **保留原始回應**：每筆紀錄都保存 API 的完整回應，日後 API 改了欄位名稱，舊資料仍能重新解析。

---

## 安裝（Windows）

需求：Python 3.9+，以及已經登入過的 Claude Code CLI（記錄程式會讀它的憑證檔）。全程不需系統管理員權限。

```powershell
# 用 claude-swap 切換多個帳號時，指定要記錄的 profile
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SwapProfile louis

# 只有一個帳號就不用給參數
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

安裝腳本會依序：

1. 沒裝 Claude Code CLI 的話，用官方指令安裝（加 `-SkipClaudeCode` 可略過）。
2. 如果裝過舊版的 claumon，就移除它的程式與排程；`~/.claumon` 的歷史資料會保留。
3. 把記錄腳本複製到 `%LOCALAPPDATA%\Programs\claude-usage-logger\`，並註冊排程 `ClaudeUsageLogger`（加 `-SkipLogger` 可略過）。
4. `pip install -e .` 安裝本工具，之後就能用 `claude-usage` 指令（加 `-SkipTool` 可略過）。

確認排程有在跑：

```powershell
Get-ScheduledTaskInfo ClaudeUsageLogger | Select LastRunTime, LastTaskResult, NextRunTime
Get-Content "$env:USERPROFILE\.claude-usage\usage-log.jsonl" -Tail 1
```

### 移除

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
#   -StopOnly   只移除排程，不刪檔案
#   -PurgeData  連同 ~/.claude-usage 用量記錄與 ~/.claumon 舊資料一起刪除
```

---

## 每月報表：weekly

```bash
claude-usage weekly --month 2026-11          # 結束日落在 11 月的每個週期
claude-usage weekly --all                    # 全部週期
claude-usage weekly --month 2026-11 --account louis   # 只看某個帳號
```

| 參數 | 說明 |
| --- | --- |
| `--month YYYY-MM` / `--all` | 範圍（預設本月）。週期依「結束日（本地時間）」歸入月份 |
| `--account` | email、uuid，或兩者的子字串（需唯一） |
| `--out <資料夾>` | 輸出位置（預設 `./exports`） |
| `--no-chart` | 只輸出 CSV |
| `--log <路徑>` | 記錄檔（全域參數，預設 `~/.claude-usage/usage-log.jsonl`） |

輸出 `weekly_<範圍>.csv` 與 `weekly_<範圍>.png`。CSV 一列代表一個帳號的一個週期：

| 欄位 | 說明 |
| --- | --- |
| `week_start` / `week_end` | 週期起訖（本地時間），以 API 的 `resets_at` 為準 |
| `weekly_pct` | 該週用掉多少 weekly 額度（%）；週期中途歸零時為各段相加，可能超過 100% |
| `claude_code_pct` 等 | 各使用管道占 weekly 額度的百分比 |
| `segment_peaks` | 各段的最大值，例如 `98+52`；沒有中途歸零時只有一段 |
| `samples` | 該週期的取樣數，滿一週約 168 筆 |
| `hours_unsampled_before_reset` | 最後一次取樣距週期結束還有幾小時 |
| `status` | 完整／進行中／可能偏低 |

### 怎麼算出一週的用量、什麼時候會「可能偏低」

同一個帳號的 weekly 使用率在一個週期內只會增加，到重置時才歸零。所以週期內的最大值就是那一週的用量，每小時取樣一次就夠了。

例外是週期還沒結束、額度就被歸零。claumon 的歷史資料裡出現過三次（9/01、10/14、11/17），都是 2 分鐘內從 61～98% 掉到 0%，`resets_at` 沒有變。遇到這種情況，報表會從下降的地方切成兩段，把各段的最大值相加，並在 `segment_peaks` 列出各段。例如 11/22 結束的那一週（目前還在進行中）是 `98+52`，合計 150%。

如果最後一次取樣距離週期結束超過 2 小時（例如週末關機），後面那段時間的用量就記不到，該週會標成「可能偏低」，並寫出差了幾小時。

---

## 運作細節

### 記錄哪個帳號

- 沒給 `-SwapProfile`：讀 `~/.claude/.credentials.json`，也就是當下登入的帳號。
- 有給 `-SwapProfile louis`：如果 `~/.claude/.credential-owner` 是 `louis`，代表 louis 正在使用，就讀 `~/.claude`；否則讀 `~/.claude-louis/.credentials.json`。

用量和帳號資訊是用同一個 token（登入用的存取憑證）查的，所以紀錄上的帳號一定和數字對得起來。

### token 續期

access token 大約 8 小時就會過期。每次記錄前，`token-refresh.ps1` 會檢查 token：只有在快過期（剩不到 120 秒）或已經過期時，才用 OAuth refresh grant（以 refresh token 換新 access token 的流程）續期並寫回原本的檔案。Claude Code 正在執行時，會在過期前約 4 分鐘自己續期，一定比這支腳本先動手，所以兩邊不會搶著輪替 refresh token。如果電腦關機太久，連 refresh token 都過期了，就需要重新執行一次 `claude` 登入。

### 失敗時

API 回錯誤（包括 429 速率限制）、找不到憑證、或回應格式不對，都只會寫進 `%LOCALAPPDATA%\Programs\claude-usage-logger\usage-logger.log`，然後結束；下一個小時會再試一次。

### 注意事項

- 額度 API（`/api/oauth/usage`）是 Claude Code 自己在用的非公開端點，Anthropic 可能會改。紀錄保存了原始回應，欄位解析集中在 `usagelog.py`。
- 電腦要開著，排程才會執行；關機期間不會有紀錄。

---

## 匯入舊的 claumon 資料

之前用 claumon 累積的 `~/.claumon/usage.db` 可以匯入記錄檔，只需要執行一次（重跑會自動略過）：

```bash
claude-usage import-claumon --account louis   # 只匯入指定帳號；不給就匯入全部帳號
```

claumon 的資料庫沒有帳號欄位，帳號要靠 `~/.claumon/account-timeline.jsonl` 判斷，但不能逐筆照時間軸歸屬。claumon 會把 token 快取在記憶體，只有在 token 快到期或被拒絕時才重新讀取憑證檔（見 claumon 原始碼 `internal/auth/provider.go`）。所以切換帳號後，它會繼續查詢舊帳號好幾個小時。例如 11/12 到 11/13 有一條從 0% 連續爬到 70% 的曲線，期間時間軸換了三次帳號，曲線卻沒有斷開。

因此匯入是以「週期」為單位：一個週期（`resets_at`）只屬於一個帳號，整個週期歸給時間軸上占多數的帳號；沒有過半數的週期不匯入。匯入時會列出每個週期的歸屬與佔比。資料密度降到每小時一筆，並保留每次中途歸零前的最大值。

我們用另一個獨立的方法驗證過這套歸屬方式。API 回應中 `extra_usage` 的帳號設定，在 louis 與其他帳號之間不同，用這個特徵分出 louis 的全部 2 分鐘資料後，14 個週期的各段最大值和匯入結果完全一致。

舊的 `chart`、`export`、`accounts` 指令仍可讀 `usage.db`（`--db` 指定位置），但它們是逐筆依時間軸切分帳號，帳號切換後的幾個小時會算錯，只適合看整體趨勢。

---

## 專案結構

```
claude-usage-tracker/
├── scripts/
│   ├── install.ps1          # 安裝：Claude Code、移除舊 claumon、記錄排程、本工具
│   ├── uninstall.ps1        # 移除排程與本工具；-StopOnly 只移除排程
│   ├── usage-logger.ps1     # 每小時記錄一次（排程呼叫）
│   └── token-refresh.ps1    # token 快過期時續期（記錄前呼叫）
└── src/claude_usage_tracker/
    ├── cli.py               # 命令列入口
    ├── usagelog.py          # 讀記錄檔、解析 API 回應、依帳號與週期彙整、claumon 匯入
    ├── weekly.py            # 每週報表 CSV 與長條圖
    ├── db.py / export.py / chart.py / account.py   # 舊 claumon 資料庫的匯出與圖表
```

---

## 授權

[MIT](LICENSE)
