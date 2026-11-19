<#
.SYNOPSIS
    一鍵安裝 Claude 用量追蹤環境（Windows）。

.DESCRIPTION
    依序處理：
      1. 安裝 Claude Code CLI（官方安裝指令 irm https://claude.ai/install.ps1 | iex）
      2. 移除舊的 claumon 背景常駐（保留 ~/.claumon 歷史資料）
      3. 註冊排程 ClaudeUsageLogger：登入時與每小時各打一次額度 API，追加一行到
         ~/.claude-usage/usage-log.jsonl（token 快過期時順便續期）
      4. 安裝本工具 claude-usage-tracker（pip install -e .）

    無法自動化的步驟：`claude` 登入為瀏覽器互動授權，需由使用者本人完成；
    腳本最後會提示。全程不需系統管理員權限。

.PARAMETER SkipClaudeCode
    略過 Claude Code CLI 安裝。

.PARAMETER SwapProfile
    使用 claude-account-swap 時，要記錄的帳號 profile 名稱（例如 louis）。
    不論當下切到哪個帳號，都只記錄這個帳號。沒用 claude-swap 就不用給。

.PARAMETER SkipLogger
    略過用量記錄排程。

.PARAMETER SkipTool
    略過本工具（pip install -e .）安裝。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SwapProfile louis
#>
[CmdletBinding()]
param(
    [switch]$SkipClaudeCode,
    [ValidatePattern('^[A-Za-z0-9_-]*$')]
    [string]$SwapProfile = '',
    [switch]$SkipLogger,
    [switch]$SkipTool
)

$ErrorActionPreference = "Stop"
$loggerOk   = $false     # 記錄排程是否註冊成功（完成提示會據此如實顯示）
$loggerTask = "ClaudeUsageLogger"
$loggerDir  = "$env:LOCALAPPDATA\Programs\claude-usage-logger"

function Write-Step($n, $msg) { Write-Host "`n=== [$n] $msg ===" -ForegroundColor Cyan }
function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

$repoRoot = Split-Path -Parent $PSScriptRoot

# 1. Claude Code CLI ---------------------------------------------------------
if (-not $SkipClaudeCode) {
    Write-Step 1 "安裝 Claude Code CLI"
    if (Test-Cmd "claude") {
        Write-Host "已安裝，略過。"
    } else {
        # 官方 PowerShell 安裝指令；在其預期環境下執行（暫時放寬 ErrorAction，避免中途中止）
        Write-Host "執行官方安裝指令：irm https://claude.ai/install.ps1 | iex"
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            irm https://claude.ai/install.ps1 | iex
        } catch {
            Write-Warning "Claude Code 安裝失敗：$($_.Exception.Message)"
            Write-Warning "可稍後手動執行：irm https://claude.ai/install.ps1 | iex"
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        # 官方安裝腳本改的是登錄檔 PATH；同步到本行程後再確認是否真的裝好（iex 不丟例外時也能抓到失敗）
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (Test-Cmd "claude")) {
            Write-Warning "安裝後偵測不到 claude 指令，可能未安裝成功或需重開終端機；可稍後手動執行：irm https://claude.ai/install.ps1 | iex"
        }
    }
} else { Write-Step 1 "略過 Claude Code CLI" }

# 2. 讓舊的 claumon 退役 ---------------------------------------------------------
# claumon 常駐監看 ~/.claude 並每 2 分鐘打一次 API；改用下方每小時的記錄排程後就不需要它。
# 只移除程式與排程，~/.claumon 的歷史資料保留（舊的 chart / export 指令仍可讀）。
Write-Step 2 "移除舊的 claumon 背景常駐（保留 ~/.claumon 歷史資料）"
$claumonDir = "$env:LOCALAPPDATA\Programs\claumon"
if ((Get-ScheduledTask -TaskName "ClaumonWatchdog" -ErrorAction SilentlyContinue) -or (Test-Path $claumonDir)) {
    Unregister-ScheduledTask -TaskName "ClaumonWatchdog" -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'claumon.vbs') -Force -ErrorAction SilentlyContinue
    Stop-Process -Name claumon -Force -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $claumonDir -ErrorAction SilentlyContinue
    $p = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($p) {
        $new = ($p -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $claumonDir) }) -join ';'
        if ($new -ne $p) { [Environment]::SetEnvironmentVariable('Path', $new, 'User') }
    }
    Write-Host "已移除 claumon 程式與排程。"
} else {
    Write-Host "沒有安裝 claumon，略過。"
}

