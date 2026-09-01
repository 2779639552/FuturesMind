# 重启 web 服务(5000):先停旧进程,再以独立进程启动(脱离任务生命周期)。
$ErrorActionPreference = "Stop"
$old = (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($old) {
    Stop-Process -Id $old -Force
    Start-Sleep -Seconds 2
    Write-Output "Stopped old PID $old"
} else {
    Write-Output "No listener on 5000"
}

$py = "C:\Program Files\Python312\python.exe"
$wd = "C:\Users\19168\Desktop\project4\AgentSense"
$out = Join-Path $wd "server_5000.log"
$err = Join-Path $wd "server_5000_err.log"

$p = Start-Process -FilePath $py -ArgumentList "web_app.py" -WorkingDirectory $wd `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Output "Started new PID $($p.Id)"
