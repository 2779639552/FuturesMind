# AgentSense Web 服务守护脚本
# 由计划任务每 5 分钟调用一次(开机/登录时也会触发)。
#
# 【2026-09-14 修订】旧版用 Get-NetTCPConnection 判断 5000 端口,在计划任务
#   上下文里拿不到结果(返回空,$ErrorActionPreference=SilentlyContinue 又把它
#   静默吞掉),于是每 5 分钟都误判"端口空闲"并重复拉起 web_app.py。新实例被
#   web_app.py 的启动保护拦下后卡在 input() 等回车,越积越多(实测一天累积 8 组
#   僵尸进程,白占几百 MB 内存)。
#
# 【2026-09-17 修订 · 二次复发】换用 TCP 握手 + netstat 兜底后,"端口是否被监听"
#   这一问是准了;但**"是否已有实例正在启动"这一问仍不可靠** —— 它依赖
#   Get-CimInstance Win32_Process 枚举命令行,而该调用在计划任务会话里时好时坏
#   (返空时不抛异常)。一旦返空,watchdog 就"看不见"那个刚刚被拉起、还没来得及
#   绑定端口的实例,于是又判"空闲"→ 再拉起一个,5 分钟后循环。
#   日志实证:watchdog.log 累计 375 次 started 却只有 1 次 cleaned;
#   2026-09-14 09:04~09:44 连续 11 轮每 5 分钟拉起一次,直到 09:44:57 一次性
#   清掉 4 个僵尸(14504,14652,18568,6040);2026-09-17 11:25 又出现
#   "venv 实例(28948) + 系统 python 实例(2508)"双套并行 —— 前者的 4.1MB 内存
#   正是"启动保护拦下后原地待命"的特征。
#   根因:**启动窗口期(约 1 分钟)内没有任何可靠的"我在启动中"凭据**。
#   修法:引入 `_web_spawn.lock`(内容 = ISO 时间戳 | 新进程 PID)。拉起时写、
#   端口就绪时删;端口未就绪但锁内 PID 仍存活且未超 TTL(默认 300 秒)→ 直接
#   返回,不再依赖任何进程枚举。超 TTL 仍未监听 → 判定卡死,精确杀掉该 PID。
#   锁记录的是"我亲手拉起的那个 PID",比模糊匹配命令行可靠得多。
#
# 判定顺序(任一命中即认为服务活着,绝不轻易动进程):
#   ① 端口 5000 能完成 TCP 握手,或 netstat 见到 LISTENING;
#   ② spawn lock 未过期且其 PID 仍存活 → "正在启动中",直接返回;
#   ③ 兜底:能枚举到 web_app.py 进程且其启动不足 180 秒 → "正在启动中",返回。
# 只有三条都不成立(确认真空闲)才清理残留进程并拉起新实例。

$ErrorActionPreference = 'SilentlyContinue'

$dir = 'D:\AgentSense\AgentSense'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$logFile = "$dir\watchdog.log"

# 启动锁:记录"最近一次由本脚本拉起的进程"。
# 之所以需要它:端口绑定发生在启动流程的**后期**(要等依赖加载完),在那之前
# 端口一定是空的;而进程枚举又不保证可用,于是这段窗口期必须有自己的凭据。
$lockFile = "$dir\_web_spawn.lock"
$lockTtlSec = 300

function Test-Port5000Alive {
    # ① .NET 同步 TCP 连接:EndConnect 完成握手,连不上会抛异常
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', 5000, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000, $false)) {
            $client.EndConnect($iar)
            $client.Close()
            return $true
        }
        $client.Close()
    } catch { }

    # ② netstat 逐行解析兜底(不用 Select-String 管道,避免隐藏上下文差异)
    $lines = @(& netstat -ano 2>$null)
    foreach ($line in $lines) {
        $text = [string]$line
        if ($text -match ':5000\s' -and $text -match 'LISTENING') { return $true }
    }

    return $false
}

function Get-WebAppProcesses {
    # 只保留能真正拿到命令行的 CIM 来源。
    # 【坑】Get-Process 返回的 Process 对象**没有 CommandLine 属性**,
    # 旧版 `Get-Process python | Where-Object { $_.CommandLine -like '*web_app.py*' }`
    # 恒为空(死代码,读起来像有效实则从不生效)。
    # CIM 不可用时返回空数组 —— 此时由 spawn lock 兜底,不会误判空闲。
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -like '*web_app.py*' })
    } catch { $procs = @() }
    return $procs
}

function Read-SpawnLock {
    if (-not (Test-Path $lockFile)) { return $null }
    try {
        $raw = (Get-Content -Path $lockFile -Raw -ErrorAction Stop).Trim()
        $parts = $raw -split '\|'
        if ($parts.Count -lt 2) { return $null }
        $lockStamp = [datetime]::Parse($parts[0], [System.Globalization.CultureInfo]::InvariantCulture)
        $lockPid = [int]$parts[1]
        if ($lockPid -le 0) { return $null }
        return [pscustomobject]@{ Stamp = $lockStamp; Pid = $lockPid }
    } catch { return $null }
}

