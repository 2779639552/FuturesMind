# 重启微信 Bot(5001):先停旧进程,再以独立进程启动(脱离任务生命周期)。
#
# 【本机适配 2026-09-14】原包内写死为作者机器路径
#   C:\Users\19168\Desktop\project4\AgentSense + C:\Program Files\Python312\python.exe,
#   在本机不存在 → 改为按脚本所在位置自动推导项目根,并用项目自带 venv 解释器。
$ErrorActionPreference = "Stop"

# 项目根 = 本脚本所在目录的上一级(scripts/ 的父目录)
$wd = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $wd "venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    # 兜底:venv 不可用时退回 PATH 上的 python
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $py) {
    Write-Output "ERROR: 找不到 python 解释器(venv\Scripts\python.exe 与 PATH 均不可用)"
    exit 1
}

Write-Output "项目目录: $wd"
Write-Output "解释器  : $py"

# 停掉占用 5001 的旧实例
$old = Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess
if ($old) {
    Stop-Process -Id $old -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Output "已停止旧进程 PID $old"
} else {
    Write-Output "5001 端口无监听(无需停止)"
}

# 启动新实例,日志落盘到项目目录
$out = Join-Path $wd "wechat_bot.log"
$err = Join-Path $wd "wechat_bot_err.log"
$p = Start-Process -FilePath $py -ArgumentList "wechat_bot.py" -WorkingDirectory $wd `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Output "已启动新进程 PID $($p.Id) (日志: wechat_bot.log / wechat_bot_err.log)"
