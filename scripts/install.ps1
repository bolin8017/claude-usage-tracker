<#
.SYNOPSIS
    一鍵安裝 Claude 用量追蹤環境（Windows）。

.DESCRIPTION
    依序處理：
      1. 安裝 Claude Code CLI（winget）
      2. 下載 claumon binary、加入 PATH
      3. 將 claumon 設為開機背景服務（每 2 分鐘記錄用量）
      4. 安裝本工具 claude-usage-tracker（pip install -e .）

    無法自動化的步驟：`claude` 登入為瀏覽器互動授權，需由使用者本人完成；
    腳本最後會提示。全程不需系統管理員權限。

.PARAMETER SkipClaudeCode
    略過 Claude Code CLI 安裝。

.PARAMETER SkipClaumon
    略過 claumon 下載與服務安裝。

.PARAMETER SkipTool
    略過本工具（pip install -e .）安裝。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipClaudeCode,
    [switch]$SkipClaumon,
    [switch]$SkipTool
)

$ErrorActionPreference = "Stop"

function Write-Step($n, $msg) { Write-Host "`n=== [$n] $msg ===" -ForegroundColor Cyan }
function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

$repoRoot = Split-Path -Parent $PSScriptRoot

# 1. Claude Code CLI ---------------------------------------------------------
if (-not $SkipClaudeCode) {
    Write-Step 1 "安裝 Claude Code CLI"
    if (Test-Cmd "claude") {
        Write-Host "已安裝，略過。"
    } elseif (Test-Cmd "winget") {
        winget install --id Anthropic.ClaudeCode `
            --accept-source-agreements --accept-package-agreements -e
    } else {
        Write-Warning "找不到 winget；請改用 PowerShell 安裝指令：irm https://claude.ai/install.ps1 | iex"
    }
} else { Write-Step 1 "略過 Claude Code CLI" }

# 2. claumon binary ----------------------------------------------------------
if (-not $SkipClaumon) {
    Write-Step 2 "下載 claumon binary"
    $dir = "$env:LOCALAPPDATA\Programs\claumon"
    $exe = "$dir\claumon.exe"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $url = "https://github.com/fabioconcina/claumon/releases/latest/download/claumon-windows-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $exe
    Unblock-File $exe
    $p = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($p -notlike "*$dir*") {
        [Environment]::SetEnvironmentVariable('Path', "$p;$dir", 'User')
        Write-Host "已加入使用者 PATH：$dir"
    }
    Write-Host "claumon 安裝於 $exe"

    Write-Step 3 "設定 claumon 開機背景服務"
    # 重試一次，避免下載後檔案短暫鎖定導致 service install 寫入不完整
    for ($i = 1; $i -le 2; $i++) {
        Start-Sleep -Seconds 1
        & $exe service install
        if ($LASTEXITCODE -eq 0) { break }
        Write-Warning "service install 第 $i 次未成功，重試…"
    }
} else { Write-Step 2 "略過 claumon" }

# 4. 本工具 ------------------------------------------------------------------
if (-not $SkipTool) {
    Write-Step 4 "安裝 claude-usage-tracker（pip install -e .）"
    if (Test-Cmd "python") {
        python -m pip install -e $repoRoot
    } else {
        Write-Warning "找不到 python；請先安裝 Python 3.9+ 後再執行：pip install -e ."
    }
} else { Write-Step 4 "略過本工具" }

# 完成提示 -------------------------------------------------------------------
Write-Host "`n----------------------------------------------------------" -ForegroundColor Green
Write-Host "安裝流程完成。還有一個手動步驟：" -ForegroundColor Green
Write-Host @"

  >> 登入 Claude（額度儀表才有資料，需瀏覽器授權）：

     claude

     首次啟動依指示完成登入後，claumon 最多 2 分鐘內會開始抓額度。
     可立即生效：claumon service restart

  之後查看與匯出：
     開啟 http://localhost:3131            # claumon dashboard
     claude-usage chart --days 7           # 一週用量曲線
     claude-usage export --month 2026-06   # 月度彙整 CSV

  注意：新開的終端機才會讓 claude / claumon / claude-usage 指令在 PATH 生效。
"@ -ForegroundColor Gray