function Write-SpawnLock([int]$procId) {
    $line = '{0}|{1}' -f (Get-Date).ToString('o'), $procId
    Set-Content -Path $lockFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Remove-SpawnLock {
    if (Test-Path $lockFile) { Remove-Item -Path $lockFile -Force -ErrorAction SilentlyContinue }
}

function Stop-ProcessTree([int]$procId) {
    # 【关键】只杀 web_app.py 会留下它已 spawn 的 rag_worker.py 孤儿进程 ——
    # 孤儿 worker 继续占内存、并持有 chroma 库句柄,是"库被多进程并发写坏"的
    # 帮凶之一。这里按 ParentProcessId 先斩子进程(到孙辈)再斩父进程。
    try {
        $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction Stop)
        foreach ($kid in $kids) {
            try {
                $grandKids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($kid.ProcessId)" -ErrorAction Stop)
                foreach ($gk in $grandKids) {
                    Stop-Process -Id $gk.ProcessId -Force -ErrorAction SilentlyContinue
                }
            } catch { }
            Stop-Process -Id $kid.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

if (Test-Port5000Alive) {
    # 已有实例在监听:锁的使命已完成,清掉,免得下次误判"还在启动中"
    Remove-SpawnLock
    exit 0
}

# ── 端口不通:先看是不是"我刚拉起的实例还在启动中" ──────────────────────────
$lock = Read-SpawnLock
if ($lock) {
    $lockAlive = $null -ne (Get-Process -Id $lock.Pid -ErrorAction SilentlyContinue)
    $lockAgeSec = [int]((Get-Date) - $lock.Stamp).TotalSeconds
    if ($lockAlive -and $lockAgeSec -lt $lockTtlSec) {
        # 正常启动窗口:实例活着、还没绑定端口 → 给它时间,本轮不插手
        Add-Content -Path $logFile -Value "[$stamp] spawn lock held pid=$($lock.Pid) age=${lockAgeSec}s -> wait"
        exit 0
    }
    if ($lockAlive) {
        # 超过 TTL 仍未监听 → 判定卡死(典型:撞启动保护后停在提示等回车)
        Stop-ProcessTree -procId $lock.Pid
        Add-Content -Path $logFile -Value "[$stamp] killed stuck web_app pid=$($lock.Pid) tree (age=${lockAgeSec}s, no listen)"
        Start-Sleep -Seconds 2
    }
    Remove-SpawnLock
}

# ── 兜底:枚举进程,看有没有"刚启动不足 180 秒"的实例(例如有人手动起的) ────
$webprocs = @(Get-WebAppProcesses)
$starting = @()
foreach ($p in $webprocs) {
    $start = $null
    if ($p.PSObject.Properties.Name -contains 'StartTime') { $start = $p.StartTime }
    elseif ($p.CreationDate) { $start = $p.CreationDate }
    if ($start -and ((Get-Date) - $start).TotalSeconds -lt 180) { $starting += $p }
}
if ($starting.Count -gt 0) {
    # 正在启动中,给它时间,本轮不插手(避免误杀启动中的实例 / 重复拉起)
    exit 0
}

# ── 确认空闲:清掉"还活着但没有监听"的残留 web_app.py(多为启动保护拦下的僵尸) ─
$stale = @()
foreach ($p in $webprocs) { $stale += $p.ProcessId }
if ($stale.Count -gt 0) {
    foreach ($stalePid in $stale) {
        Stop-ProcessTree -procId $stalePid
    }
    Add-Content -Path $logFile -Value "[$stamp] cleaned $($stale.Count) stale web_app.py process(es): $($stale -join ',')"
    Start-Sleep -Seconds 2
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONFAULTHANDLER = '1'
$env:PYTHONUNBUFFERED = '1'

# 空 stdin 文件:万一新实例仍撞上启动保护,prompt 会立刻 EOF 退出而不是长期挂起
$emptyIn = "$dir\_empty_stdin.txt"
if (-not (Test-Path $emptyIn)) { New-Item -Path $emptyIn -ItemType File -Force | Out-Null }

# 拉起服务(独立进程,不依赖任何终端会话);-PassThru 拿到 PID 写进锁
$proc = Start-Process -FilePath "$dir\venv\Scripts\python.exe" `
    -ArgumentList 'web_app.py' `
    -WorkingDirectory $dir `
    -WindowStyle Hidden `
    -RedirectStandardInput $emptyIn `
    -RedirectStandardOutput "$dir\server_watchdog.log" `
    -RedirectStandardError "$dir\server_watchdog.err.log" `
    -PassThru

if ($proc) {
    Write-SpawnLock -procId $proc.Id
    Add-Content -Path $logFile -Value "[$stamp] port 5000 free -> started web_app.py (pid=$($proc.Id))"
} else {
    Add-Content -Path $logFile -Value "[$stamp] port 5000 free -> Start-Process FAILED"
}