# 3. 每小時用量記錄 ------------------------------------------------------------
if (-not $SkipLogger) {
    Write-Step 3 "安裝每小時用量記錄排程（$loggerTask）"
    $credMarker = Join-Path $env:USERPROFILE '.claude\.credential-owner'
    if (-not $SwapProfile -and (Test-Path $credMarker)) {
        Write-Warning "偵測到 claude-swap 多帳號設定，但沒有指定 -SwapProfile：切換帳號後會改記錄當下登入的帳號。"
    }
    New-Item -ItemType Directory -Force $loggerDir | Out-Null
    foreach ($f in 'usage-logger.ps1', 'token-refresh.ps1') {
        Copy-Item -Force (Join-Path $PSScriptRoot $f) (Join-Path $loggerDir $f)
    }

    # 以 VBS 包一層：排程直接起 powershell.exe 會閃一下主控台視窗；
    # 由 wscript（無主控台）以視窗模式 0 啟動就完全不閃窗。
    $profileArg = if ($SwapProfile) { " -SwapProfile $SwapProfile" } else { "" }
    $vbs = Join-Path $loggerDir 'usage-logger.vbs'
    @"
' Run the hourly Claude usage logger fully hidden (no console window flash).
Dim fso, ps1
Set fso = CreateObject("Scripting.FileSystemObject")
ps1 = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "usage-logger.ps1")
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & ps1 & """$profileArg", 0, False
"@ | Set-Content -Path $vbs -Encoding ASCII

    # 用目前登入身分的完整名稱：Entra/AzureAD 機器上是 AzureAD\user，自己拼會解析失敗。
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B //Nologo `"$vbs`""
    $trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $me
    $trigTick  = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    try {
        Register-ScheduledTask -TaskName $loggerTask -Action $action -Trigger $trigLogon, $trigTick `
            -Settings $settings -Principal $principal `
            -Description "Log Claude weekly usage once an hour and at logon (no admin)." -Force | Out-Null
        $loggerOk = $true
        Start-ScheduledTask -TaskName $loggerTask -ErrorAction SilentlyContinue
        Write-Host "已建立排程：$loggerTask（登入時與每小時各記錄一次）"
    } catch {
        Write-Warning "排程註冊失敗（可能被群組原則封鎖）：$($_.Exception.Message)"
    }
} else { Write-Step 3 "略過用量記錄排程" }

# 4. 本工具 ------------------------------------------------------------------
if (-not $SkipTool) {
    Write-Step 4 "安裝 claude-usage-tracker（pip install -e .）"
    # Prefer the py launcher: it finds registered installs regardless of PATH,
    # so another tool's venv (often created without pip) can't shadow it.
    # Probes write to stderr on failure; under "Stop" Windows PowerShell 5.1
    # would turn that into a terminating error, so relax it for this step.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pyCmd = $null
    if (Test-Cmd "py") {
        & py -3 -c "pass" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyCmd = @("py", "-3") }
    }
    if (-not $pyCmd -and (Test-Cmd "python")) { $pyCmd = @("python") }

    if (-not $pyCmd) {
        Write-Warning "找不到 python；請先安裝 Python 3.9+（例如 winget install Python.Python.3.12）後再執行：py -3 -m pip install -e ."
    } else {
        $pyExe  = $pyCmd[0]
        $pyArgs = @($pyCmd | Select-Object -Skip 1)
        $pyPath = & $pyExe @pyArgs -c "import sys; print(sys.executable)" 2>$null
        Write-Host "使用 Python：$pyPath"
        & $pyExe @pyArgs -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "此 Python 沒有 pip（可能是其他工具的虛擬環境）：$pyPath`n請安裝正式版 Python 3.9+ 後執行：py -3 -m pip install -e `"$repoRoot`""
        } else {
            & $pyExe @pyArgs -m pip install -e $repoRoot
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "pip install 失敗（exit $LASTEXITCODE），請檢查上方錯誤訊息。"
            }
        }
    }
    $ErrorActionPreference = $prevEap
} else { Write-Step 4 "略過本工具" }

# 完成提示 -------------------------------------------------------------------
if ($SkipLogger) {
    $bgSection = "  用量記錄：本次以 -SkipLogger 略過。"
} elseif ($loggerOk) {
    $bgSection = @"
  用量記錄（已設定，免系統管理員）：
     排程 $loggerTask 在登入時與每小時各記錄一次，寫入 ~/.claude-usage/usage-log.jsonl。
     執行紀錄：Get-Content "$loggerDir\usage-logger.log" -Tail 20
     想停用：  Unregister-ScheduledTask -TaskName $loggerTask -Confirm:`$false
"@
} else {
    $bgSection = @"
  用量記錄（！未設定成功，請留意）：
     排程 $loggerTask 未建立（可能被群組原則封鎖或權限不足），不會自動記錄；可重跑本腳本。
"@
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Green
Write-Host "安裝流程完成。" -ForegroundColor Green
Write-Host @"

  >> 還沒登入 Claude 的話，先執行 claude 依指示在瀏覽器完成登入，記錄才會有資料。

$bgSection

  每月報表：
     claude-usage weekly --month 2026-11   # 該月每週用量 CSV + 長條圖

  注意：新開的終端機才會讓 claude / claude-usage 指令在 PATH 生效。
"@ -ForegroundColor Gray
