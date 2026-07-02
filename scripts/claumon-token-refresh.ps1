<#
.SYNOPSIS
    讓 Claude Code 的 OAuth access token 保持新鮮，使 claumon 的額度儀表不會斷線。

.DESCRIPTION
    claumon 會讀 ~/.claude/.credentials.json 去打訂閱用量 API，但它自己「不會」續期，
    完全依賴 Claude Code 幫忙刷新 token。當 Claude Code 沒在跑（例如整晚閒置），約 8 小時
    的 access token 過期後，claumon 的額度儀表就會變空，直到你下次手動 `claude` 登入。

    本腳本用 Claude Code 標準的 OAuth refresh grant 續期，但「只在 token 快過期或已過期時」
    才動作（緩衝 $BufferSeconds 秒）。因為 Claude Code daemon 會在過期前約 4 分鐘就主動續期，
    只要它活著就一定比本腳本早把 expiresAt 推遠，本腳本便不會觸發 —— 天然避開 refresh token
    一次性輪替所造成的衝突（若兩邊搶著換，晚換的那方會失效而被強制重新登入）。本腳本只在
    「沒有其他人維持 token」的空窗才補上。

    設計要點：
      * 每次執行都「即時讀檔」，永不快取 token，確保拿到的是最新（可能剛被輪替過）的 refresh token。
      * 寫回前再讀一次檔：若 HTTP 期間 Claude Code 剛好也續期了，就保留它的、不覆蓋。
      * 原子寫入（先寫暫存檔再 Move），中途失敗也不會弄壞 .credentials.json。
      * 全程 best-effort：任何錯誤只記 log、不丟例外，不影響 watchdog 其餘工作。

.PARAMETER BufferSeconds
    距離過期還剩多少秒（含已過期）時才續期。預設 120。務必小於 Claude Code daemon 的主動續期
    緩衝（實測約 240 秒），才能保證 daemon 活著時一定比本腳本先動手。

.PARAMETER CredentialsPath
    憑證檔路徑。預設 ~/.claude/.credentials.json。
#>
[CmdletBinding()]
param(
    [int]$BufferSeconds = 120,
    [string]$CredentialsPath = (Join-Path $env:USERPROFILE '.claude\.credentials.json')
)

$ErrorActionPreference = 'Stop'

# Claude Code 公開 OAuth client 與續期端點（以假 token 實測：api 端點回 invalid_grant，為正解）。
$TokenUrl = 'https://api.anthropic.com/v1/oauth/token'
$ClientId = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
$LogFile  = Join-Path $PSScriptRoot 'claumon-token-refresh.log'

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

# 即時讀檔（永不快取），確保拿到最新的 refresh token
try {
    $raw  = Get-Content -Path $CredentialsPath -Raw -Encoding UTF8
    $json = $raw | ConvertFrom-Json
} catch {
    Write-Log "credentials unreadable/parse failed: $($_.Exception.Message) - skip"
    return
}

$oauth = $json.claudeAiOauth
if (-not $oauth -or -not $oauth.refreshToken) {
    Write-Log 'no claudeAiOauth.refreshToken (API-key mode or different auth) - skip'
    return
}

# expiresAt 是 epoch 毫秒
$nowMs    = [long][DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$expMs    = [long]$oauth.expiresAt
$secsLeft = [math]::Round(($expMs - $nowMs) / 1000)

if ($secsLeft -gt $BufferSeconds) {
    # 還有充裕效期（或 daemon 已幫忙續過）—— 什麼都不用做，正常路徑
    return
}

Write-Log "token expires in ${secsLeft}s (<= ${BufferSeconds}s) - refreshing"

$body = @{
    grant_type    = 'refresh_token'
    refresh_token = $oauth.refreshToken
    client_id     = $ClientId
} | ConvertTo-Json -Compress

try {
    $resp = Invoke-RestMethod -Method Post -Uri $TokenUrl -ContentType 'application/json' -Body $body -TimeoutSec 30
} catch {
    Write-Log "refresh HTTP call failed: $($_.Exception.Message)"
    return
}

if (-not $resp.access_token) {
    Write-Log 'refresh response missing access_token - abort (file untouched)'
    return
}

# 寫回前再讀一次：若 HTTP 期間 Claude Code 也續期了（expiresAt 往後跳），保留它的、不覆蓋。
try {
    $curJson = (Get-Content -Path $CredentialsPath -Raw -Encoding UTF8) | ConvertFrom-Json
    if (([long]$curJson.claudeAiOauth.expiresAt) -gt ($expMs + 1000)) {
        Write-Log 'another refresher updated the token meanwhile - keeping theirs'
        return
    }
} catch {}

$expiresIn = if ($resp.expires_in) { [long]$resp.expires_in } else { 28800 }  # 缺欄位就當 8 小時
$newExpMs  = $nowMs + ($expiresIn * 1000)

$oauth.accessToken = $resp.access_token
if ($resp.refresh_token) { $oauth.refreshToken = $resp.refresh_token }  # 輪替後的新 refresh token
$oauth.expiresAt   = $newExpMs
$json.claudeAiOauth = $oauth

# 原子寫入：先寫暫存檔再 Move，且不含 BOM（比照 Claude Code 的格式）
$out = $json | ConvertTo-Json -Depth 20 -Compress
$tmp = "$CredentialsPath.tmp"
try {
    [System.IO.File]::WriteAllText($tmp, $out, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -Force $tmp $CredentialsPath
    $mins = [math]::Round(($newExpMs - $nowMs) / 60000)
    Write-Log "refresh OK - new token valid ~${mins} min"
} catch {
    Write-Log "write-back failed: $($_.Exception.Message)"
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
