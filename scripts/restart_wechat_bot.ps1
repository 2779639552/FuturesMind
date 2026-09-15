# 重启微信 Bot(5001):先停旧进程,再以独立进程启动(脱离任务生命周期)。
$ErrorActionPreference = "Stop"
$old = (Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($old) {
    Stop-Process -Id $old -Force
    Start-Sleep -Seconds 2
    Write-Output "Stopped old PID $old"
} else {
    Write-Output "No listener on 5001"
}

$py = "C:\Program Files\Python312\python.exe"
$wd = "C:\Users\19168\Desktop\project4\AgentSense"
$out = Join-Path $wd "wechat_bot.log"
$err = Join-Path $wd "wechat_bot_err.log"

$p = Start-Process -FilePath $py -ArgumentList "wechat_bot.py" -WorkingDirectory $wd `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Output "Started new PID $($p.Id)"
