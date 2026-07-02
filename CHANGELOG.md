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

### Added
- 初版發布。
- `claude-usage export`：匯出每月 token / 成本彙整與額度峰值 CSV。
- `claude-usage chart`：輸出額度（session / weekly / sonnet）時間序列 CSV 與曲線圖。
  - 區間選擇：`--days` / `--month` / `--all`
  - 序列選擇：`--series`
  - 輸出時降採樣：`--resample` 搭配 `--agg max|mean|last`
  - 資料缺口斷線、峰值標註、峰值/平均統計、`--no-chart`
- 安裝指南文件 `docs/setup-guide.md`（Windows 部署 claumon + Claude Code CLI）。
