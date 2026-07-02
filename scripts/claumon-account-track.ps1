<#
.SYNOPSIS
    偵測目前登入的 Claude 帳號，換帳號時追加一筆到 account-timeline.jsonl。

.DESCRIPTION
    claumon 讀 ~/.claude/.credentials.json 打訂閱用量 API，但憑證檔內沒有帳號識別碼，
    無法從本機判斷「這是哪個帳號」或「換帳號 vs 一般 token 續期」。本腳本用當前 access
    token 打 profile API 取穩定的 account.uuid，只有當它與時間軸最後一筆不同（或時間軸為空）
    時，才追加一筆邊界。供 claude-usage 依帳號分流用量。

    全程 best-effort：任何錯誤只記 log、不丟例外，不影響 watchdog 其餘工作。
    不快取、不碰 usage.db、不寫入任何 token。

.PARAMETER CredentialsPath
    憑證檔路徑。預設 ~/.claude/.credentials.json。

.PARAMETER TimelinePath
    帳號時間軸路徑。預設 ~/.claumon/account-timeline.jsonl。
#>
[CmdletBinding()]
param(
    [string]$CredentialsPath = (Join-Path $env:USERPROFILE '.claude\.credentials.json'),
    [string]$TimelinePath    = (Join-Path $env:USERPROFILE '.claumon\account-timeline.jsonl')
)

$ErrorActionPreference = 'Stop'

$ProfileUrl = 'https://api.anthropic.com/api/oauth/profile'
$LogFile    = Join-Path $PSScriptRoot 'claumon-account-track.log'

function Write-Log($msg) {
    $line = '[{0:yyyy-MM-ddTHH:mm:ssZ}] {1}' -f (Get-Date).ToUniversalTime(), $msg
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

# log 只留最近 200 行，避免無限成長
try {
    if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 256KB)) {
        $tail = Get-Content $LogFile -Tail 200
        Set-Content -Path $LogFile -Value $tail -Encoding UTF8
    }
} catch {}

if (-not (Test-Path $CredentialsPath)) {
    Write-Log "no credentials file at $CredentialsPath (never logged in?) - skip"
    return
}

try {
    $oauth = (Get-Content -Path $CredentialsPath -Raw -Encoding UTF8 | ConvertFrom-Json).claudeAiOauth
} catch {
    Write-Log "credentials unreadable/parse failed: $($_.Exception.Message) - skip"
    return
}
if (-not $oauth -or -not $oauth.accessToken) {
    Write-Log 'no claudeAiOauth.accessToken (API-key mode or different auth) - skip'
    return
}

# 打 profile API 取穩定 account.uuid
try {
    $headers = @{
        Authorization    = "Bearer $($oauth.accessToken)"
        'anthropic-beta' = 'oauth-2025-04-20'
    }
    $prof = Invoke-RestMethod -Method Get -Uri $ProfileUrl -Headers $headers -TimeoutSec 20
} catch {
    Write-Log "profile API call failed: $($_.Exception.Message) - skip"
    return
}
$acct = $prof.account
if (-not $acct -or -not $acct.uuid) {
    Write-Log 'profile response missing account.uuid - skip'
    return
}

# 讀時間軸最後一筆 uuid；相同就不寫
$lastUuid = $null
if (Test-Path $TimelinePath) {
    try {
        $lastLine = Get-Content -Path $TimelinePath -Tail 1 -Encoding UTF8
        if ($lastLine) { $lastUuid = ($lastLine | ConvertFrom-Json).uuid }
    } catch {}
}
if ($lastUuid -eq $acct.uuid) { return }   # 帳號沒變，正常路徑

# 換帳號（或首筆）→ append 一行
$entryHash = [ordered]@{
    ts           = '{0:yyyy-MM-ddTHH:mm:ssZ}' -f (Get-Date).ToUniversalTime()
    uuid         = $acct.uuid
    email        = $acct.email
    display_name = $acct.display_name
}
$entry = $entryHash | ConvertTo-Json -Compress
try {
    $dir = Split-Path -Parent $TimelinePath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    # Use AppendAllText with explicit no-BOM UTF-8; PS 5.1's Add-Content -Encoding UTF8 adds BOM
    # which breaks json.loads() on the first line.
    $noBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($TimelinePath, "$entry`n", $noBom)
    Write-Log "account switch recorded: $($acct.email) ($($acct.uuid))"
} catch {
    Write-Log "timeline append failed: $($_.Exception.Message)"
}
