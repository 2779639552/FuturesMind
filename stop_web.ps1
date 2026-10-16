# AgentSense web service clean stop.
#
# NOTE: this file is intentionally ASCII-only. Windows PowerShell 5.1 (used by
# the .bat wrapper) reads BOM-less files as GBK, which corrupts non-ASCII
# characters and can break parsing. Keep it ASCII.
#
# Why this exists: the old stop_web.bat only killed whatever process was
# LISTENING on :5000. That leaves behind two kinds of leftovers:
#   (a) the rag_worker.py child process -- it never listens on any port, so
#       port-based lookup never sees it; it keeps holding the chroma index
#       handles and a few hundred MB of RAM;
#   (b) web_app.py instances that were blocked by the startup guard and are
#       sitting at the "press Enter to exit" prompt without ever binding
#       :5000 -- again invisible to port-based lookup.
# Those leftovers accumulate, and a lingering half-started instance is a known
# cause of "double instance" states (two web_app.py, one holding the port and
# one stuck in the guard).
#
# So: kill the whole family -- anything running web_app.py or rag_worker.py,
# plus whatever holds :5000 as a safety net.

$ErrorActionPreference = 'SilentlyContinue'

function Get-AgentSenseProcs {
    $all = @()
    $all += @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*web_app.py*' -or $_.CommandLine -like '*rag_worker.py*' })

    # Safety net: whatever is listening on :5000, even if the command line
    # could not be read (CIM unavailable, unusual interpreter name, etc.).
    $conn = Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction SilentlyContinue
    if ($conn) {
        foreach ($owner in @($conn.OwningProcess | Select-Object -Unique)) {
            $all += @(Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue)
        }
    }
    return @($all | Where-Object { $_ } | Sort-Object ProcessId -Unique)
}

$procs = @(Get-AgentSenseProcs)
if ($procs.Count -eq 0) {
    Write-Host "  Nothing to stop - no web_app.py / rag_worker.py process found."
    exit 0
}

Write-Host ("  Found {0} AgentSense python process(es):" -f $procs.Count)
foreach ($p in $procs) {
    $kind = 'web_app'
    if ($p.CommandLine -like '*rag_worker.py*') { $kind = 'rag_worker' }
    Write-Host ("    Killing PID {0} ({1})" -f $p.ProcessId, $kind)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

$left = @(Get-AgentSenseProcs)
if ($left.Count -gt 0) {
    Write-Host ("  WARNING: {0} process(es) still alive: {1}" -f $left.Count, (($left | ForEach-Object { $_.ProcessId }) -join ','))
} else {
    Write-Host "  Done. All AgentSense python processes stopped, port :5000 released."
}
