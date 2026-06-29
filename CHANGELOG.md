# Changelog

本專案遵循 [Keep a Changelog](https://keepachangelog.com/) 與
[語意化版本](https://semver.org/lang/zh-TW/)。

## [0.1.0] - 2026-06-29

### Added
- 初版發布。
- `claude-usage export`：匯出每月 token / 成本彙整與額度峰值 CSV。
- `claude-usage chart`：輸出額度（session / weekly / sonnet）時間序列 CSV 與曲線圖。
  - 區間選擇：`--days` / `--month` / `--all`
  - 序列選擇：`--series`
  - 輸出時降採樣：`--resample` 搭配 `--agg max|mean|last`
  - 資料缺口斷線、峰值標註、峰值/平均統計、`--no-chart`
- 安裝指南文件 `docs/setup-guide.md`（Windows 部署 claumon + Claude Code CLI）。
