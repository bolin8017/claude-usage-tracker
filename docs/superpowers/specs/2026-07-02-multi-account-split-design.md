# 多帳號分流設計（auto-detect account switch → per-account usage）

- 日期：2026-07-02
- 分支：`feat/multi-account-split`
- 狀態：已核可，待實作

## 問題

claumon 讀 `~/.claude/.credentials.json` 打訂閱用量 API，並把額度快照寫進單一
`~/.claumon/usage.db`（每 2 分鐘一筆）。當使用者 `claude` 登入不同帳號時：

- claumon 的**即時**額度儀表會自動切到新帳號（它每次都即時讀憑證檔）。
- 但 `usage.db` **沒有帳號欄位**，新舊帳號的歷史會混進同一條時間序列，本工具畫出的
  圖／匯出的 CSV 無法區分是哪個帳號，切換點會無聲地「跳一下」，造成誤導。

目標：自動偵測換帳號，讓本工具能**依帳號**分別呈現用量，且**完全不干擾** claumon。

## 關鍵前提（已驗證）

- `.credentials.json` 內只有 `accessToken / refreshToken / expiresAt / scopes /
  subscriptionType / rateLimitTier`，**沒有帳號識別碼**；`accessToken` 非 JWT（不可本機解碼）。
- 因此「這是哪個帳號」「換帳號 vs 一般 token 續期」**無法只靠本機檔案判斷**。
- 唯一穩定來源：以當前 token 打
  `GET https://api.anthropic.com/api/oauth/profile`
  （headers：`Authorization: Bearer <token>`、`anthropic-beta: oauth-2025-04-20`），
  回傳含 `account.uuid`（36 碼、穩定）、`account.email`、`account.display_name`。
- **canonical key = `account.uuid`**（不隨改名變動）；`email` 僅供人類辨識與選擇。

## 決策

- 分流方式採「**帳號時間軸邏輯分流**」：不物理搬移 `usage.db`，改記錄換帳號邊界，讀取端
  依時間區間切分。理由：claumon 是第三方工具、常駐時很可能持有 `usage.db` 檔案鎖，
  物理換檔需停/起 claumon 且有競態與資料遺失風險；邏輯分流給使用者相同結果（各帳號獨立
  圖表／CSV）卻零干擾、零遺失風險。
- 偵測掛在**現有 watchdog 每 3 分鐘心跳**（與 token 續期腳本同一機制），不新增排程。

## 架構

```
watchdog 心跳(每3分)
  ├─ 重啟 claumon（既有）
  ├─ claumon-token-refresh.ps1（既有）
  └─ claumon-account-track.ps1（新增）
        └─ 打 profile API → 取 account.uuid/email
             └─ 與 timeline 最後一筆比對，變了才 append
                  → ~/.claumon/account-timeline.jsonl
                                     │（讀取，唯讀）
claude-usage chart/export/accounts ──┴─ account.py（區間計算/選擇解析）
                                        + db.py（依 UTC 區間過濾）
```

## 元件

### A. `scripts/claumon-account-track.ps1`（新增）

比照 `claumon-token-refresh.ps1` 的風格與健壯性約定：

- 即時讀 `.credentials.json`（永不快取）；取 `claudeAiOauth.accessToken`。
- 打 profile API（短逾時，例如 20 秒）；取 `account.uuid / email / display_name`。
- 讀 `~/.claumon/account-timeline.jsonl` 最後一筆：**timeline 空、或 uuid 與上一筆不同**
  時才 append 一行 JSON：
  `{"ts":"<UTC ISO8601>","uuid":"...","email":"...","display_name":"..."}`。
- **不存 token、不碰 `usage.db`**。
- Best-effort：無憑證檔／API 模式無 oauth／離線／API 失敗／解析失敗一律記 log 後 `return`，
  **永不丟例外**（避免影響 watchdog 其餘工作）。
- 獨立 log `claumon-account-track.log`，超過門檻只留最近 N 行（比照續期腳本）。
- 參數：`-CredentialsPath`（預設 `~/.claude/.credentials.json`）、
  `-TimelinePath`（預設 `~/.claumon/account-timeline.jsonl`）。
- Append 採安全寫入：若 timeline 檔不存在則建立其目錄後寫入；append 單行、失敗只記 log。

邊界時間精度 ≈ 心跳間隔（3 分鐘）；claumon 取樣 2 分鐘一筆，換帳號當下最多 1 筆
snapshot 歸屬模糊，可接受，文件註明。

### B. `scripts/install.ps1`（修改）

