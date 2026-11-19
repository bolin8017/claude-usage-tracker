<#
.SYNOPSIS
    Hourly, lightweight Claude weekly-usage logger (replaces the claumon daemon).

.DESCRIPTION
    Run by the ClaudeUsageLogger scheduled task. Each run:
      1. Locates the tracked account's credential file (see -SwapProfile).
      2. Refreshes its OAuth token in place only if it is about to expire
         (token-refresh.ps1, same directory).
      3. With that single token, calls the usage API and the profile API, and
         appends one JSON line {ts, uuid, email, display_name, source, raw}
         to the usage log. `raw` is the usage API response verbatim, so a
         field rename upstream can be re-parsed later without data loss.

    It never scans ~/.claude: it reads exactly one credential file and the
    claude-swap owner marker. Every failure (including HTTP 429) is logged and
    the run ends; the next hourly run is the retry.

.PARAMETER SwapProfile
    claude-account-swap profile name of the account to track (e.g. "louis").
    The credential is ~/.claude/.credentials.json while that profile is checked
    in (~/.claude/.credential-owner equals the name), otherwise
    ~/.claude-<name>/.credentials.json. Empty = always ~/.claude.

.PARAMETER LogPath
    JSONL output. Default ~/.claude-usage/usage-log.jsonl.
#>
[CmdletBinding()]
param(
    [string]$SwapProfile = '',
    [string]$LogPath = (Join-Path $env:USERPROFILE '.claude-usage\usage-log.jsonl')
)

$ErrorActionPreference = 'Stop'

$UsageUrl   = 'https://api.anthropic.com/api/oauth/usage'
$ProfileUrl = 'https://api.anthropic.com/api/oauth/profile'
$DiagLog    = Join-Path $PSScriptRoot 'usage-logger.log'

function Write-Diag($msg) {
    $line = '[{0:yyyy-MM-ddTHH:mm:ssZ}] {1}' -f (Get-Date).ToUniversalTime(), $msg
    try { Add-Content -Path $DiagLog -Value $line -Encoding UTF8 } catch {}
}

# Keep the diagnostic log bounded.
try {
    if ((Test-Path $DiagLog) -and ((Get-Item $DiagLog).Length -gt 256KB)) {
        $tail = Get-Content $DiagLog -Tail 200
        Set-Content -Path $DiagLog -Value $tail -Encoding UTF8
    }
} catch {}

# GET a URL and return the body decoded as UTF-8. Windows PowerShell 5.1
# decodes JSON without a charset as ISO-8859-1, which garbles non-ASCII names,
# so decode the raw bytes ourselves.
function Get-Utf8Body($url, $token) {
    $headers = @{
        Authorization    = "Bearer $token"
        'anthropic-beta' = 'oauth-2025-04-20'
    }
    $r = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $url -Headers $headers -TimeoutSec 30
    return [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
}

# 1. Resolve the credential file of the tracked account.
$claudeDir = Join-Path $env:USERPROFILE '.claude'
$credPath  = Join-Path $claudeDir '.credentials.json'
if ($SwapProfile) {
    $owner  = 'default'
    $marker = Join-Path $claudeDir '.credential-owner'
    if (Test-Path $marker) { $owner = (Get-Content -Path $marker -Raw).Trim() }
    if ($owner -ne $SwapProfile) {
        $credPath = Join-Path $env:USERPROFILE ".claude-$SwapProfile\.credentials.json"
    }
}
if (-not (Test-Path $credPath)) {
    Write-Diag "no credential at $credPath - skip"
    return
}

# 2. Refresh only when near expiry (no-op otherwise). Best-effort.
$refresh = Join-Path $PSScriptRoot 'token-refresh.ps1'
if (Test-Path $refresh) { try { & $refresh -CredentialsPath $credPath } catch {} }

# 3. Read the token once; use it for both calls so the numbers and the
#    account identity always come from the same account.
try {
    $oauth = (Get-Content -Path $credPath -Raw -Encoding UTF8 | ConvertFrom-Json).claudeAiOauth
} catch {
    Write-Diag "credential unreadable: $($_.Exception.Message) - skip"
    return
}
if (-not $oauth -or -not $oauth.accessToken) {
    Write-Diag 'no claudeAiOauth.accessToken (API-key mode?) - skip'
    return
}

try {
    $usageText   = Get-Utf8Body $UsageUrl $oauth.accessToken
    $profileText = Get-Utf8Body $ProfileUrl $oauth.accessToken
} catch {
    $code = $null
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    Write-Diag "API call failed (HTTP $code): $($_.Exception.Message) - skip until next run"
    return
}

try {
    $acct = ($profileText | ConvertFrom-Json).account
    $null = $usageText | ConvertFrom-Json   # validate it is JSON before splicing it in
} catch {
    Write-Diag "unparseable API response: $($_.Exception.Message) - skip"
    return
}
if (-not $acct -or -not $acct.uuid) {
    Write-Diag 'profile response missing account.uuid - skip'
    return
}

# 4. Append one line. `raw` is spliced in verbatim (not re-serialised) so no
#    PowerShell version can reinterpret its values (PS7 turns ISO strings into
#    DateTime). Newlines between JSON tokens are insignificant; strip them so
#    the record stays on one line.
$head = [ordered]@{
    ts           = '{0:yyyy-MM-ddTHH:mm:ssZ}' -f (Get-Date).ToUniversalTime()
    uuid         = [string]$acct.uuid
    email        = [string]$acct.email
    display_name = [string]$acct.display_name
    source       = 'api'
} | ConvertTo-Json -Compress
$raw  = ($usageText -replace "[`r`n]", '').Trim()
$line = $head.Substring(0, $head.Length - 1) + ',"raw":' + $raw + '}'

try {
    $dir = Split-Path -Parent $LogPath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    # No-BOM UTF-8: PS 5.1's Add-Content -Encoding UTF8 writes a BOM that breaks json.loads.
    [System.IO.File]::AppendAllText($LogPath, "$line`n", (New-Object System.Text.UTF8Encoding($false)))
} catch {
    Write-Diag "log append failed: $($_.Exception.Message)"
}
