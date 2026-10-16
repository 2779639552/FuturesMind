# Isolation check (ASCII only on purpose: Windows PowerShell 5.1 parses non-BOM
# .ps1 as GBK, so Chinese comments here would break parsing).
$out = @()
$w = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'rag_worker' })
$web = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'web_app.py' })

$out += "=== RAG worker processes: " + $w.Count + " ==="
$w | ForEach-Object { $out += ("  worker PID " + $_.ProcessId + " parent=" + $_.ParentProcessId) }
$out += "=== web_app processes: " + $web.Count + " ==="
$web | ForEach-Object { $out += ("  web PID " + $_.ProcessId + " parent=" + $_.ParentProcessId) }

$out += ""
$out += "=== heavy deps per process ==="
foreach ($p in ($web + $w)) {
  $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
  if (-not $proc) { continue }
  $heavy = @($proc.Modules | Where-Object { $_.ModuleName -match 'torch|chromadb|sentence' })
  $role = if ($p.CommandLine -match 'rag_worker') { 'worker' } else { 'web   ' }
  $hd = if ($heavy.Count -eq 0) { 'NONE (isolated OK)' } else { ($heavy | ForEach-Object { $_.ModuleName }) -join ',' }
  $out += ($role + " PID " + $p.ProcessId + "  " + [math]::Round($proc.WorkingSet64 / 1MB, 0) + " MB  heavy: " + $hd)
}

$out += ""
$out += "=== port 5000 ==="
$l = Get-NetTCPConnection -State Listen -LocalPort 5000 -ErrorAction SilentlyContinue
$out += $(if ($l) { "listening PID " + $l.OwningProcess } else { "NOT listening" })

($out | Out-String) | Out-File 'D:\AgentSense\AgentSense\isolation_check.log' -Encoding UTF8
$out | Write-Output
