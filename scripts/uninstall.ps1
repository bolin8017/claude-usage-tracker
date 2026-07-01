<#
.SYNOPSIS
    停止並移除 claumon 背景常駐 / 解除安裝 claumon 與本工具（保留 Claude Code CLI）。

.DESCRIPTION
    預設會依序處理：
      1. 停止 claumon 背景常駐（移除 watchdog 排程、開機啟動、結束程序）
      2. 刪除 claumon binary 與 watchdog 腳本、從使用者 PATH 移除
      3. pip uninstall 本工具 claude-usage-tracker
      （4. 加 -PurgeData 才會刪除 ~/.claumon 歷史資料）

    刻意不會執行 claumon.exe（在被防毒封鎖的機器上執行會再次觸發偵測），
    也不會更動 Claude Code CLI。全程不需系統管理員權限。

.PARAMETER StopOnly
    只停止背景常駐（移除 watchdog 排程 / 開機啟動、結束程序），保留已安裝的檔案與資料。
    適合「只想停掉一直跳的防毒封鎖通知」但暫時不想移除的情況。

.PARAMETER SkipTool
    不要 pip uninstall claude-usage-tracker。

.PARAMETER PurgeData
    連同 ~/.claumon 歷史資料庫一併刪除（預設保留）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
    完整移除 claumon 與本工具（保留歷史資料與 Claude Code）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -StopOnly
    只停掉背景常駐與通知，不移除任何檔案。
#>
[CmdletBinding()]
param(
    [switch]$StopOnly,
    [switch]$SkipTool,
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
function Write-Step($n, $msg) { Write-Host "`n=== [$n] $msg ===" -ForegroundColor Cyan }

$dir        = "$env:LOCALAPPDATA\Programs\claumon"
$taskName   = "ClaumonWatchdog"
$startupVbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'claumon.vbs'
$claumonData = "$env:USERPROFILE\.claumon"

# 1. 停止背景常駐（不執行 claumon.exe）----------------------------------------
Write-Step 1 "停止 claumon 背景常駐"
# watchdog 排程（每 3 分鐘重試、一直跳封鎖通知的元兇）
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "已移除排程：$taskName（若原本就沒有則略過）"
# 舊版 claumon service install 放的開機啟動 vbs（直接刪檔，不呼叫 claumon）
Remove-Item $startupVbs -Force -ErrorAction SilentlyContinue
# 結束仍在跑的 claumon
Stop-Process -Name claumon -Force -ErrorAction SilentlyContinue
Write-Host "已移除開機啟動、結束 claumon 程序。"

if ($StopOnly) {
    Write-Host "`n-StopOnly：只停止背景常駐，保留已安裝的檔案與資料。" -ForegroundColor Green
    Write-Host "之後要完整移除，重跑本腳本（不加 -StopOnly）即可。" -ForegroundColor Gray
    return
}

# 2. 移除 claumon binary 與 watchdog 腳本 -------------------------------------
Write-Step 2 "移除 claumon 檔案與 PATH"
if (Test-Path $dir) {
    Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
    Write-Host "已刪除資料夾：$dir"
} else {
    Write-Host "找不到 $dir，略過。"
}
# 從使用者 PATH 移除 claumon 目錄
$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($p) {
    $kept = $p -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $dir.TrimEnd('\')) }
    $new = $kept -join ';'
    if ($new -ne $p) {
        [Environment]::SetEnvironmentVariable('Path', $new, 'User')
        Write-Host "已從使用者 PATH 移除：$dir"
    }
}

# 3. 本工具 claude-usage-tracker ---------------------------------------------
if (-not $SkipTool) {
    Write-Step 3 "移除本工具（pip uninstall claude-usage-tracker）"
    if ([bool](Get-Command python -ErrorAction SilentlyContinue)) {
        python -m pip uninstall -y claude-usage-tracker
    } else {
        Write-Warning "找不到 python，略過。可手動執行：pip uninstall claude-usage-tracker"
    }
} else { Write-Step 3 "略過本工具移除（-SkipTool）" }

# 4. 歷史資料 ----------------------------------------------------------------
if ($PurgeData) {
    Write-Step 4 "刪除歷史資料 ~/.claumon"
    Remove-Item -Recurse -Force $claumonData -ErrorAction SilentlyContinue
    Write-Host "已刪除：$claumonData"
} else {
    Write-Host "`n保留歷史資料：$claumonData（要一併刪除請加 -PurgeData）" -ForegroundColor Gray
}

# 完成 -----------------------------------------------------------------------
Write-Host "`n----------------------------------------------------------" -ForegroundColor Green
Write-Host "claumon 與本工具已移除。Claude Code CLI 未更動。" -ForegroundColor Green
Write-Host "注意：PATH 變更需重開終端機才會生效。" -ForegroundColor Gray
