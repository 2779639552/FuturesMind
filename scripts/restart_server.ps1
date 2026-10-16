# 重启 web 服务(5000):先停旧进程,再以独立进程启动(脱离任务生命周期)。
$ErrorActionPreference = "Stop"
# 【2026-09-14】路径自动推导:脚本位于 <项目根>\scripts\,勿硬编码他机绝对路径
# (原实现写死 C:\Users\19168\Desktop\project4\AgentSense + Program Files\Python312,
#  在本机根本起不来)。优先用项目 venv,缺失才回退 PATH 里的 python。
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$old = (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($old) {
    Stop-Process -Id $old -Force
    Start-Sleep -Seconds 2
    Write-Output "Stopped old PID $old"
} else {
    Write-Output "No listener on 5000"
}

$wd = $root
$out = Join-Path $wd "server_5000.log"
$err = Join-Path $wd "server_5000_err.log"

$p = Start-Process -FilePath $py -ArgumentList "web_app.py" -WorkingDirectory $wd `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Output "Started new PID $($p.Id)"
