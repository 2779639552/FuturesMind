# AgentSense public tunnel keeper (Cloudflare quick tunnel)
# NOTE: this file is intentionally ASCII-only. Windows PowerShell 5.1
# (used by Task Scheduler) reads BOM-less files as GBK, which corrupts
# non-ASCII characters and breaks parsing. Keep it ASCII.
$ErrorActionPreference = 'SilentlyContinue'
$root    = 'D:\AgentSense\AgentSense'
$exe     = Join-Path $root 'tools\cloudflared.exe'
$urlFile = Join-Path $root 'public_url.txt'
$logFile = Join-Path $root 'tunnel_guard.log'
$outLog  = Join-Path $root 'tunnel_out.log'

function Write-Log([string]$msg) {
    Add-Content -Path $logFile -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $msg) -Encoding UTF8
}

# URL file must be BOM-less: a .bat wrapper reads it with `set /p`,
# and a UTF-8 BOM would prefix the URL with garbage bytes.
function Set-UrlFile([string]$u) {
    [System.IO.File]::WriteAllText($urlFile, $u, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-TunnelUrl {
    if (-not (Test-Path $outLog)) { return $null }
    $m = Select-String -Path $outLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
         ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } | Select-Object -Last 1
    return $m
}

# Local web service must be listening first; a tunnel to a dead port is useless.
$webAlive = $false
foreach ($line in @(& netstat -ano 2>$null)) {
    $t = [string]$line
    if ($t -match ':5000\s' -and $t -match 'LISTENING') { $webAlive = $true; break }
}
if (-not $webAlive) {
    Write-Log 'web service not listening on 5000 -> skip tunnel (web guard will revive it)'
    exit 0
}

$proc = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($proc) {
    # Already tunnelling. Make sure the URL file is populated (idempotent).
    $u = Get-TunnelUrl
    if ($u) {
        $cur = ''
        if (Test-Path $urlFile) { $cur = (Get-Content $urlFile -Raw).Trim() }
        if ($cur -ne $u) {
            Set-UrlFile $u
            Write-Log ("public url recorded: " + $u)
        }
    }
    exit 0
}

# Not running -> start a fresh quick tunnel.
Write-Log 'cloudflared not running -> starting new quick tunnel'
if (Test-Path $outLog) { Remove-Item $outLog -Force }
Start-Process -FilePath $exe `
    -ArgumentList 'tunnel', '--url', 'http://localhost:5000', '--no-autoupdate', '--logfile', $outLog, '--loglevel', 'info' `
    -WorkingDirectory (Join-Path $root 'tools') -WindowStyle Hidden

# Wait up to 60s for the assigned hostname to appear in the log.
$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $url = Get-TunnelUrl
    if ($url) { break }
}
if ($url) {
    Set-UrlFile $url
    Write-Log ("tunnel started, public url = " + $url)
} else {
    Write-Log 'tunnel started but public url not detected within 60s (check tunnel_out.log)'
}
