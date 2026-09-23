# Changelog

本專案遵循 [Keep a Changelog](https://keepachangelog.com/) 與
[語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

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
