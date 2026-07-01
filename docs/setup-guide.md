# claumon 用量追蹤部署指南（Windows）

> 在個人電腦上架設 [claumon](https://github.com/fabioconcina/claumon)，用於追蹤 Claude（Pro/Max 訂閱）的 rate-limit 用量、session token / 成本與歷史趨勢。
> 本文件以本機（Windows 10/11 x64）實際部署流程為基礎撰寫，供團隊複製相同環境使用。

---

## 1. 目的與範圍

claumon 是一個本機常駐的儀表板服務，提供 Anthropic 官方對個人 Pro/Max 方案未開放的用量分析：

- **即時 rate-limit 額度**：session（5 小時窗口）、weekly（7 天窗口）、各模型（Opus/Sonnet）配額，資料來自 Claude OAuth usage API（伺服器端、帳號層級的真實數字，非由 log 推估）。
- **用量預測**：以每日重新擬合的 empirical-Bayes 模型，預估窗口重置時的使用率與到達門檻的 ETA。
- **Token 與成本**：解析 `~/.claude` 下的 session 檔，逐 session 統計 token / 成本，並產生每日彙總與 14 天趨勢，持久化於 SQLite。

### 適用與限制（部署前務必確認）

| 項目 | 說明 |
| --- | --- |
| 追蹤對象 | **Claude 訂閱（Pro/Max）+ Claude Code CLI** 的用量。額度儀表為帳號層級，反映該帳號在所有機器上的消耗。 |
| 不在範圍 | 在 **Cursor**、其他 IDE 或第三方工具中使用 Claude 的用量（各自獨立計費，不寫入 `~/.claude`，claumon 抓不到）。 |
| 前置條件 | 該機器必須安裝 Claude Code CLI 並完成 `login`，否則額度儀表無資料（僅本機 session 統計可用）。 |
| 歷史完整度 | 額度趨勢僅在服務執行期間累積；電腦關機期間無紀錄。需 7×24 追蹤者，建議部署於長時開機的機器。 |

---

## 2. 環境需求

- Windows 10 / 11，x64
- PowerShell（一般使用者權限即可，**全程不需系統管理員**）
- 對外網路：可連 `downloads.claude.ai`、`github.com`
- 不需 `winget`：Claude Code CLI 改用官方 PowerShell 安裝腳本 `irm https://claude.ai/install.ps1 | iex`

---

## 3. 架構概觀

```
Claude OAuth API ─┐
~/.claude JSONL  ─┤→  claumon（Pollers·Watchers·Parsers）→ SQLite（~/.claumon/usage.db）
~/.claude Memory ─┘                                       └→ HTTP Server :3131 → 瀏覽器 Dashboard
```

- 服務以背景常駐方式執行（Windows Startup 啟動腳本，登入時隱藏啟動）。
- 預設每 2 分鐘輪詢一次 usage API；session 檔變動即時解析。
- 所有資料寫入 `~/.claumon/usage.db`（SQLite，WAL 模式）。

---

## 4. 部署步驟

### 步驟 0：檢查現有環境

```powershell
claude --version      # 是否已裝 Claude Code CLI
claumon --version     # 是否已裝 claumon（未裝會找不到指令，屬正常）
```

### 步驟 1：安裝 Claude Code CLI

```powershell
# 官方 PowerShell 安裝指令（不需 winget）
irm https://claude.ai/install.ps1 | iex
```

安裝後 PATH 會更新，**請開啟新的 PowerShell 視窗**再驗證：

```powershell
claude --version
```

### 步驟 2：登入 Claude 帳號（互動式，需瀏覽器）

此步驟為瀏覽器授權，**無法自動化，須由使用者本人完成**：

```powershell
claude
```

首次啟動會提示登入，依指示在瀏覽器完成授權（選擇 Pro/Max 帳號）。成功後憑證寫入：

```
C:\Users\<使用者>\.claude\.credentials.json
```

驗證憑證已產生：

```powershell
Test-Path "$env:USERPROFILE\.claude\.credentials.json"   # 應回 True
```

### 步驟 3：下載並安裝 claumon binary

```powershell
$dir = "$env:LOCALAPPDATA\Programs\claumon"
New-Item -ItemType Directory -Force $dir | Out-Null
$exe = "$dir\claumon.exe"

# 下載最新版 Windows binary
Invoke-WebRequest -Uri "https://github.com/fabioconcina/claumon/releases/latest/download/claumon-windows-amd64.exe" -OutFile $exe

# 解除 Windows 封鎖（Mark of the Web）
Unblock-File $exe

# 加入使用者 PATH（持久化）
$p = [Environment]::GetEnvironmentVariable('Path','User')
if ($p -notlike "*claumon*") {
    [Environment]::SetEnvironmentVariable('Path', "$p;$dir", 'User')
}
```

> 完成後請**開啟新的 PowerShell 視窗**，`claumon` 指令才會生效。

### 步驟 4：設為背景常駐（開機啟動 + 自動重啟）

claumon 內建的 `claumon service install` 只是把 `claumon.vbs` 放進 Startup 資料夾：**僅登入時啟動、被關掉不會自己回來**，而且 `claumon service status` 會把「已安裝」誤報成 `running`（即使程序早已結束）。因此改用**排程任務 watchdog**：登入即啟動、背景隱藏、每 3 分鐘檢查，被誤關最多 3 分鐘內自動拉回。**全程免系統管理員。**

```powershell
$dir = "$env:LOCALAPPDATA\Programs\claumon"
$exe = "$dir\claumon.exe"

# 若先前用過官方 service，先移除 Startup(vbs)，避免雙重啟動搶 3131 埠
try { & $exe service uninstall | Out-Null } catch {}

# 1) 寫入 watchdog 腳本：claumon 不在跑就隱藏重啟
$watchdog = "$dir\claumon-watchdog.ps1"
@'
$exe = Join-Path $PSScriptRoot 'claumon.exe'
if (-not (Get-Process -Name claumon -ErrorAction SilentlyContinue)) {
    if (Test-Path $exe) { Start-Process -FilePath $exe -WindowStyle Hidden }
}
'@ | Set-Content -Path $watchdog -Encoding UTF8

# 2) 註冊排程任務（登入啟動 + 每 3 分鐘自動重啟；免系統管理員）
# 用目前登入身分的完整名稱（Entra/AzureAD 加入的機器是 AzureAD\user，拼 USERDOMAIN\USERNAME 會解析失敗）
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`""
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $me
$trigTick  = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ClaumonWatchdog" -Action $action -Trigger $trigLogon,$trigTick `
    -Settings $settings -Principal $principal -Force | Out-Null

# 3) 立即啟動一次
Start-ScheduledTask -TaskName "ClaumonWatchdog"
```

> 一鍵腳本 `scripts\install.ps1` 已內建以上流程，手動部署才需要這段。

### 步驟 5：驗證

```powershell
# 1) watchdog 排程存在，且 claumon 程序在跑
Get-ScheduledTask -TaskName ClaumonWatchdog | Select-Object TaskName, State
Get-Process claumon

# 2) 服務正在監聽 3131
netstat -ano | Select-String ":3131"          # 應看到 LISTENING

# 3) Dashboard 回應 200
(Invoke-WebRequest -Uri "http://127.0.0.1:3131" -UseBasicParsing).StatusCode

# 4) 額度資料已抓到（登入後最多約 2 分鐘）
(Invoke-WebRequest -Uri "http://127.0.0.1:3131/api/usage" -UseBasicParsing).Content
```

`/api/usage` 正常時範例輸出：

```json
{"last_poll_at":1782725522,"session_pct":71,"session_reset":"57m",
 "weekly_pct":68,"weekly_reset":"47h 27m","weekly_sonnet_pct":5}
```

最後於瀏覽器開啟 **http://localhost:3131** 確認儀表板。

---

## 5. 日常維運

| 指令 | 用途 |
| --- | --- |
| `Get-Process claumon` | 查看 claumon 是否運行（或開 http://localhost:3131） |
| `Get-ScheduledTask ClaumonWatchdog \| Select TaskName,State` | 查看 watchdog 排程狀態 |
| `Stop-Process -Name claumon -Force -ErrorAction SilentlyContinue; Start-Process claumon -WindowStyle Hidden` | 重啟（改設定或剛登入後立即重抓額度；不依賴排程） |
| `Unregister-ScheduledTask -TaskName ClaumonWatchdog -Confirm:$false` | 停用背景常駐（移除 watchdog 排程） |
| `Disable-ScheduledTask ClaumonWatchdog; Stop-Process -Name claumon -Force; claumon update` | 更新 claumon（先停排程與程序，否則 watchdog 會鎖住執行檔；更新後重跑一鍵腳本或 `Enable-ScheduledTask ClaumonWatchdog`） |

### 設定檔（選用）

於 `C:\Users\<使用者>\.claumon\config.json` 自訂，所有欄位皆可省略：

```json
{
  "port": 3131,
  "poll_interval_seconds": 120,
  "credentials_path": "~/.claude/.credentials.json",
  "claude_dir": "~/.claude",
  "db_path": "~/.claumon/usage.db"
}
```

### 資料位置

```
~/.claumon/usage.db        # SQLite 主資料庫（含每日彙總，首次啟動會回溯既有 session）
~/.claumon/usage.db-wal    # WAL 紀錄
~/.claumon/pricing.json    # 計價快取
```

---

## 6. 資料匯出與視覺化

claumon 本身無匯出指令；資料全部存於 SQLite。本專案 `claude-usage-tracker` 提供 `export`（每月彙整）與 `chart`（時間序列與曲線圖）兩個子命令。完整參數請見專案 [README](../README.md)。

### 安裝（於專案根目錄）

```powershell
pip install -e .
# 之後即可使用 claude-usage 指令；或免安裝改用 python -m claude_usage_tracker ...
```

### 資料表

| 表 | 內容 | 用途 |
| --- | --- | --- |
| `daily_aggregates` | 每日 token（input/output/cache）、`cost_usd`、session/message 數 | 成本/用量彙總（`export`） |
| `usage_snapshots` | 每 2 分鐘的額度快照（`session_pct`、`weekly_pct`、`raw_json`） | 額度趨勢/峰值（`chart`、`export`） |

### export — 每月彙整 CSV

```powershell
claude-usage export --month 2026-06     # 指定月份
claude-usage export                     # 全部
```

產出 `monthly_summary.csv`（各月 token/成本）、`daily_<scope>.csv`（每日明細）、`utilization_<scope>.csv`（各月額度峰值/平均），皆為 UTF-8-BOM CSV。

### chart — 時間序列與曲線圖

```powershell
claude-usage chart --days 7 --resample 30                 # 一週，每 30 分鐘
claude-usage chart --month 2026-07 --series weekly --resample 1440 --agg max
```

可調整區間（`--days`/`--month`/`--all`）、序列（`--series session,weekly,sonnet`）、降採樣（`--resample` + `--agg`）。

> 注意：`daily_aggregates` / 成本資料僅來自**本機 Claude Code session**；若該機器主要用 Cursor 而少用 Claude Code CLI，此表可能為空（額度快照 `utilization` 仍正常累積）。

> 採樣頻率：claumon 收集端固定每 2 分鐘；`--resample` 只在輸出時聚合，不更動原始資料，因此同一份資料可隨時用不同頻率重出。

---

## 7. 疑難排解

### 7.1 claumon 沒有自動啟動／被關掉後沒回來

**檢查**：watchdog 排程是否存在且啟用，claumon 程序是否在跑。

```powershell
Get-ScheduledTask -TaskName ClaumonWatchdog | Select-Object TaskName, State   # State 應為 Ready
Get-Process claumon                                                            # 應看到程序
```

**處理**：手動觸發一次立即拉起 claumon；若排程不存在，重跑步驟 4（或一鍵腳本）重新註冊。

```powershell
Start-ScheduledTask -TaskName ClaumonWatchdog
```

> 註：正常情況下 watchdog 每 3 分鐘會自動檢查並拉回，不需手動介入。

### 7.2 啟動 claumon 時出現「存取被拒（Access is denied）」

**原因**：多為前一個動作仍短暫持有檔案鎖，或防毒即時掃描介入；通常為暫時性。
**處理**：稍候數秒重試。若要快速確認 binary 本身正常，可複製到暫存路徑測試：

```powershell
Copy-Item "$env:LOCALAPPDATA\Programs\claumon\claumon.exe" "$env:TEMP\claumon-test.exe" -Force
& "$env:TEMP\claumon-test.exe" -port 3199    # 能正常 startup 即代表 binary 無誤；測完關閉並刪除
```

### 7.3 額度儀表沒有數字

**檢查順序**：

1. 憑證是否存在：`Test-Path "$env:USERPROFILE\.claude\.credentials.json"`
2. 若剛登入，重啟 claumon 以立即重抓：`Stop-Process -Name claumon -Force -ErrorAction SilentlyContinue; Start-Process claumon -WindowStyle Hidden`
3. 等待一個輪詢週期（預設 2 分鐘）後再看 `/api/usage`。

> 註：若該機器只用 Cursor 而未使用 Claude Code CLI，額度仍可顯示（usage API 為帳號層級），但本機 session/token 明細會是空的。

### 7.4 找不到 `claude` 或 `claumon` 指令

PATH 更新後需重開終端機。確認安裝路徑：

```powershell
Get-Command claude  -ErrorAction SilentlyContinue
Get-Command claumon -ErrorAction SilentlyContinue
```

---

## 8. 移除

```powershell
# 停用背景常駐（移除 watchdog 排程與腳本）
Unregister-ScheduledTask -TaskName ClaumonWatchdog -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item "$env:LOCALAPPDATA\Programs\claumon\claumon-watchdog.ps1" -Force -ErrorAction SilentlyContinue
try { & "$env:LOCALAPPDATA\Programs\claumon\claumon.exe" service uninstall | Out-Null } catch {}  # 清掉舊版官方 Startup(vbs)（若有）
Stop-Process -Name claumon -Force -ErrorAction SilentlyContinue

Remove-Item (Get-Command claumon).Source -ErrorAction SilentlyContinue
Remove-Item -Recurse "$env:USERPROFILE\.claumon"   # 連同歷史資料一併刪除（選用）
```

---

## 附錄：一鍵安裝腳本

本專案已將上述步驟封裝成 [`scripts/install.ps1`](../scripts/install.ps1)，會自動完成
「安裝 Claude Code CLI → 下載 claumon → 設定背景常駐（watchdog：開機啟動＋自動重啟）→ 安裝本工具」：

```powershell
# 於專案根目錄
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

> 仍須**手動完成 `claude` 登入**（步驟 2，瀏覽器授權），額度儀表才有資料；腳本結束時會提示。
> 可用 `-SkipClaudeCode` / `-SkipClaumon` / `-SkipTool` 略過個別步驟。
