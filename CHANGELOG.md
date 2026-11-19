# Changelog

本專案遵循 [Keep a Changelog](https://keepachangelog.com/) 與
[語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

### Changed
- 改用每小時一次的排程 `ClaudeUsageLogger`（`scripts/usage-logger.ps1`）取代常駐的 claumon：
  不再監看 `~/.claude`，每次只讀一個憑證檔、打用量與帳號兩支 API，追加一行到
  `~/.claude-usage/usage-log.jsonl`（保存原始回應）。`install.ps1` 會移除舊的 claumon 程式與排程，
  `~/.claumon` 資料保留。
- `install.ps1` 新增 `-SwapProfile`：搭配 claude-swap 時，不管當下登入哪個帳號，都只記錄指定帳號。
- `claumon-token-refresh.ps1` 改名為 `token-refresh.ps1`，由記錄腳本在每次記錄前呼叫。

### Added
- `claude-usage weekly`：依帳號與週期彙整 weekly 使用率，輸出 CSV 與長條圖，標出取樣不足、可能偏低的週期。

- `claude-usage import-claumon`：把 claumon 歷史匯入記錄檔。以週期為單位、依帳號時間軸多數決歸屬，
  避開 claumon 快取 token 造成的帳號切換延遲。

### Fixed
- 週期中途額度被歸零時，`weekly` 原本只取最大值會少算；改成在下降處切段、各段最大值相加，
  CSV 新增 `segment_peaks` 欄位。

### Removed
- `claumon-account-track.ps1` 與 `docs/setup-guide.md`（claumon 部署指南）。

### Added
- Claude OAuth token 自動續期：新增 `scripts/claumon-token-refresh.ps1`，由 watchdog 每 3 分鐘
  心跳時在 token 快過期（預設剩 ≤120 秒）時自動刷新並寫回 `~/.claude/.credentials.json`。
  解決 Claude Code 閒置後 access token 過期、claumon 額度儀表變空、需手動重新登入的問題。
  緩衝刻意小於 Claude Code daemon 主動續期時機，避免 refresh token 輪替衝突；採原子寫入、
  寫回前再比對，失敗不損毀憑證檔。`install.ps1` 會自動部署，`uninstall.ps1` 會隨資料夾一併移除。
- 多帳號分流：自動偵測 `claude` 換帳號，`chart`/`export` 新增 `--account`、新增 `accounts`
  子指令、混帳號圖表換帳號標記。

### Fixed
- `install.ps1` 第 4 步優先用 `py -3` 找正式安裝的 Python，不再誤用 PATH 上其他工具的
  venv（例如沒有 pip 的 MCP client venv，會出現 `No module named pip`）；先確認 pip 可用，
  失敗時印出實際 Python 路徑與修正指令，pip 失敗也會明確警告。

### Added
- 初版發布。
- `claude-usage export`：匯出每月 token / 成本彙整與額度峰值 CSV。
- `claude-usage chart`：輸出額度（session / weekly / sonnet）時間序列 CSV 與曲線圖。
  - 區間選擇：`--days` / `--month` / `--all`
  - 序列選擇：`--series`
  - 輸出時降採樣：`--resample` 搭配 `--agg max|mean|last`
  - 資料缺口斷線、峰值標註、峰值/平均統計、`--no-chart`
- 安裝指南文件 `docs/setup-guide.md`（Windows 部署 claumon + Claude Code CLI）。