- 於安裝 claumon 段落，將 `claumon-account-track.ps1` 複製到
  `$env:LOCALAPPDATA\Programs\claumon\`（比照續期腳本的 Copy-Item）。
- 產生的 `claumon-watchdog.ps1` 於呼叫續期腳本「之後」新增一段 best-effort 呼叫：
  ```powershell
  $acct = Join-Path $PSScriptRoot 'claumon-account-track.ps1'
  if (Test-Path $acct) { try { & $acct } catch {} }
  ```
- 找不到來源腳本時只 `Write-Warning`、不中止安裝（比照續期腳本）。

### C. `src/claude_usage_tracker/account.py`（新增）

純讀取、無副作用：

- `default_timeline_path()` → `~/.claumon/account-timeline.jsonl`。
- `load_timeline(path)` → 依 `ts` 排序的邊界清單（`ts` 解析為 aware UTC datetime；
  容忍壞行：跳過無法解析的行）。
- `account_intervals(timeline, now_utc)` → `dict[uuid] -> list[(start_utc, end_utc)]`：
  邊界 i 擁有 `[ts_i, ts_{i+1})`，最後一筆到 `now_utc`。同一 uuid 若出現多次（登出又登入）
  就有多個區段；各區段依構造本就互不相交，直接全部收集即可，無需合併重疊。
- `list_accounts(timeline)` → 每個 uuid 的 `email / display_name / first_seen / last_seen`。
- `resolve_account(timeline, query)` → 回傳唯一 uuid：
  依序嘗試 (1) query == uuid，(2) query == email（不分大小寫），
  (3) query 為 email 或 display_name 的不分大小寫子字串；命中 0 筆或 >1 筆 → 丟
  `AccountSelectionError`（訊息列出候選 email）。

`now_utc` 由呼叫端傳入（便於測試；避免模組內直接取現在時間）。

### D. `src/claude_usage_tracker/db.py`（修改）

- `load_snapshots(...)` 新增可選參數 `utc_intervals: Optional[list[tuple[datetime, datetime]]]`。
- 有給時，在把 UTC 時間字串轉本地時間**之前**，以解析後的 aware UTC datetime 判斷該筆是否
  落在任一區間 `[start, end)` 內，不在就丟棄。時區全程以 UTC 比較，最後才轉本地（維持既有
  `_utc_to_local` 行為）。

### E. `src/claude_usage_tracker/cli.py`（修改）

- `chart` 與 `export` 皆新增 `--account <email 或 uuid>`：
  - 解析流程：`load_timeline` → `resolve_account` → `account_intervals`[uuid] → 傳給
    `load_snapshots(utc_intervals=...)`。
  - timeline 不存在或空、或解析不到帳號 → 友善錯誤並結束（提示先跑過偵測或用 `accounts` 查）。
  - 未給 `--account` → 行為完全不變（全部資料）。
- 輸出檔名在有 `--account` 時附帳號短碼（uuid 前 6 碼），例如
  `usage_chart_last7d_session_ab12cd.png`，避免不同帳號互相覆蓋。
- 新增子指令 `accounts`：讀 timeline，表列已知帳號（email、uuid 短碼、first/last seen、
  該帳號區間內的 snapshot 筆數）。timeline 空時印說明（可能尚未偵測到，或未安裝偵測腳本）。

### F. 圖表換帳號標記（`chart.py` 修改，預設開）

- 未給 `--account` 且繪圖區間內出現 ≥2 個帳號時，於每個換帳號邊界畫一條淡色虛線垂直標記，
  並標上新帳號 email（短）。
- 提供 `--no-account-markers` 關閉。給了 `--account`（單帳號）時自然不畫。

### G. 文件（修改）

- `README.md`：新增「多帳號分流」小節，說明自動偵測、`--account`、`accounts`、換帳號標記，
  以及「偵測前的舊資料視為未歸屬」與「邊界精度 ≈3 分鐘」的註記。
- `docs/setup-guide.md`：補一句偵測腳本隨 watchdog 一併部署。

### H. 測試（新增 `tests/`，pytest）

- `account.py`：
  - `account_intervals` 基本切分、最後一段到 now、同帳號多次登入取多段。
  - `resolve_account` 的 uuid／email／子字串命中，及 0 筆／多筆錯誤。
  - `load_timeline` 排序與壞行容忍。
- `db.load_snapshots(utc_intervals=...)`：以 in-memory sqlite 建 `usage_snapshots`，驗證
  只回落在區間內的列、且時間已轉本地。
- PS 腳本：邏輯簡單、以 best-effort 為主，不寫自動化測試，靠手動驗證（見下）。
- `pyproject.toml` 加入 `pytest` 為選用 dev 相依。

## 資料格式

`~/.claumon/account-timeline.jsonl`（每行一個 JSON 物件，append-only）：

```json
{"ts":"2026-07-02T01:23:45Z","uuid":"<account uuid>","email":"a@example.com","display_name":"A"}
{"ts":"2026-07-02T09:10:00Z","uuid":"<other uuid>","email":"b@example.com","display_name":"B"}
```

- 只在帳號變更時 append；一般心跳（帳號不變）不寫。
- 純本機、僅含使用者自己的帳號 email/uuid（低敏感）；不含任何 token。

## 邊界情況

- timeline 缺／空：`--account` 報友善錯誤；`accounts` 印說明；無 `--account` 路徑不受影響。
- API 模式（無 `claudeAiOauth`）或離線：偵測腳本記 log 後跳過，不寫 timeline。
- 同帳號登出又登入：多筆同 uuid 邊界，區間取各段聯集。
- 舊資料（偵測開始前）：視為未歸屬，`--account` 排除。
- 時鐘／時區：timeline 與 DB 皆存 UTC，區間運算全程 UTC，最後才轉本地。

## 手動驗收

1. 安裝後確認 `claumon-account-track.ps1` 已部署、watchdog 會呼叫它。
2. 心跳跑過一次後，`~/.claumon/account-timeline.jsonl` 出現當前帳號一筆。
3. `claude` 登入另一帳號 → 下次心跳後 timeline 追加第二筆（uuid 不同）。
4. `claude-usage accounts` 列出兩個帳號。
5. `claude-usage chart --all --account <emailA>` 只含 A 的區間；不加 `--account` 的圖在切換
   點出現換帳號標記。

## 非目標（YAGNI）

- 不物理搬移／分割 `usage.db`（需要時再加「依帳號匯出獨立 db」指令）。
- 不回溯歸屬偵測開始前的歷史資料。
- 不為 PS 腳本建自動化測試框架。
