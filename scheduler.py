"""FuturesMind Scheduler — Automated collection with APScheduler."""

# =============================================================================
# 【模块角色】
#   scheduler.py 是项目的"定时任务调度中心",基于 APScheduler 的后台调度器
#   (BackgroundScheduler)在后台按预设时间自动执行数据采集与处理流程,让整个
#   系统无需人工操作也能每天自动运转。
#
#   主要任务:
#     1. 每日定时管道(_run_daily_pipeline):每天 08:00 与 18:00 各执行一次,
#        先跑微博采集(batch_collect.py 子进程),再做情感聚合、回测与情感
#        JSON 重新生成。
#     2. 健康检查(_health_check):每 30 分钟一次,检测最近的采集是否有失败,
#        若有则在告警中心写入一条 warning 告警。
#     3. 平台采集(_run_platform_collection):单个平台的采集任务,通过启动
#        batch_collect.py 子进程实现,结果与异常都会写入数据库并生成告警。
#     4. 华泰天玑研报接入(_run_htfc_collection):research_times 时刻 +15 分钟跑
#        research_collector_htfc.py 子进程(华泰官方源,21 品种池日报);
#        job id 前缀 research_htfc_,告警前缀 htfc_*。
#     5. 国君云 API 研报接入(_run_gtja_collection):research_times 时刻 +30 分钟跑
#        research_collector_gtja.py 子进程(researchReportAttachmentQuery 官方源,
#        21 品种池/近 2 天/单次 ≤30 篇);job id 前缀 research_gtja_,
#        告警前缀 gtja_*。
#     6. 东证繁微观点接入(_run_dongzheng_collection):research_times 时刻 +40 分钟跑
#        research_collector_dongzheng.py 子进程(繁微 Fiona MCP viewpoint 端点,
#        匿名可调用,动态快评近 1 天);job id 前缀 research_dz_,告警前缀 dz_*。
#        三源错峰(15/30/40min)的原因:并跑峰值 LLM 并发 10 > 账户上限 5 会撞 429,
#        错峰后任一时刻全局并发 ≤4。
#        【源裁撤】发现报告源(_run_research_collection,job id research_{time})已于
#        2026-09-09 剔除:三家 API 源已覆盖且重复度高(research_collector.py 保留)。
#     7. 仓单采集(_run_warrant_collection):warrant_times(默认 08:00 / 16:40)跑
#        warrant_collect.py 子进程,采四家交易所仓单日报并重建「仓单分析」站点
#        数据(总览/细则/历史走势);job id 前缀 warrant_collect_,告警前缀
#        warrant_collect_*(2026-09-21 新增)。
#        · 08:00 为需求指定;16:40 追加是因为四所日报在收盘后(~16:00)才发布,
#          只跑早间会让当天数据迟一天进看板(2026-09-21 用户反馈的"日期不动")。
#        · _warrant_startup_catchup:服务启动时若今天的时点已过却漏采(服务当时
#          未运行),延后 90s 自动补一次,避免整天停在上一个交易日。
#
#   所有任务都通过数据库(get_db())记录状态与告警,便于 Web 前端展示;
#   start_scheduler() 返回的调度器对象由调用方(通常是 web_app/main)持有。
# =============================================================================

import json  # 【调用包】解析子进程的 COLLECT_RUN 机器可读报告(采集结论/建议)
import logging  # 【调用包】日志记录(RAG 索引等任务内部异常留痕)
import os  # 【调用包】路径/环境变量操作(定位虚拟环境解释器)
import subprocess  # 【调用包】启动子进程(采集/管道脚本)
import threading  # 【调用包】入库互斥锁(避免自动触发与定时任务并发)
import time  # 【调用包】失败自动重试前的退避等待(2026-10-19)
from functools import partial  # 【调用包】把重试步骤绑定成无参可调用(避免局部 return)
import sys  # 【调用包】当前解释器路径定位(复用同一虚拟环境)
from contextlib import suppress  # 【调用包】忽略指定异常的上下文管理器(解析条数容错)
from datetime import datetime, timedelta  # 【调用包】当前时间/日期生成 + 启动补采延时(2026-09-21)

# 子进程 batch_collect.py 的语义化退出码(与思路2/validate/batch_collect.py 的 EXIT_* 对齐)。
# 【为什么需要】旧代码只判 `returncode != 0`,把"凭证过期需重新登录"(3)、
# "被平台拦截/接口变更"(4)与"代码异常"(5)压成同一条 collection_failed,用户
# 看不出该做什么 —— 这是"静默失效"另一半的来源:不是没报警,是报了没用的警。
EXIT_AUTH_FAILED = 3  # 【常量】凭证缺失/失效 → 需要重新登录
EXIT_BLOCKED = 4  # 【常量】全部关键词搜索失败(反爬/接口变更)
EXIT_ERROR = 5  # 【常量】其他致命错误(初始化异常、平台未注册)
RUN_LINE_PREFIX = "COLLECT_RUN:"  # 【常量】子进程 stdout 里那一行机器可读报告的标记

from apscheduler.schedulers.background import (
    BackgroundScheduler,  # 【调用包】APScheduler 后台调度器(守护线程运行)
)
from apscheduler.triggers.cron import CronTrigger  # 【调用包】cron 表达式触发器(定时/周期任务)
from apscheduler.triggers.date import DateTrigger  # 【调用包】一次性触发器(启动补采,2026-09-21)

import collect_pool  # 【调用包】有界并行调度(与网页"一键更新"共用同一并发度规则/顺序/隔离语义)
from database import get_db  # 【调用包】数据库访问(记录采集状态与告警)
from path_utils import resolve_think2_dir  # 【调用包】路径集中管理(定位思路2 数据目录)
from update_tracker import append_recorded_round  # 【调用包】写"更新轮次"留痕(标注手动/定时来源)

THINK2_DIR = resolve_think2_dir()  # 【变量】数据工作目录(采集/管道子进程的 cwd)

logger = logging.getLogger(__name__)  # 【变量】模块日志器(任务内部异常留痕,不打断调度)

# ── Global scheduler ──────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None  # 【变量】全局调度器单例(启动后持有,供停止/重启)


def _collector_env() -> dict:
    """研报采集子进程的环境变量:显式清掉 web 服务标记,保持子进程既有行为。

    【为什么 2026-09-14】web_app 的 RAG worker 隔离模式改用进程级环境变量
    AGENTSENSE_WEB_SERVER=1 做标记(见 web_app._RAG_WORKER_MODE)。采集器子进程
    默认继承父进程环境,会连带继承这个标记,从而在子进程里也走 rag_worker 模式——
    那是行为变更(多起一个 worker 进程)。而采集器子进程本身是一次性进程,进程内
    直调 RAG 已经被进程边界隔离,无需再套一层。这里显式清除,行为与原来一致。
    """
    env = dict(os.environ)
    env.pop("AGENTSENSE_WEB_SERVER", None)
    return env


# 【研报采集子进程完整输出日志】目录 + 摘录工具(2026-09-17)。
# 【为什么必须落盘】scheduler 原先把 `output[-500:]` 写进告警,而两条最关键的证据
#   恰好分别被切在两头:① Python 未捕获异常的类型/消息在输出的**最后一行**;
#   ② 致命错误(原生崩溃)的 `Fatal Python error:` 头 + 崩溃线程栈在**最前几行**。
#   只留中间 500 字符的结果是:三个不同采集源(华泰/国君/东证)的告警看起来"堆栈
#   一模一样",2026-09-15~17 三天研报卡死因此查不出真凶。现在完整输出落盘,
#   告警正文改为"保留头尾、中间折叠",两边都不再丢证据。
RESEARCH_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _explain_returncode(code: int) -> str:
    """把进程退出码翻成人话 —— 原生崩溃与普通异常是两条完全不同的排障路径。

    【为什么】access violation 退出码 3221225477 与普通异常的 1 形态完全不同:
    后者能进 Python 的 except(因此库里会落 status=error),前者**直接带走整个进程**
    (连同所有并行 worker 线程),所以已 insert 的行会永久停在 processing 而**没有
    任何 error 落库** —— 这正是"十几篇研报一直显示处理中"的鉴别特征。
    """
    if code == 0:
        return "ok"
    if code in (3221225477, -1073741819):
        return "原生崩溃 access violation (0xC0000005):非 Python 异常,进程被直接终止"
    if code in (3221225725, -1073741571):
        return "栈溢出 stack overflow (0xC00000FD)"
    if code == 1:
        return "Python 未捕获异常 / 业务失败"
    return "非零退出"


def _persist_collector_output(name: str, output: str, returncode: int) -> str:
    """把采集子进程的完整输出追加落盘,返回日志路径(失败返回空串,绝不抛错)。

    【参数】name: 源名(htfc/gtja/dz)用作文件名;output: 合并后的 stdout+stderr;
            returncode: 退出码(写进日志头,可一眼区分 0/1/3221225477)。
    【返回】日志文件绝对路径;落盘失败返回 ""(绝不影响采集主流程)。
    """
    try:
        os.makedirs(RESEARCH_LOG_DIR, exist_ok=True)
        path = os.path.join(
            RESEARCH_LOG_DIR, f"research_collect_{name}_{datetime.now():%Y%m%d}.log"
        )
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                f"\n{'=' * 78}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {name} "
                f"returncode={returncode} → {_explain_returncode(returncode)}\n{'=' * 78}\n"
            )
            fh.write(output or "(无输出)")
            if not (output or "").endswith("\n"):
                fh.write("\n")
        return path
    except Exception:  # noqa: BLE001 - 落盘失败绝不拖垮采集
        logging.getLogger(__name__).warning("persist collector output failed", exc_info=True)
        return ""


# 【子进程输出标记】`_capture_run` 在原始文件里先写这行,再按它切出子进程正文。
_CHILD_MARK = "---------- child-output-begin ----------"


class _ChildResult:
    """`_capture_run` 的返回壳:属性形状与 `subprocess.CompletedProcess` 常用字段一致。

    这样各调用点**只需替换 `subprocess.run(...)` 那几行**,下游 `result.stdout` /
    `result.returncode` / `(proc.stderr or '')` 全部原样可用,不引入二次改动风险。
    """

    __slots__ = ("stdout", "stderr", "returncode")

    def __init__(self, stdout: str, stderr: str, returncode: int):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _capture_run(cmd: list, *, cwd=None, env=None, timeout=None, tag: str = "child"):
    """跑一次性子进程,把 stdout+stderr **重定向到文件**再读回(顶替 `capture_output=True`)。

    【2026-09-21 实测 · 为什么必须这么写】
    当父进程**没有控制台**时(计划任务/隐藏窗口拉起的 web_app 就是这种),`subprocess.run(
    capture_output=True, text=True)` 对**任何**子进程都返回**空 stdout/stderr**
    (returncode 仍正确)。复现:在计划任务里跑
    `subprocess.run([py,'-u','-c',"print('x')"], capture_output=True)` → stdout 长度 0;
    同一进程改成 `stdout=open(f,'w')` → 正常拿到 20 字节。
    ⇒ 凡"靠子进程输出做判断"的地方都会**静默拿到空串**:
      · 研报采集器的 `Collected:/Processed:` 被解析成 0 —— 采集器自己用
        `collector_crash_log` tee 了同一份日志,所以现象被掩盖了很久;
      · 情绪采集器的 `COLLECT_RUN: {...}` 结论会整段丢失(平台"需重新登录"等诊断全没了)。
    ⇒ 统一走本函数。落盘文件 `logs/child_stdout_<tag>.log` 同时是取证材料
      (含命令行 + cwd,且子进程崩溃前已打印的内容也一定在)。
    """
    os.makedirs(RESEARCH_LOG_DIR, exist_ok=True)
    path = os.path.join(RESEARCH_LOG_DIR, "child_stdout_%s.log" % tag)
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("cmd: %s\ncwd: %s\n%s\n" % (" ".join(map(str, cmd)), cwd, _CHILD_MARK))
        fh.flush()  # 【必须】先落自己的头,再让子进程往同一个 fd 追加
        rc = subprocess.run(
            cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout,
        ).returncode
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    body = text.split(_CHILD_MARK, 1)[1] if _CHILD_MARK in text else text
    return _ChildResult(body, "", rc)


def _alert_excerpt(output: str, head: int = 1500, tail: int = 800) -> str:
    """告警正文摘录:保留头部(致命错误类型/崩溃线程栈)+ 尾部(异常消息),中间折叠。

    【为什么不沿用 output[-500:]】旧写法恰好把 `Fatal Python error:` 头切掉,只剩
    中间几帧调用栈 —— 拿到告警也判不出"原生崩溃"还是"普通异常"(2026-09-17 实测)。
    """
    text = output or ""
    if len(text) <= head + tail:
        return text or "Unknown error"
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n"
        f"... [中间省略 {omitted} 字符;完整输出见 logs/research_collect_*.log] ...\n\n"
        f"{text[-tail:]}"
    )


def _safe_alert(db, *args, **kwargs):
    """写告警但绝不抛错(2026-09-07 防御加固)。

    【功能】包装 db.create_alert:告警写入失败(如 DB 异常)只打日志,不向上传播。
            调度任务的"started"告警在 try 之外调用,若 create_alert 抛错会把整个
            采集任务炸掉 —— 历史上 create_alert 的 lastrowid bug 曾让全部采集任务
            在第一步静默死亡(告警表恒空、子进程从未启动),本函数杜绝该类事故。
    """
    try:
        _write_alert = db.create_alert  # 【变量】取出底层写入函数(经局部变量调用,避免本文件直接散布裸调用)
        return _write_alert(*args, **kwargs)
    except Exception:  # 告警失败不影响采集主流程(suppress 但留日志痕迹)
        import logging

        logging.getLogger(__name__).warning("create_alert failed (non-fatal)", exc_info=True)


def _maybe_alert_degraded(db, platform: str, degradations: list, *, dedup_hours: int = 20) -> None:
    """采集"能用但已降级"时写一条告警(带时间窗去重, 绝不抛错)。

    【功能】把平台适配器自检到的降级(如微博登录态失效 → 每关键词只能取到第 1 页)
            变成一条**用户可见**的告警。注意采集本身是成功的, 所以 severity 用
            warning 而不是 error —— 报成 error 会与"确实采到了 N 条"自相矛盾。
    【参数】
        db:            数据库句柄(需具备 create_alert / get_latest_alert)。
        platform:      平台名, 同时用作去重键的一部分(见下)。
        degradations:  运行报告(COLLECT_RUN)里的 degradations 列表。
        dedup_hours:   去重窗口(小时), 默认 20h。
    【返回】None(任何异常都吞掉: 降级告警只是锦上添花, 不能影响采集主流程)
    【关键逻辑】
    ⚠️ **必须去重**: 降级是**持续状态** —— 微博登录态失效会一直失效到人工重新扫码
       为止。若每轮都写一条(每天 2 轮), 告警表会被同一件事刷满, 真正的偶发故障
       反而被淹没 —— 这正是"告警疲劳"造成静默失效长期无人发现的同一个机制。
       窗口取 20h ≈ 覆盖每天两轮, 保证每天至少被看到一次, 又不重复刷屏。
    ⚠️ **去重键必须含平台**(故用 f"collection_degraded_{platform}" 作为 alert_type):
       否则两个平台同时降级时, 后一个会被前一个的告警抑制掉 —— 那只是把静默
       从一个平台搬到另一个平台。
    """
    try:
        items = [d for d in (degradations or []) if isinstance(d, dict)]
        if not items:
            return
        alert_type = f"collection_degraded_{platform}"
        with suppress(Exception):
            if db.get_latest_alert(alert_type, hours=dedup_hours):
                return  # 窗口内已就同一平台的降级告警过 → 抑制, 避免刷屏
        codes = "/".join(sorted({str(d.get("code") or "") for d in items}))
        lines = []
        for d in items[:4]:
            n = int(d.get("count") or 1)
            lines.append(f"· {d.get('message', '')}" + (f"（共 {n} 次）" if n > 1 else ""))
            if d.get("impact"):
                lines.append(f"  影响: {d['impact']}")
            if d.get("fix_hint"):
                lines.append(f"  处理: {d['fix_hint']}")
        _safe_alert(
            db,
            alert_type,
            f"{platform} 采集降级（跑成了, 但少采）: {codes}",
            "\n".join(lines)[:1500],
            severity="warning",
        )
    except Exception:  # noqa: BLE001 - 降级告警失败绝不能影响采集主流程
        logging.getLogger(__name__).warning("degraded alert failed (non-fatal)", exc_info=True)


# ──────────────────────────────────────────────────────────────────────────
# 按平台采集档位 (2026-09-15)
# ──────────────────────────────────────────────────────────────────────────
# 【为什么需要分档】原先六个平台共用一套参数: per_kw=15 + 一律 --turbo。
# 实测各平台的"单关键词真实产出上限"与"风控耐受度"差别极大,一刀切两头不讨好:
#   - 小红书: 一次搜索就返回 35 条 → per_kw=15 等于白扔一半以上 (实测 5 词/113 条);
#   - 微博:   单页 8 条(其中 6 条藏在 card_type=11 的 card_group 内),
#             登录有效时可翻页到 30 条;per_kw=15 拿不满;
#   - 雪球/东财/知乎: 单关键词 20~36 条,per_kw=15 同样偏紧;
#   - 抖音:   对请求频率极敏感。软限流(HTTP 200 + status_code:0 + data:[])触发后
#             **冷却 45 分钟不恢复**,而旧配置恰好给它最激进的 --turbo 档,
#             等于主动去撞限流 → 只能用 safe 档 + 减关键词降载。
# 档位含义: (per_kw, mode, max_keywords)
#   mode: "turbo"(120~500ms) / "fast"(300ms 起,默认) / "safe"(750ms 起)
#   max_keywords: None=用完整预设列表;数字=只取前 N 个(降载)
PLATFORM_COLLECT_PROFILES: dict[str, tuple[int, str, int | None]] = {
    "xhs": (30, "fast", None),             # 单页 35 条,给足
    "weibo": (30, "fast", None),           # 登录有效可翻页;失效时只有第 1 页 8 条
    # 【2026-09-17 xueqiu 改 fast→safe, 并限 12 词/轮】实测雪球的挂死阈值**随请求数走**:
    #   fast 节奏(约 22 个请求)跑到第 12 个关键词就挂死; safe 节奏能撑到第 21 个。
    #   它的 23 个关键词一轮跑不完 —— 所以不是"硬撑跑完", 而是 12 词/轮 + 关键词轮转
    #   (--rotate-keywords): 每天两轮各跑不同切片, 约一天覆盖全部关键词, 每轮请求数
    #   稳稳落在阈值以内。若不轮转, 排在后面的关键词会被**系统性饿死**(永远采不到)。
    "xueqiu": (30, "safe", 12),
    "zhihu": (25, "fast", None),
    "eastmoney_guba": (30, "fast", None),
    "douyin": (50, "safe", 13),            # 风控敏感:慢档 + 13 词/轮。
                                           # 【2026-09-24 全量试采 → 回 13 词/轮】25 词全量实测:
                                           #   ① 需 ~37min, 超预算 30min 被截断(最后 5 词跑不到);
                                           #   ② 2 词被 rate_limit(比 13 词/轮的 1 词翻倍)。
                                           # ⇒ 回 13 词/轮 + rotate(游标推进), 并新增 **06:00 早轮**
                                           #   (douyin_morning job): 06:00 跑游标前半区(0-12),
                                           #   08:00 统一管道跑后半区(13-24) —— 2 小时间隔 > 45min
                                           #   冷却期, 早间 8 点前全部词各采一遍, 两轮互不传染限流。
}
DEFAULT_COLLECT_PROFILE: tuple[int, str, int | None] = (20, "fast", None)

# ── 采集并发度 (2026-09-17) ──────────────────────────────────────────────
# 【为什么改并行】六个平台原先是**串行**跑的, 一轮墙钟 = 各平台耗时之和: 实测抖音
#   单独就吃满 12min 预算、雪球 safe 档更长, 于是排在后面的平台(zhihu/股吧/xhs/
#   douyin)被整体拖到很晚才开始, 前面的平台一超时就直接吃掉后面的时间。
# 【为什么默认 3 而不是 6】每个平台子进程内部会各起一个 **Chromium**
#   (抖音/知乎/雪球/小红书都是 Playwright): 6 个并发 ≈ 6 个浏览器实例同时开,
#   内存与 CPU 打满反而让每个平台都变慢甚至超时。3 是"墙钟近似减半"与
#   "资源不炸"之间的折中, 可用环境变量 AGENTSENSE_COLLECT_CONCURRENCY 覆盖。
# 【唯一真源】默认值与解析规则都住在 collect_pool, 此处只是转发 —— 定时管道与网页
#   "一键更新"两条链路必须共用同一把尺子, 否则会出现"网页点一下 6 并发、定时只 3 并发"。
DEFAULT_COLLECT_CONCURRENCY = collect_pool.DEFAULT_CONCURRENCY


def _collect_concurrency(platform_count: int) -> int:
    """解析采集并发度 —— 委托 collect_pool, 与网页"一键更新"链路共用同一规则。

    【关键逻辑】绝不因为一个手滑的环境变量就退化成"0 并发"(什么也不跑)或
    "99 并发"(把机器打爆): 非整数/小于 1 → 回退默认 3, 大于硬上限 6 → 钳到 6,
    最后再被平台数兜一层。规则细节与其单测都在 collect_pool.resolve_concurrency。
    """
    return collect_pool.resolve_concurrency(platform_count)


def _run_platform_collection(
    platform: str,
    per_kw: int = 15,
    since_days: int = 7,
    mode: str = "fast",
    max_keywords: int | None = None,
    keywords_count: int = 30,
):
    """运行单个平台的采集任务并记录日志。

    【功能】调用 batch_collect.py 子进程采集指定平台(如 weibo)的帖子,
            把任务开始/结束、条数、失败原因写入数据库并视情况生成告警。
    【参数】
        platform : 平台名,如 "weibo"/"zhihu"。
        per_kw   : 每个关键词采集条数上限,默认 15。
        since_days: 采集回溯天数,默认 7 天(只采近 7 天内容)。
        mode     : 采集节奏档位 —— "turbo"(120~500ms) / "fast"(默认) / "safe"(750ms 起)。
        max_keywords: 只取预设关键词列表前 N 个(降载用);None=不限制。
        keywords_count: 仅用于采集日志里记"计划关键词数",与 max_keywords 保持一致。
    【返回】无。
    【关键逻辑】
            - 先 start_collection 落一条 running 日志,拿到 log_id。
            - 用子进程跑 batch_collect.py(带 --no-detail 提速)。
            - 从子进程输出里解析 "Total notes:" 得到采集条数。
            - 返回码非 0 且非 "Interrupted" 视为失败,写 error 日志并告警;
              成功但条数 < 10 时告警"低数据量"。
            - 25 分钟超时(subprocess timeout=1500)与异常均会记录并告警。
              【2026-09-18 900 → 1500】采集侧全局预算 MAX_RUN_MINUTES 由 12min 提到 20min,
              硬超时必须同步放大到 20min 之后, 否则预算还没用完就被强杀 —— 而一旦被强杀,
              连 collect_run 报告与退出码都写不出来, 表现为"每轮 error 却查不到报告"。
              1500s 给 20min 预算留 300s: 收尾时那个"已在跑的关键词"最慢约 150s,
              再留 150s 给浏览器关闭与报告落盘。
    """
    db = get_db()  # 【调用函数】取当前线程数据库实例(写采集日志/告警)

    # 【关键 2026-09-15】写日志必须包在 try 里 —— 旧代码把 start_collection 放在 try 之外,
    # 而它带着 lastrowid bug 每次必抛 AttributeError,于是整个函数在第一行就中断,
    # 连 batch_collect.py 子进程都没机会启动:四个平台天天"跑",实际采集 100% 停摆,
    # 而外层仍照常写 pipeline_complete(静默失效的教科书案例)。
    # 日志只是"锦上添花",绝不允许它反过来把采集本身搞崩。
    log_id = None  # 【变量】log_id:采集日志自增 id;写日志失败时保持 None(finish_collection 会安全空转)
    try:
        log_id = db.start_collection(platform, keywords_count=keywords_count)  # 【调用函数】写一条 running 采集日志,返回 log_id 供后续更新
    except Exception as _log_err:  # noqa: BLE001
        _safe_alert(db,
            "collection_log_error",
            f"{platform} start_collection failed",
            str(_log_err)[:300],
            severity="warning",
        )

    try:
        since_date = (
            datetime.now().replace(hour=0, minute=0, second=0)
            - __import__("datetime").timedelta(days=since_days)
        ).strftime("%Y-%m-%d")  # 【变量】since_date:回溯起点日期(YYYY-MM-DD,只采近 N 天)

        # 复用当前 Python 解释器所在的虚拟环境,避免依赖系统 Python
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:当前虚拟环境解释器路径(复用环境,避免依赖系统 Python)
        cmd = [
            venv_py,
            "batch_collect.py",
            "--platform",
            platform,
            "--per-kw",
            str(per_kw),
            "--no-detail",
            "--since",
            since_date,
        ]
        # 节奏档位:只有 turbo / safe 需要显式加旗标,fast 是 batch_collect 默认档。
        # ⚠️ 不要对所有平台一律 --turbo:抖音软限流(HTTP 200 + data:[])对请求频率
        #    极敏感,触发后冷却 45min 不恢复,用最快档等于主动去撞。
        if mode == "turbo":
            cmd.append("--turbo")
        elif mode == "safe":
            cmd.append("--safe-mode")
        if max_keywords:
            # 限词必须**搭配轮转**: 否则每轮都从第 1 个关键词开始, 排在第 N 个之后的
            # 关键词将永远采不到(实测雪球 23 个词里有 11 个长期为零)。
            cmd += ["--max-keywords", str(max_keywords), "--rotate-keywords"]

        result = _capture_run(  # 【调用函数】启动 batch_collect.py 子进程采集指定平台帖子(25 分钟超时)
            cmd,
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            timeout=1500,  # 25 min timeout:必须 > 采集侧全局预算 20min, 否则预算内就被强杀、报告丢失
            tag="batch_collect",  # 【必须】不经 PIPE —— 无控制台父进程下 PIPE 会拿到空串(见 _capture_run)
        )

        # ── 解析子进程结论:优先机器可读的 COLLECT_RUN 报告,退回旧的人话行 ──
        # 【为什么】batch_collect.py 自 2026-09-14 起在 stdout 打一行
        #   `COLLECT_RUN: {json}`,内含 status(ok/empty/auth_failed/blocked)、
        #   notes、diagnosis、fix_hint 与逐关键词明细。只认旧的人话行 "Total notes:"
        #   会丢掉"该平台需重新登录"这类关键结论,把所有失败压成同一条告警。
        output = (result.stdout or "") + (result.stderr or "")  # 【变量】output:子进程输出(合并 stdout+stderr,供解析条数与结论)
        run_info = {}  # 【变量】run_info:COLLECT_RUN 报告(空 dict 表示脚本不支持该协议,走旧解析)
        for line in output.split("\n"):
            if RUN_LINE_PREFIX in line:
                with suppress(Exception):
                    run_info = json.loads(line.split(RUN_LINE_PREFIX, 1)[1].strip())

        posts_count = 0  # 【变量】posts_count:采集条数(默认 0)
        if run_info:
            with suppress(Exception):
                posts_count = int(run_info.get("notes") or 0)
        if not posts_count:  # 兼容旧脚本/旧格式:从人话行兜底解析
            for line in output.split("\n"):
                if "Total notes:" in line:
                    with suppress(Exception):
                        posts_count = int(line.split("Total notes:")[1].strip().split()[0])

        rc = result.returncode  # 【变量】rc:子进程退出码(语义见 EXIT_* 常量)
        run_status = str(run_info.get("status") or "")  # 【变量】run_status:报告里的结论(ok/empty/auth_failed/blocked)
        diagnosis = str(run_info.get("diagnosis") or "")  # 【变量】diagnosis:报告给出的一句话原因
        fix_hint = str(run_info.get("fix_hint") or "")  # 【变量】fix_hint:报告给出的修复建议(直接透传给用户)
        # 【变量】degradations: 适配器自检到的降级证据(如微博登录态失效→只能取第1页)。
        # ⚠️ 这类故障**不会**让 status 变成 auth_failed(第1页照样能采到),
        #    所以必须单独取出来, 否则它就和"一切正常"完全无法区分。
        degradations = run_info.get("degradations") or []
        # 【2026-09-17】把结论**回传**给调用方,供 _run_daily_pipeline 汇总
        # "本轮六个平台成了几个"。否则管道只能在收尾写一句笼统的 "complete",
        # 某平台失败(如 xhs 被 blocked)时用户从告警里完全看不出来。
        # 归一化:报告缺失(旧脚本/解析失败)时按退出码兜底,保证调用方拿到可判等的状态。
        _final = run_status or (
            "auth_failed" if rc == EXIT_AUTH_FAILED
            else "blocked" if rc == EXIT_BLOCKED
            else "ok" if rc == 0
            else "error"
        )

        if "Interrupted" in output:  # 用户主动中断不算失败
            db.finish_collection(log_id, posts_count)  # 【调用函数】更新采集日志为成功
            return {"platform": platform, "status": "interrupted", "notes": posts_count}
        elif rc == EXIT_AUTH_FAILED or run_status == "auth_failed":
            # 凭证失效是"可自助修复"的故障,必须把重新登录的指引透传出去,
            # 否则用户只会看到一条没有行动项的"采集失败"。
            db.finish_collection(log_id, posts_count, error=diagnosis or "auth failed")  # 【调用函数】记失败并留下原因
            _safe_alert(db,
                "collection_auth_failed",
                f"{platform} 登录凭证失效,需重新登录",
                fix_hint or diagnosis or output[-300:] or "Auth failed",
                severity="error",
            )
            return {"platform": platform, "status": "auth_failed", "notes": posts_count,
                    "diagnosis": diagnosis, "fix_hint": fix_hint}
        elif rc == EXIT_BLOCKED or run_status == "blocked":
            db.finish_collection(log_id, posts_count, error=diagnosis or "blocked")  # 【调用函数】记失败并留下原因
            _safe_alert(db,
                "collection_blocked",
                f"{platform} 被平台拦截/接口变更",
                fix_hint or diagnosis or output[-300:] or "Blocked",
                severity="error",
            )
            return {"platform": platform, "status": "blocked", "notes": posts_count,
                    "diagnosis": diagnosis, "fix_hint": fix_hint}
        elif rc != 0:
            db.finish_collection(log_id, posts_count, error=output[-500:])  # 【调用函数】更新采集日志为失败(error 取输出末尾 500 字符)
            _safe_alert(db,
                "collection_failed",  # 【调用函数】写入"采集失败"告警
                f"{platform} collection failed",
                output[-300:] or "Unknown error",
                severity="error",
            )
            return {"platform": platform, "status": "error", "notes": posts_count,
                    "diagnosis": output[-400:]}
        else:
            db.finish_collection(log_id, posts_count)  # 【调用函数】更新采集日志为成功

            # 降级告警(2026-09-18): 采集成功但有降级(如微博游客态只能取到第 1 页)时,
            # 此前 status=ok / auth_failures=0, 页面上完全看不出异常。补一条 warning
            # 告警让用户能发现; 内部按平台 + 20h 窗口去重, 不会每轮刷屏。
            _maybe_alert_degraded(db, platform, degradations)

            # Check if posts count is too low
            if posts_count < 10:
                _safe_alert(db,
                    "low_data",  # 【调用函数】写入"低数据量"告警(条数 < 10)
                    f"{platform} low posts",
                    f"Only {posts_count} posts collected",
                    severity="warning",
                )
            # 注意 _final 可能是 "empty"(搜索成功但平台确实没内容,退出码仍是 0)
            # —— 它不属于故障,但确实 0 条,汇总时要能与 "ok" 区分开。
            return {"platform": platform, "status": _final, "notes": posts_count,
                    "diagnosis": diagnosis}

    except subprocess.TimeoutExpired:
        db.finish_collection(log_id, 0, error="Timeout (15 min)")  # 【调用函数】超时:记 0 条并把日志标记为超时
        _safe_alert(db,
            "collection_timeout",  # 【调用函数】写入"采集超时"告警
            f"{platform} timeout",
            "Collection exceeded 15 minutes",
            severity="error",
        )
        return {"platform": platform, "status": "timeout", "notes": 0,
                "diagnosis": "Timeout (15 min)"}
    except Exception as e:
        db.finish_collection(log_id, 0, error=str(e)[:500])  # 【调用函数】异常:记 0 条并写错误日志
        _safe_alert(db,"collection_error", f"{platform} error", str(e)[:300], severity="error")  # 【调用函数】写入"采集异常"告警
        return {"platform": platform, "status": "error", "notes": 0, "diagnosis": str(e)[:300]}


def _run_daily_pipeline():
    """每日全量处理管道:多平台采集 → 聚合 → 回测 → 重新生成情感 JSON。

    【功能】每天定时执行的主流程:依次采集微博/雪球/知乎/东财股吧,再跑
            情感聚合、权重回测与情感 JSON 文件重生成,最后写入完成/失败告警。
    【参数】无。
    【返回】无。
    【关键逻辑】
            - 开始前写 pipeline_started 告警。
            - 4 平台顺序采集,单平台失败(凭据缺失/超时)跳过不影响其他平台。
            - 聚合+回测+生成 通过一段内嵌 Python 脚本(-c)在 THINK2_DIR 子进程
              中执行,依次调用 trend_aggregator.aggregate、backtest_weights.run_all、
              generate_tradingagents_sentiment 生成各品种情感 JSON。
            - 整体 5 分钟超时(300 秒);异常只记告警,不影响调度器继续运行。
    """
    db = get_db()  # 【调用函数】取数据库实例(写管道事件告警)
    _pipeline_started = datetime.now()  # 【变量】本轮起点(留痕用: 收尾时刻去匹配轮次起点会漂出窗口)
    _safe_alert(
        db,
        "pipeline_started",  # 【调用函数】写入"管道启动"信息告警(失败不阻塞管道)
        "Daily pipeline started",
        f"Automated pipeline at {datetime.now():%H:%M}",
        severity="info",
    )

    # 多平台采集: 微博→雪球→知乎→东财股吧→小红书→抖音 顺序执行。
    # 单平台失败(如凭据缺失/超时)由 _run_platform_collection 内部兜底记录,
    # 外层再包一层 try/except 保证某个平台抛错不会拖垮整条管道。
    # Collect from all platforms sequentially; per-platform failure skips to next
    # 【2026-09-15 调整两处】
    # ① 平台列表补入 xhs / douyin。此前调度器只跑 4 个平台,这两个渠道在"配置上
    #   就不在计划内",于是长期无人察觉它们根本没被采过。补入后其失败会走正常
    #   告警链路(依赖缺失→退出码 5、登录失效→退出码 3),不再静默。
    # ② since_days 7 → 30。该过滤在**采集之后**执行(见 batch_collect.py 的
    #   "Time filter (since_date)"),只决定"已抓到的条目留下多少",**不增加采集耗时**。
    #   而各平台搜索结果新旧混排:雪球是唯一真·实时源(7 天存活 177/177),
    #   微博 3/43、抖音 0、小红书 0 —— 统一砍 7 天等于结构性只放过雪球。放宽到
    #   30 天让其余渠道的存量帖子能进来,重复条目由下游 note_id 去重兜住。
    # ③ 参数按平台分档(PLATFORM_COLLECT_PROFILES),不再一律 per_kw=15 + --turbo。
    #   旧配置同时造成"小红书被限到 15 条"和"抖音被加速去撞软限流"两个反向错误。
    # 【2026-09-17】收集每个平台的结论,供收尾汇总。此前采集结果被直接丢弃,
    # 管道只能在结尾写一句笼统的 "Daily pipeline complete" —— 即使 6 个平台里有
    # 平台被 blocked / 凭证失效,用户在告警里也完全看不出来。
    _platforms = ("weibo", "xueqiu", "zhihu", "eastmoney_guba", "xhs", "douyin")  # 【变量】本轮采集平台(固定顺序, 用于展示/汇总)
    _workers = _collect_concurrency(len(_platforms))  # 【变量】并发度(环境变量可覆盖, 默认 3)

    def _collect_one(_p: str) -> dict:
        """采集单个平台并回传结论(在工作线程里执行)。

        【关键逻辑】db 必须在**本线程内**取: get_db() 是 threading.local 的单例,
        SQLite 连接不允许跨线程使用 —— 若沿用主线程的连接会直接抛
        ProgrammingError, 把"并行"变成"六个平台一起失败"。
        """
        _per_kw, _mode, _max_kw = PLATFORM_COLLECT_PROFILES.get(_p, DEFAULT_COLLECT_PROFILE)
        try:
            return _run_platform_collection(  # 【调用函数】采集单平台并回传结论
                _p,
                per_kw=_per_kw,
                since_days=30,
                mode=_mode,
                max_keywords=_max_kw,
                keywords_count=_max_kw or 30,
            ) or {"platform": _p, "status": "unknown", "notes": 0}
        except Exception as _e:
            _safe_alert(get_db(),  # 【调用函数】写入"采集异常"告警(外层兜底, 用本线程的连接)
                "collection_error",
                f"{_p} pipeline error",
                str(_e)[:300],
                severity="error",
            )
            return {"platform": _p, "status": "error", "notes": 0, "diagnosis": str(_e)[:200]}

    # 【并行调度】有界线程池跑六个平台(顺序/隔离/并发度规则见 collect_pool):
    #   完成顺序不确定, 但返回结果已按 _platforms 固定顺序排好, 告警文案才可比对。
    results: dict[str, dict]  # 【变量】results:平台名 → 本轮采集结论(status/notes/diagnosis)
    results, _collect_errors = collect_pool.run_platforms(
        _platforms, _collect_one, concurrency=_workers
    )
    for _p, _msg in _collect_errors.items():  # 线程内意外抛错也不能让汇总缺项
        results[_p] = {"platform": _p, "status": "error", "notes": 0, "diagnosis": _msg}
    # 按固定顺序重排(并行完成顺序不确定), 保证告警与前端展示顺序稳定
    results = {_p: results[_p] for _p in _platforms if _p in results}

    # 本轮采集小结:ok 与其他状态分开列,一眼看出"是不是又有平台挂了"
    _ok = [p for p, r in results.items() if r.get("status") == "ok"]  # 【变量】_ok:采集正常的平台
    _bad = {p: r.get("status") for p, r in results.items() if r.get("status") != "ok"}  # 【变量】_bad:非 ok 平台及状态
    collect_summary = f"采集 {len(_ok)}/{len(results)} 平台正常"  # 【变量】collect_summary:采集小结文本
    if _bad:
        collect_summary += " | 异常: " + ", ".join(f"{_p}={_s}" for _p, _s in sorted(_bad.items()))

    # 【轮次留痕 2026-09-17】给本轮打一个"定时采集"标记,供前端「各平台增量」表标注来源。
    # 【为什么需要】增量数字本身由 update_tracker 重放批次文件得出(不依赖埋点),
    #   但"这次是定时跑的还是人点的"只能靠留痕 —— 纯按时间推断会在调度延迟启动
    #   (如 18:07 才开始)时判成"手动", 把自动轮误报成人为操作。
    # 【失败不影响调度】留痕是锦上添花, 任何异常只记 debug 日志。
    try:
        _out_dir = THINK2_DIR / "output" if THINK2_DIR else None
        if _out_dir and _out_dir.exists():
            append_recorded_round(_out_dir, {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "started_ts": _pipeline_started.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "scheduled",
                "platforms": list(results.keys()),
            })
    except Exception as _rec_err:  # noqa: BLE001
        logger.debug("record update round failed: %s", _rec_err)

    # Aggregate + backtest + regenerate
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:虚拟环境解释器路径(用于执行内嵌脚本)
        script = """
import json, sys, glob
from pathlib import Path
OUTPUT = Path('.') / 'output'
TRENDS = OUTPUT / 'trends'
sys.path.insert(0, '.')
from trend_aggregator import aggregate
paths = sorted(glob.glob(str(OUTPUT / 'batch_*.jsonl')))
result = aggregate(paths)
from backtest_weights import run_all
result_b = run_all(min_points=10, horizons=[1, 3, 5])
from generate_tradingagents_sentiment import load_trends_data, generate_sentiment_json, OUTPUT_DIR as GEN_OUTPUT
varieties, index, global_weights = load_trends_data(TRENDS)
GEN_OUTPUT.mkdir(parents=True, exist_ok=True)
gen_count = 0
for vname in sorted(varieties.keys()):
    output = generate_sentiment_json(vname, varieties[vname], index, global_weights)
    if output is None or output['data']['social_sentiment']['total_posts_analyzed'] < 2: continue
    with open(GEN_OUTPUT / f'{output[\"variety\"]}_sentiment.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    gen_count += 1
print(f'Pipeline OK: {gen_count} JSONs')
        """
        # 【2026-09-17】必须检查 returncode。此前 subprocess.run 的返回值**没有赋值**,
        # 结果被直接丢弃 —— 聚合/回测/生成脚本哪怕报错,也照样写 pipeline_complete。
        # 这与 start_collection 的 lastrowid 坑同属"静默失效":告警说成功、产物却没更新。
        proc = _capture_run(  # 【调用函数】在数据目录子进程执行聚合+回测+情感 JSON 生成脚本(5 分钟超时)
            [venv_py, "-c", script],
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            timeout=300,
            tag="pipeline_aggregate",
        )
        if proc.returncode != 0:
            _safe_alert(db,
                "pipeline_error",  # 【调用函数】写入"管道失败"告警
                "Pipeline aggregate step failed",
                f"exit={proc.returncode} | {collect_summary} | "
                f"{((proc.stderr or '').strip() or (proc.stdout or '').strip())[-500:]}",
                severity="error",
            )
        else:
            # 取嵌入脚本的最后一行(如 "Pipeline OK: 47 JSONs")作为"产物确实生成了"的凭据
            _tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]  # 【变量】_tail:子进程输出中的非空行
            _artifact = _tail[-1].strip() if _tail else ""  # 【变量】_artifact:产物摘要(生成的 JSON 数量)
            _safe_alert(db,
                "pipeline_complete",  # 【调用函数】写入"管道完成"信息告警
                "Daily pipeline complete",
                f"{collect_summary} | {_artifact} | at {datetime.now():%H:%M}",
                severity="info",
            )
    except subprocess.TimeoutExpired:
        _safe_alert(db, "pipeline_error", "Pipeline aggregate step timeout",
                    f"exceeded 5 min | {collect_summary}", severity="error")
    except Exception as e:
        _safe_alert(db,"pipeline_error", "Pipeline failed", f"{collect_summary} | {str(e)[:300]}", severity="error")  # 【调用函数】写入"管道失败"告警


def _run_douyin_round():
    """【2026-09-24】抖音"半区轮": 与统一管道(13 词/轮 + rotate 游标)互补覆盖全部词。

    【为什么】抖音 13 词/轮 + rotate 持久游标: 每轮跑游标推进处的 13 个词, 相邻两轮
    合计覆盖词池全部 25 词。早晚各注册两轮(06:00/08:00 与 18:00/20:00, 间隔 2h >
    抖音 45min 限流冷却期, 互不传染限流) ⇒ 每天 4 轮 × 13 词 = 52 词次, 全词池采 2 遍,
    早间 8 点前、晚间 8 点后都各有完整一轮数据。
    【参数】与 PLATFORM_COLLECT_PROFILES["douyin"] 一致(per_kw=50/safe/13 词),
    共享 output/.rotation_douyin.json 游标文件 ⇒ 相邻轮天然互补, 无需额外状态。
    【注意】只跑抖音, 不拖累其他 5 平台; 若浏览器会话失效会走 auth_fail 告警
    (有兜底, 后续轮独立照跑, 互不影响)。
    """
    _run_platform_collection(
        "douyin",
        per_kw=50,
        since_days=30,
        mode="safe",
        max_keywords=13,
        keywords_count=13,
    )


def _run_htfc_collection():
    """每日开盘前接入华泰官方天玑研报:子进程跑 research_collector_htfc.py 并记录告警。

    【功能】每日定时跑华泰期货官方天玑采集器
            (research_collector_htfc.py,21 品种池日报)。单独函数 + 单独
            job id(research_htfc_{time}),告警前缀 htfc_*,
            在 /api/scheduler/status 与告警中心里与国君/东证区分。
            (2026-09-09 发现报告源剔除后,_run_htfc_collection 是最早的研报接入点。)
    【参数】无。
    【返回】无。
    【关键逻辑】
            - 同一虚拟环境子进程,同一 cwd(AgentSense 根),无 env= 继承 os.environ。
            - 90 分钟超时(timeout=5400):单日 ≤21 篇 × LLM ~110s + 详情拉取,
              兜底放得更宽,避免首日/回补日被误杀。
            - 从 stdout 解析 "Collected: N" / "Processed: M"。
    """
    db = get_db()  # 【调用函数】取数据库实例(写采集日志/告警)
    _safe_alert(db,  # 【调用函数】写入"天玑研报接入启动"信息告警
        "htfc_started",
        "HTFC research collection started",
        f"Automated HTFC research ingest at {datetime.now():%H:%M}",
        severity="info",
    )
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:当前虚拟环境解释器路径
        script_dir = os.path.dirname(os.path.abspath(__file__))  # 【变量】script_dir:AgentSense 根(research_collector_htfc.py 同目录)
        cmd = [venv_py, "-u", "research_collector_htfc.py"]
        result = _capture_run(  # 【调用函数】启动天玑研报接入子进程(90 分钟超时)
            cmd,
            cwd=script_dir,
            env=_collector_env(),  # 【2026-09-14】不继承 AGENTSENSE_WEB_SERVER 标记(见 _collector_env)
            timeout=5400,  # 90 min timeout:接入含 LLM 提取,单日 21 篇约 40min,回补更久;超时视为失败
            tag="htfc",  # 【必须】不经 PIPE,否则 Collected/Processed 会被解析成 0(见 _capture_run)
        )
        output = (result.stdout or "") + (result.stderr or "")  # 【变量】output:子进程输出(合并 stdout+stderr)
        # 【2026-09-17 取证】完整输出落盘:告警只摘头尾,中间栈仍可从日志逐字复现。
        # 另加 -u 强制子进程无缓冲 —— 致命崩溃时 Python 的块缓冲 stdout 会整段丢失,
        # 只剩 stderr 上的 faulthandler 转储(实测 09-17 的告警就只剩半截栈)。
        _log_file = _persist_collector_output("htfc", output, result.returncode)
        collected = processed = 0  # 【变量】解析出的接入统计(默认 0)
        for line in output.splitlines():
            if line.startswith("Collected:"):
                with suppress(Exception):
                    collected = int(line.split(":", 1)[1].strip())
            elif line.startswith("Processed:"):
                with suppress(Exception):
                    processed = int(line.split(":", 1)[1].strip())

        if result.returncode != 0:
            _safe_alert(db,  # 【调用函数】写入"天玑研报接入失败"告警
                "htfc_error",
                "HTFC research collection failed",
                f"[{_explain_returncode(result.returncode)}] log={_log_file}\n{_alert_excerpt(output)}",
                severity="error",
            )
        else:
            _safe_alert(db,  # 【调用函数】写入"天玑研报接入完成"信息告警
                "htfc_complete",
                "HTFC research collection complete",
                f"Collected {collected}, processed {processed} reports",
                severity="info",
            )
    except subprocess.TimeoutExpired:
        _safe_alert(db,  # 【调用函数】写入"天玑研报接入超时"告警
            "htfc_timeout",
            "HTFC research collection timeout",
            "HTFC research ingest exceeded 90 minutes",
            severity="error",
        )
    except Exception as e:
        _safe_alert(db,  # 【调用函数】写入"天玑研报接入异常"告警
            "htfc_error",
            "HTFC research collection error",
            str(e)[:300],
            severity="error",
        )


def _run_rag_index():
    """每日把新入库研报补进 RAG 向量索引(增量、幂等,本地 embedding 零 LLM)。

    【为什么需要】单篇研报入库时会即时索引,但 embedding 模型缺失/损坏期间入库的
            研报会静默漏索引(2026-09-09 真机:模型没装,09-07~09-09 的新研报在
            研报问答里完全检索不到,模型把"最新"误判成 9 月 6 日)。本任务每日
            兜底补齐,是"采集 → 入库 → 可问答"链路的最后一环。
    【参数】无。
    【返回】无。
    【关键逻辑】复用 web_app._rag_index_pending(同进程内调用,避免再起子进程);
              只扫近 3 天入库的 done 研报(增量,秒级~分钟级);结果写 info 告警,
              失败写 error 告警且不抛异常(不影响后续任务)。
    """
    db = get_db()  # 【调用函数】取数据库实例(写告警)
    try:
        from web_app import _rag_index_pending  # 【调用包】增量索引(懒导入避免启动变慢)

        stats = _rag_index_pending(since_days=3)
        if stats.get("error"):
            _safe_alert(db,  # 【调用函数】写入"RAG 未就绪"告警
                "rag_index_skipped", "RAG index skipped",
                str(stats["error"])[:200], severity="warning",
            )
            return
        _safe_alert(db,  # 【调用函数】写入"RAG 入库完成"信息告警
            "rag_index_complete", "RAG index updated",
            f"新索引 {stats['done']} / 跳过 {stats['skipped']} / 失败 {stats['failed']}"
            f"(索引共 {stats['chunks']} 片段 / {stats['reports']} 份)",
            severity="info",
        )
    except Exception as e:
        logger.warning("RAG index job failed: %s", e)
        _safe_alert(db,  # 【调用函数】写入"RAG 入库异常"告警
            "rag_index_error", "RAG index failed", str(e)[:300], severity="error",
        )


def _run_daily_summary():
    """每日研报采集完成后自动生成当天日报总结(2026-10-12 需求)。

    【功能】在研报接入错峰链(RAG 索引)之后运行:先等当天 pending/processing 研报
            处理完(轮询,最多 10 分钟),再调 web_app._generate_daily_summary(today)
            生成并落盘 research_daily/{today}.md;已生成过则复用(force=False 幂等)。
    【为什么放采集链末尾】采集器(htfc/gtja/dz)只是把研报**入队**异步处理,
            日报必须等当天全部研报 status=done 才有完整数据;RAG 索引(+75min)
            兜底补向量后,本任务(+95min)再收尾生成。
    【多轮兜底】research_times 每天 08:10/18:00/22:00 三轮:早轮研报未处理完
            会跳过(等满 10 分钟仍 pending ⇒ 记 warning,等晚轮重试);
            已生成过的日期幂等复用,不重复烧 LLM。
    【参数】无。【返回】无。
    """
    db = get_db()
    try:
        from datetime import date, datetime, timedelta  # 【调用包】日期计算(懒导入)
        from time import sleep  # 【调用包】轮询等待
        from web_app import _generate_daily_summary  # 【调用包】日报总结生成(懒导入)

        today = date.today().isoformat()
        # ① 等当天研报处理完:每 30s 检查一次,最多 10 分钟(采集器入队是异步的,
        #    LLM 提取/观点/RAG 索引都在子线程跑,日报必须等 status=done 才完整)。
        waited = False
        deadline = datetime.now() + timedelta(minutes=10)
        while datetime.now() < deadline:
            rows = db.list_research_reports(limit=500)  # 【调用函数】最近 500 条(当天几十条在内)
            pending = [r for r in rows
                       if (r.get("publish_date") or "")[:10] == today
                       and r.get("status") in ("pending", "processing")]
            if not pending:
                waited = True
                break
            sleep(30)
        if not waited:
            # 【超时】研报还没处理完(可能 LLM 慢/排队):不生成残缺日报,等晚轮重试
            _safe_alert(db,  # 【调用函数】写入"日报生成推迟"告警
                "daily_summary_deferred", "Daily summary deferred",
                f"{today}: 仍有 {len(pending)} 篇研报在处理中(等下一轮重试)",
                severity="warning",
            )
            return
        # ② 生成日报(幂等;当天无日报研报时 ok=False → 记 warning 跳过)
        result = _generate_daily_summary(today, force=False, rtype="")
        if not result.get("ok"):
            _safe_alert(db,  # 【调用函数】写入"日报未生成"告警(无研报/失败)
                "daily_summary_skipped", "Daily summary skipped",
                f"{today}: {result.get('error', '未知原因')}", severity="warning",
            )
            return
        _safe_alert(db,  # 【调用函数】写入"日报生成完成"信息告警
            "daily_summary_complete", "Daily research summary generated",
            f"{today} 日报总结{'已生成' if not result.get('cached') else '已存在(复用)'},"
            f" 覆盖 {result.get('reports', 0)} 条研报结论",
            severity="info",
        )
    except Exception as e:
        logger.warning("Daily summary job failed: %s", e)
        _safe_alert(db, "daily_summary_error", "Daily summary failed",
                    str(e)[:300], severity="error")


def _run_weekly_summary():
    """每周一早上自动生成「上一周」研报周报总结(2026-09-11 需求三)。

    【功能】计算上一周的周起点(周日,即本周一往前推 7 天),调用
            web_app._generate_daily_summary(rtype='周报') 生成并落盘
            research_weekly/{周起点}.md;已生成过则复用(force=False 幂等)。
    【为什么周一】周区间 = 周日~周六,周一早上上周完整结束,生成时机最自然;
            用户确认「周一早生成」(2026-09-11)。
    【参数】无。【返回】无。
    【关键逻辑】同进程调用 web_app 函数(与 _run_rag_index 同模式),失败只告警不抛。
    """
    from datetime import date, timedelta

    db = get_db()
    try:
        today = date.today()
        # 上一周周起点 = 本周一(周一的 weekday=0)往前推 7 天 → 上周日
        last_monday = today - timedelta(days=today.weekday())
        week_start = (last_monday - timedelta(days=7)).isoformat()
        from web_app import _generate_daily_summary  # 【调用包】周报总结生成(懒导入)

        result = _generate_daily_summary(week_start, force=False, rtype="周报")
        if not result.get("ok"):
            _safe_alert(db,  # 【调用函数】写入"周报总结未生成"告警(无周报/失败)
                "weekly_summary_skipped", "Weekly summary skipped",
                f"{week_start}: {result.get('error', '未知原因')}", severity="warning",
            )
            return
        _safe_alert(db,  # 【调用函数】写入"周报总结生成完成"信息告警
            "weekly_summary_complete", "Weekly research summary generated",
            f"{week_start} 周报总结{'已生成' if not result.get('cached') else '已存在(复用)'},"
            f" 覆盖 {result.get('reports', 0)} 条研报结论",
            severity="info",
        )
    except Exception as e:
        logger.warning("Weekly summary job failed: %s", e)
        _safe_alert(db, "weekly_summary_error", "Weekly summary failed",
                    str(e)[:300], severity="error")


def _run_summary_archive():
    """每日增量归档超过 14 天的研报(2026-09-28): 总结化 → 进 RAG → 软删原行。

    【为什么】09-24 落地归档方案 B(每日总结裁剪卡片), 但只能手动跑脚本; 每天都有
    新的 >14 天 done 研报, 不自动归档会积压(原文块不清、列表膨胀、检索命中旧文)。
    【口径】research_archive.archive_new(days_back=14):
            ① 缺失每日总结的归档日期烧 LLM 补(方案 B 缺失日统一生成);
            ② build 生成 (日期×品种) 总结(每日总结裁剪 + conclusion 兜底), 幂等;
            ③ 增量进 RAG(只处理当日新建组, 两阶段删原文块防共享 rid 交错);
            ④ 软删原行(done→summarized, 数据全保留)。
    【返回】无(统计写日志; 失败告警不中断其他任务)。
    """
    db = get_db()
    try:
        from research_archive import archive_new  # 【调用包】懒导入(内部会 import web_app, 延迟防循环)

        st = archive_new(days_back=14)
        logger.info(
            "summary archive done: cutoff=%s groups=%s indexed=%s chunks=%s soft_deleted=%s",
            st.get("cutoff"), (st.get("build") or {}).get("groups"),
            (st.get("index") or {}).get("indexed"), (st.get("index") or {}).get("chunks"),
            (st.get("soft_delete") or {}).get("soft_deleted"),
        )
    except Exception as e:  # noqa: BLE001  归档失败不影响其他定时任务
        logger.warning("summary archive failed: %s", e, exc_info=True)
        try:
            _safe_alert(db, "summary_archive_error", "研报归档任务失败",
                        str(e)[:300], severity="warning")
        except Exception:  # noqa: BLE001  告警失败静默
            pass


def _run_research_file_cleanup():
    """每日清理超过 14 天的研报原件文件(2026-09-11 需求三)。

    【为什么需要】研报原件(PDF/附件)累计 700MB+,保留 14 天即删,释放磁盘;
            聚合 JSON(结论)、RAG 向量、数据库行、总结 md 全部保留,历史可查/问答可用。
    【口径】按数据库 uploaded_at 判定研报入库时间,距今天数 > 14 删除原件文件
            (file_path 指向的真实文件);DB 行 file_path 置空标记 file_deleted=1。
    【参数】无。【返回】无。
    """
    from datetime import datetime, timedelta

    db = get_db()
    try:
        from web_app import _delete_research_original_file  # 【调用包】原件清理(懒导入)

        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        rows = [r for r in db.list_research_reports(limit=5000)
                if r.get("status") == "done" and (r.get("uploaded_at") or "")[:19] < cutoff]
        deleted = failed = 0
        for r in rows:
            try:
                if _delete_research_original_file(r):
                    deleted += 1
            except Exception:
                failed += 1
        if deleted or failed:
            _safe_alert(db,  # 【调用函数】写入"研报原件清理"信息告警
                "research_cleanup_complete", "Research files cleaned",
                f"删除 {deleted} 份 >14 天原件, 失败 {failed}; 聚合 JSON/RAG/结论均保留",
                severity="info",
            )
    except Exception as e:
        logger.warning("Research file cleanup job failed: %s", e)
        _safe_alert(db, "research_cleanup_error", "Research cleanup failed",
                    str(e)[:300], severity="error")


def _run_aggregate_repair():
    """每日聚合 JSON 对账自愈(2026-09-11):done 行缺失的聚合条目从 DB 重建。

    【为什么】周报行曾整体漏进聚合(历史缺陷),逐品种观点总览与总结不对齐。
            每日对账兜底,新增缺失立即补齐(零 LLM)。
    """
    db = get_db()
    try:
        from web_app import _repair_research_aggregates  # 【调用包】对账修复(懒导入)

        result = _repair_research_aggregates(dry_run=False)
        if result.get("missing_rows"):
            _safe_alert(db,  # 【调用函数】写入"聚合修复"信息告警
                "aggregate_repair_complete", "Research aggregate repaired",
                f"补 {result['missing_rows']} 行 / {result['repaired_entries']} 条聚合条目",
                severity="info",
            )
    except Exception as e:
        logger.warning("Aggregate repair job failed: %s", e)
        _safe_alert(db, "aggregate_repair_error", "Research aggregate repair failed",
                    str(e)[:300], severity="error")


def _run_gtja_collection():
    """每日接入国泰君安官方研报:子进程跑 research_collector_gtja.py 并记录告警。

    【功能】与 _run_htfc_collection 同模式,跑国君云 API 采集器
            (research_collector_gtja.py,researchReportAttachmentQuery,
            默认 21 目标品种、近 2 天窗口、单次 ≤30 篇)。job id 前缀
            research_gtja_,告警前缀 gtja_*。
    【关键逻辑】子进程 + 90 分钟超时(timeout=5400,与 HTFC 同兜底);从 stdout
              解析 "Collected: N" / "Processed: M";失败/超时写告警。
    """
    db = get_db()  # 【调用函数】取数据库实例(写采集日志/告警)
    _safe_alert(db,  # 【调用函数】写入"国君研报接入启动"信息告警
        "gtja_started",
        "GTJA research collection started",
        f"Automated GTJA research ingest at {datetime.now():%H:%M}",
        severity="info",
    )
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:当前虚拟环境解释器路径
        script_dir = os.path.dirname(os.path.abspath(__file__))  # 【变量】script_dir:AgentSense 根(research_collector_gtja.py 同目录)
        cmd = [venv_py, "-u", "research_collector_gtja.py"]
        result = _capture_run(  # 【调用函数】启动国君研报接入子进程(90 分钟超时)
            cmd,
            cwd=script_dir,
            env=_collector_env(),  # 【2026-09-14】不继承 AGENTSENSE_WEB_SERVER 标记(见 _collector_env)
            timeout=5400,  # 90 min timeout:单次 ≤30 篇 × LLM ~110s,与 HTFC 同兜底
            tag="gtja",  # 【必须】不经 PIPE(见 _capture_run)
        )
        output = (result.stdout or "") + (result.stderr or "")  # 【变量】output:子进程输出(合并 stdout+stderr)
        _log_file = _persist_collector_output("gtja", output, result.returncode)  # 【2026-09-17】完整输出落盘
        collected = processed = 0  # 【变量】解析出的接入统计(默认 0)
        for line in output.splitlines():
            if line.startswith("Collected:"):
                with suppress(Exception):
                    collected = int(line.split(":", 1)[1].strip())
            elif line.startswith("Processed:"):
                with suppress(Exception):
                    processed = int(line.split(":", 1)[1].strip())

        if result.returncode != 0:
            _safe_alert(db,  # 【调用函数】写入"国君研报接入失败"告警
                "gtja_error",
                "GTJA research collection failed",
                f"[{_explain_returncode(result.returncode)}] log={_log_file}\n{_alert_excerpt(output)}",
                severity="error",
            )
        else:
            _safe_alert(db,  # 【调用函数】写入"国君研报接入完成"信息告警
                "gtja_complete",
                "GTJA research collection complete",
                f"Collected {collected}, processed {processed} reports",
                severity="info",
            )
    except subprocess.TimeoutExpired:
        _safe_alert(db,  # 【调用函数】写入"国君研报接入超时"告警
            "gtja_timeout",
            "GTJA research collection timeout",
            "GTJA research ingest exceeded 90 minutes",
            severity="error",
        )
    except Exception as e:
        _safe_alert(db,  # 【调用函数】写入"国君研报接入异常"告警
            "gtja_error",
            "GTJA research collection error",
            str(e)[:300],
            severity="error",
        )


def _run_dongzheng_collection():
    """每日接入东证期货繁微观点:子进程跑 research_collector_dongzheng.py 并记录告警。

    【功能】与 _run_gtja_collection 同模式,跑繁微 Fiona MCP 采集器
            (research_collector_dongzheng.py,viewpoint 端点匿名可调用,
            研报 + 动态快评 3 天滚动窗口)。
            job id 前缀 research_dz_,告警前缀 dz_*。
    【关键逻辑】子进程 + 120 分钟超时(与 GTJA 同兜底);从 stdout 解析
              "Collected: N" / "Processed: M";失败/超时写告警。
    【2026-09-14 修复】原先裸调脚本不带参数 → --dynamics 默认关(3 天实测有
              48 条动态快评从未采集,--days 默认 1 又会漏周末档),用户体感
              "几乎没有东证研报"。现固定 --dynamics --days 3 --max-reports 60,
              超时同步放宽到 120 分钟(60 条 × LLM ~110s / 2 并行 ≈ 55 分钟)。
              seen 去重保证幂等:重复运行不会重复入库。
    """
    db = get_db()  # 【调用函数】取数据库实例(写采集日志/告警)
    _safe_alert(db,  # 【调用函数】写入"东证研报接入启动"信息告警
        "dz_started",
        "Dongzheng(Fiona) research collection started",
        f"Automated Dongzheng viewpoint ingest at {datetime.now():%H:%M}",
        severity="info",
    )
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:当前虚拟环境解释器路径
        script_dir = os.path.dirname(os.path.abspath(__file__))  # 【变量】script_dir:AgentSense 根(research_collector_dongzheng.py 同目录)
        cmd = [  # 【变量】采集命令:--dynamics 采动态快评,--days 3 覆盖周末档
            venv_py,
            "-u",  # 【2026-09-17】强制无缓冲:原生崩溃时块缓冲 stdout 会整段丢失(见 _persist_collector_output)
            "research_collector_dongzheng.py",
            "--dynamics",
            "--days", "3",
            "--max-reports", "60",
        ]
        result = _capture_run(  # 【调用函数】启动东证研报接入子进程(90 分钟超时)
            cmd,
            cwd=script_dir,
            env=_collector_env(),  # 【2026-09-14】不继承 AGENTSENSE_WEB_SERVER 标记(见 _collector_env)
            timeout=7200,  # 120 min timeout:单次 ≤60 条 × LLM ~110s / 2 并行 ≈ 55min,留足下载余量
            tag="dz",  # 【必须】不经 PIPE(见 _capture_run)
        )
        output = (result.stdout or "") + (result.stderr or "")  # 【变量】output:子进程输出(合并 stdout+stderr)
        _log_file = _persist_collector_output("dz", output, result.returncode)  # 【2026-09-17】完整输出落盘
        collected = processed = 0  # 【变量】解析出的接入统计(默认 0)
        for line in output.splitlines():
            if line.startswith("Collected:"):
                with suppress(Exception):
                    collected = int(line.split(":", 1)[1].strip())
            elif line.startswith("Processed:"):
                with suppress(Exception):
                    processed = int(line.split(":", 1)[1].strip())

        if result.returncode != 0:
            _safe_alert(db,  # 【调用函数】写入"东证研报接入失败"告警
                "dz_error",
                "Dongzheng(Fiona) research collection failed",
                f"[{_explain_returncode(result.returncode)}] log={_log_file}\n{_alert_excerpt(output)}",
                severity="error",
            )
        else:
            _safe_alert(db,  # 【调用函数】写入"东证研报接入完成"信息告警
                "dz_complete",
                "Dongzheng(Fiona) research collection complete",
                f"Collected {collected}, processed {processed} items",
                severity="info",
            )
    except subprocess.TimeoutExpired:
        _safe_alert(db,  # 【调用函数】写入"东证研报接入超时"告警
            "dz_timeout",
            "Dongzheng(Fiona) research collection timeout",
            "Dongzheng viewpoint ingest exceeded 90 minutes",
            severity="error",
        )
    except Exception as e:
        _safe_alert(db,  # 【调用函数】写入"东证研报接入异常"告警
            "dz_error",
            "Dongzheng(Fiona) research collection error",
            str(e)[:300],
            severity="error",
        )


def _run_warrant_collection():
    """每日定时采集四家交易所仓单日报并重建「仓单分析」站点数据(2026-09-21 需求)。

    【功能】子进程跑 warrant_collect.py(不带 --date,自动取最近交易日)→ 落盘
            out/*.json 与 web/{overview,detail}.json → 失效 web 进程内的站点缓存
            与历史时序缓存,前端下一次请求即拿到新数据(总览/细则/历史走势全联动)。
    【为什么 08:00 能更新】交易所仓单日报在交易日收盘后(~16:00 起)陆续发布,
            早 8 点采集拿到的是「最近一个已发布交易日」;若前一晚已采过同一天,
            本次为幂等覆盖写,无害。周末/节假日同样空转覆盖,不会产生坏数据。
    【参数】无。【返回】无。
    【关键逻辑】
            - env 用 _collector_env():清掉 AGENTSENSE_WEB_SERVER 标记,避免采集
              子进程连带走 RAG worker 模式(与研报采集子进程同一约定);
            - 完整输出经 _persist_collector_output 落盘 logs/research_collect_warrant_*.log,
              告警正文只留结论,证据看日志(与三源研报采集同一套取证约定);
            - 结论从 out/latest.json 读四所 ok/date/品种数:任一所失败或退出码
              非 0 → error 告警;全部成功 → info 告警;
            - DCE 采集必须真实浏览器(瑞数防护,无头过不了):web 服务跑在用户
              交互会话里,定时触发时允许短暂弹出浏览器窗口,属预期行为。
    """
    db = get_db()
    root = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(root, "warrant_collect.py")
    try:
        proc = _capture_run(
            [sys.executable, script],  # 【关键】复用同一 venv 解释器(采集依赖都在项目 venv)
            cwd=root, timeout=900, env=_collector_env(), tag="warrant",
        )  # 【必须】不经 PIPE(见 _capture_run);结论仍以 latest.json 为准,不解析 stdout
        output = (proc.stdout or "") + (proc.stderr or "")
        _persist_collector_output("warrant", output, proc.returncode)

        # 结论以 latest.json 为准(采集器自己写好的机器可读汇总),不解析 stdout
        summary = {}
        with suppress(Exception):
            with open(os.path.join(root, "data", "warrant", "out", "latest.json"),
                      encoding="utf-8") as fh:
                summary = json.load(fh).get("results") or {}
        bad = {k: (v.get("error") or "未知原因") for k, v in summary.items()
               if not v.get("ok")}
        if proc.returncode != 0 or bad:
            detail = (f"returncode={proc.returncode}"
                      f"({_explain_returncode(proc.returncode)})")
            if bad:
                detail += "; " + "; ".join(f"{k}: {v}" for k, v in bad.items())
            detail += (f" | 日志: logs/research_collect_warrant_"
                       f"{datetime.now():%Y%m%d}.log")
            _safe_alert(db, "warrant_collect_error",
                        "Warrant collection failed", detail[:500], severity="error")
            return
        dates = sorted({v.get("date") for v in summary.values() if v.get("date")})
        rows = " ".join(f"{k} {v.get('product_rows', '?')}品种"
                        for k, v in summary.items())
        _safe_alert(db, "warrant_collect_complete", "Warrant collection updated",
                    f"数据日期 {','.join(dates) or '?'}；{rows}", severity="info")
    except subprocess.TimeoutExpired:
        _safe_alert(db, "warrant_collect_error", "Warrant collection timeout",
                    "warrant_collect.py 超过 900s 未结束,已被终止", severity="error")
    except Exception as e:  # noqa: BLE001 - 定时任务异常只告警,不打断调度线程
        logger.warning("Warrant collection job failed: %s", e)
        _safe_alert(db, "warrant_collect_error", "Warrant collection failed",
                    str(e)[:300], severity="error")
    finally:
        # 【缓存联动】采集落盘后立即失效 web 进程内两处缓存:总览/细则(_WARRANT_CACHE,
        # mtime key)与历史时序(history._CACHE, 文件集 key)。两者的 key 理论上会随
        # 落盘自动变化,这里显式置空是双保险 —— 否则容易出现"采集完成了,页面日期
        # 还停在旧数据"的观感(本任务要解决的核心问题)。
        with suppress(Exception):
            import web_app as _wa  # 【调用包】同进程复用已加载的 web_app(不会重复执行模块级代码)

            _wa._WARRANT_CACHE["key"] = None
        with suppress(Exception):
            from warrant import history as _whist  # 【调用包】仓单历史时序缓存

            _whist._CACHE["key"] = None


# 【变量】仓单采集时间点(由 start_scheduler 写入; 启动补采判据要用它算"今天最近一个已过时点")
_WARRANT_TIMES: list[str] = []


def _warrant_recent_slot(now: datetime | None = None):
    """今天「已过」的最近一个仓单采集时点(无则 None)。供启动补采判定用。"""
    now = now or datetime.now()
    cands = []
    for t in _WARRANT_TIMES:
        try:
            h, m = (int(x) for x in t.split(":"))
        except (ValueError, AttributeError):
            continue
        if (h, m) <= (now.hour, now.minute):
            cands.append((h, m))
    return max(cands) if cands else None


def _warrant_startup_catchup():
    """启动补采:今天的采集时点已过、却还没成功采过 → 立刻补跑一次(2026-09-21 新增)。

    【为什么必须补】定时任务是**进程内**的:服务在 08:00 那一刻没在运行(重启/崩溃/
    守护脚本 5 分钟间隔内), 那个槽位就静默跳过, 看板会整天停在上一个交易日。实测
    2026-09-21 就是这样 —— 服务 09:54 才起来, 08:00 的仓单采集没跑, 用户看到"数据
    日期还是 09-18"以为采集功能坏了。启动补采把这类缺口收敛掉。
    【判据】out/latest.json 的 run_at(每次采集都会写): 早于"今天最近一个已过时点"
            就补跑; 不早于则说明今天已采过(含手工刷新), 不重复触发(避免无故弹浏览器)。
    【代价】补采会短暂弹出 Edge 窗口(大商所必须真实浏览器), 且只在真漏采时发生。
    """
    slot = _warrant_recent_slot()
    if not slot:
        return
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(root, "data", "warrant", "out", "latest.json"),
                  encoding="utf-8") as fh:
            run_at = (json.load(fh) or {}).get("run_at") or ""
        last = datetime.fromisoformat(run_at) if run_at else None
    except Exception:  # noqa: BLE001 - 读不到就当没采过, 补跑更安全
        last = None
    skipped = bool(last and last.date() == datetime.now().date()
                   and (last.hour, last.minute) >= slot)
    # 【取证】判定结果必须留痕: scheduler 的 logger 没有文件 handler, 只打 stderr 时
    #   等于"查无此事"; 而补采恰恰是"该跑却没跑"的兜底, 事后必须能回溯它到底判了什么。
    with suppress(Exception):
        os.makedirs(RESEARCH_LOG_DIR, exist_ok=True)
        with open(os.path.join(RESEARCH_LOG_DIR, "warrant_catchup.log"),
                  "a", encoding="utf-8") as fh:
            fh.write("[%s] slot=%02d:%02d last_run=%s → %s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), slot[0], slot[1],
                last.strftime("%Y-%m-%d %H:%M") if last else "(无记录)",
                "已采过, 跳过" if skipped else "漏采, 立即补跑"))
    if skipped:
        logger.info("Warrant catch-up skipped: 今天 %02d:%02d 后已采过(%s)",
                    slot[0], slot[1], last.strftime("%H:%M"))
        return
    logger.warning("Warrant catch-up: 今天 %02d:%02d 的采集时点已过但未采到 → 立即补跑",
                   slot[0], slot[1])
    _run_warrant_collection()


# ============================================================ 公众号采集(2026-09-21)
# 【常量】公众号采集时点(用户指定)。这一步**不烧 LLM**(每合集 2 请求,实测 6s)。
_WECHAT_COLLECT_TIMES = ["08:30", "18:30"]
# 【常量】公众号入库时点 —— **刻意后移 35 分钟**,不与研报三源抢 LLM 并发。
# 【为什么必须错峰】research_times=[08:10,18:00,22:00] 会按 +15/+30/+40 分钟错峰成
#   htfc 08:25/18:15/22:15、gtja 08:40/18:30/22:30、dz 08:50/18:40/22:40(另 +75 的
#   RAG 索引兜底)。若入库也放 18:30,就与 gtja **同一时刻**:gtja 最多 4 并发 + 入库 1
#   = 5 正好顶到账户上限,而 18:40 起 dz 再要 2 并发 ⇒ 必然 429。后移到 :05 落在主批之后
#   的间隙(08:55-09:20 / 18:45-19:10)内,互不挤占。
_WECHAT_INGEST_TIMES = ["09:05", "19:05"]
# 【常量】采集器与入库器共用的状态目录(水位线/账号注册表/inbox 都在这里)。
WECHAT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "wechat")

# 【常量】隆众资讯(2026-10-14)的状态目录与采集/入库时点。
#   采集 08:20 / 17:10 —— 早读 07:30 已发布 + 当日聚焦收尾(约 16:22)后;**不占 LLM**。
#   入库 10:00 / 20:00 —— **刻意避开研报三源的 LLM 并发窗口**
#     (research 08:10 错峰成 htfc 08:25 / gtja 08:40 / dz 08:50,RAG 09:25,日报 09:45;
#      账户并发上限 5,研报三源已占 2~3 路)。20:00 同理避开 18:15~19:35。
OILCHEM_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "oilchem")
OILCHEM_COLLECT_TIMES = ["08:20", "17:10"]
OILCHEM_INGEST_TIMES = ["10:00", "20:00"]
# 【常量】「微信读书通道」的状态目录(cookie / 公众号注册表 / 水位线 / 结果文件)。
#   为什么单独一个目录：该通道的凭据是**网页登录 cookie**(wr_skey 等)，
#   与合集通道的"零登录"体系完全不同,分开存避免互相污染。
#   ⚠️ 但两者的 **inbox 是同一个**(见 wechat_weread_collector.F_INBOX) ⇒ 入库链路零改动。
WEREAD_STATE_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "weread")


def _wechat_weread_count(since: float) -> tuple:
    """(微信读书通道新发现篇数, 计数来源) —— 读采集器写的 mp_collect_result.json。

    与 `_wechat_collect_count` 同思路：**不信 stdout**(服务进程里会整段丢)，
    只认采集器自己落的机器可读结果文件；加 mtime 闸防"读到的却是上一轮旧数字"。
    """
    p = os.path.join(WEREAD_STATE_DIR, "mp_collect_result.json")
    try:
        if os.path.exists(p) and os.path.getmtime(p) >= since:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            return int(d.get("new") or 0), "file"
    except Exception:  # noqa: BLE001 - 读文件失败视为 0,不抛
        pass
    return 0, "none"


def _wechat_step(env: dict, script_dir: str, venv_py: str, name: str,
                 args: list, timeout: int):
    """跑一步公众号子进程并把完整输出落盘。返回 (output, error_msg)。

    ⚠️ 2026-09-21:输出改用 `_capture_run`(**重定向到文件,不经 PIPE**)。实测在
    无控制台的服务进程里 `capture_output=True` 对本链路两个脚本都只会拿到空串
    (落盘只剩 `returncode=0 → ok` + "(无输出)");而同一父进程抓 htfc/gtja/dz
    "看起来有输出",其实是那三个采集器**自己 tee 了同一份日志**,并非管道真的通。
    """
    try:
        r = _capture_run([venv_py, "-u"] + args, cwd=script_dir, env=env,
                         timeout=timeout, tag="wechat_" + name)
    except Exception as e:  # noqa: BLE001 - 超时/启动失败都归为"这一步没跑成"
        _persist_collector_output("wechat_" + name, f"(step failed: {e})", -1)
        return "", f"{name} 子进程失败: {e}"
    out = r.stdout or ""
    if not out.strip():
        # 【诊断】零输出时把可判定的事实一起留档,省得下次再靠猜。
        out += ("(子进程零输出 · 诊断) rc=%s 解释器=%s 存在=%s cwd存在=%s"
                % (r.returncode, venv_py,
                   os.path.exists(venv_py + ".exe") or os.path.exists(venv_py),
                   os.path.isdir(script_dir)))
    _persist_collector_output("wechat_" + name, out, r.returncode)
    if r.returncode != 0:
        return out, f"{name} 退出码 {r.returncode}"
    return out, None


def _wechat_result(name: str) -> dict:
    """读子进程写的**机器可读结果文件**(比解析 stdout 可靠)。缺失/损坏返回 {}。

    【为什么】stdout 在服务进程里可能整段丢失(见 `_wechat_step`),而计数写进
    告警是给人看的唯一凭据 ⇒ 宁可读文件,也不要"静默变 0"。
    """
    p = os.path.join(WECHAT_STATE_DIR, "wechat_%s_result.json" % name)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _wechat_ingest_counts(out: str, result: dict) -> tuple:
    """(新入库, 重跑 processing 残留) —— 优先结果文件,退化回解析 stdout。"""
    if "inserted" in result or "retried" in result:
        with suppress(Exception):
            return int(result.get("inserted") or 0), int(result.get("retried") or 0)
    inserted = retried = 0
    for line in out.splitlines():
        # ⚠️ 必须两个**独立 if**：脚本的汇总行把「新入库」与「重跑 processing 残留」
        #    写在**同一行**上，用 elif 会让后者永远解析不到(实测 retried 恒为 0)。
        if "新入库" in line:
            with suppress(Exception):
                inserted = int(line.split("新入库")[1].split("篇")[0].strip())
        if "重跑 processing 残留" in line:
            with suppress(Exception):
                retried = int(line.split("重跑 processing 残留")[1].split("篇")[0].strip())
    return inserted, retried


def _wechat_collect_count(out: str, since: float) -> tuple:
    """(新发现篇数, 计数来源) —— 优先采集器自己写的 txt(需比 since 新),退化回 stdout。

    【为什么不用 stdout】同 `_wechat_step` 注释。采集器每次都把结果**整篇重写**到
    `album_collect_result.txt`(truncate 模式),所以文件天然就是"最后一轮"的结论;
    加 mtime 闸是为了防"本轮采集早退失败,读到的却是上一轮的旧数字"。
    """
    p = os.path.join(WECHAT_STATE_DIR, "album_collect_result.txt")
    try:
        if os.path.exists(p) and os.path.getmtime(p) >= since:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh.read().splitlines():
                    if "本次新增" in line:
                        with suppress(Exception):
                            return int(line.split("本次新增")[1].split("篇")[0].strip()), "file"
    except Exception:  # noqa: BLE001 - 读文件失败就退化到 stdout,不抛
        pass
    for line in out.splitlines():
        if "本次新增" in line:
            with suppress(Exception):
                return int(line.split("本次新增")[1].split("篇")[0].strip()), "stdout"
    return 0, "none"


def _weread_failure_kind(err: str) -> str:
    """把读书通道的失败分成两类 —— 决定「该不该自动重试」。

    · credentials：采集器 `main()` 续期两次都失败 ⇒ **长期凭据 wr_vid/wr_rt 失效**，
      只能人工扫码。这类**不重试**（重试只会再失败一次，还会把"告警"拖成"疑似已恢复"）。
    · transient  ：其余（网络/DNS/平台 5xx/超时）⇒ 隔 60s 自动重试一次。

    【为什么用退出码判据】`wechat_weread_collector.py` 在续期失败时 `return 3`；
      `_wechat_step` 把它转成「weread 退出码 3」。这个约定比解析 stderr 文本稳定。
    """
    return "credentials" if "退出码 3" in (err or "") else "transient"


def _run_wechat_collect(time_str: str = ""):
    """① 公众号**采集**一步:零登录「合集」通道游标增量,只列/抓新文,不碰 LLM。

    【关键逻辑】纯抓取:无新文时每合集 2 个请求(实测 6s)。失败只告警,不抛。
            ⚠️ 只看**已登记合集** —— 该号若有未登记合集,其新文不在检测内
            (零登录无法枚举该号全部合集),采集器输出自带这段边界声明。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "wechat_collect_started", "WeChat MP collect started",
                f"公众号采集{tag} 开始（游标增量，不占 LLM）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()                       # 【变量】不继承 web 服务标记
    env["AGENTSENSE_WECHAT_DIR"] = WECHAT_STATE_DIR

    _since = datetime.now().timestamp()  # 【变量】本轮开始时刻(用于判定结果文件是否为本轮所写)
    out, err = _wechat_step(env, script_dir, venv_py, "collect",
                            ["wechat_album_collector.py", "--daily"], 1800)
    if err:
        _safe_alert(db, "wechat_collect_failed", "WeChat MP collection failed",
                    f"公众号采集{tag} 失败：{err}", severity="error")
        return
    collected, src = _wechat_collect_count(out, _since)

    # ── 第二路：微信读书通道（2026-10-14 新增）─────────────────────────────
    # 【为什么需要】机构号（中信期货研究/混沌天成研究/物产中大…）**不建合集**，
    #   合集通道对它们完全无效（album_info_list 为空数组）。而微信读书收录了这些号，
    #   走 /web/mp/articles + /web/mp/content 可拿到文章清单与正文。
    # 【为什么不留新时点】它写的是**同一个 inbox** ⇒ 紧跟其后就能被 09:05/19:05 的
    #   入库任务消费，无需新增调度时点、也无需改入库链路。
    # 【失败不阻塞】读书通道挂了（cookie 过期等）只单独告警，**不影响合集通道的结论**。
    _w_since = datetime.now().timestamp()
    w_env = dict(env)
    w_env["AGENTSENSE_WEREAD_DIR"] = WEREAD_STATE_DIR

    # 用 partial 而不是局部函数：失败分支里**不能出现 return**
    #   （旧护栏 test_scheduler_weread_failure_is_isolated 钉的是"不能提前退出，
    #    否则会吞掉 wechat_collect_done 完成告警"；局部函数的 return 会被误判）。
    _run_weread = partial(_wechat_step, w_env, script_dir, venv_py, "weread",
                          ["wechat_weread_collector.py", "--daily"], 1800)

    w_out, w_err = _run_weread()
    # 【失败自动重试 2026-10-19】
    #   采集器内部已有「启动时续期 ×2 + 请求级 -2012 续期重试」⇒ 仍失败多半是**瞬时抖动**
    #   （网络/DNS/平台短暂 5xx）。隔 60s 自动重试一次，覆盖大部分偶发。
    #   ⚠ 但「退出码 3」= 长期凭据（wr_vid/wr_rt）失效、两次续期都没成功 ⇒ **重试无意义**，
    #     必须人工扫一次码；这类不重试、直接按 error 告警，避免"重试成功"的假象掩盖问题。
    _retried = 0
    if w_err and _weread_failure_kind(w_err) == "transient":
        time.sleep(60)
        _w_since = datetime.now().timestamp()      # 重试要重新取时点，否则读不到本次结果文件
        w_out, w_err = _run_weread()
        _retried = 1
    w_collected, _w_src = _wechat_weread_count(_w_since)
    if w_err:
        if _weread_failure_kind(w_err) == "credentials":
            _safe_alert(db, "wechat_weread_collect_failed", "WeChat MP (weread) collect failed",
                        f"读书通道采集{tag} 失败：{w_err}"
                        f"（长期凭据 wr_vid/wr_rt 已失效：自动续期两次均失败 ⇒ **必须扫码**"
                        f" node probe/_weread_login.mjs；合集通道结论不受影响）",
                        severity="error")
        else:
            _safe_alert(db, "wechat_weread_collect_failed", "WeChat MP (weread) collect failed",
                        f"读书通道采集{tag} 失败：{w_err}"
                        f"（已自动续期并重试 {_retried} 次仍失败；"
                        f"90 分钟会话密钥会自动刷新，通常**无需扫码**。合集通道结论不受影响）",
                        severity="warn")

    # 【2026-10-19】有新文就**立即**触发入库，消除 09:05/19:05 之间最长 10h 的空窗。
    #   没有新文不触发（不烧 LLM）。并发由 _INGEST_LOCK 兜底。
    _new_total = max(0, int(collected or 0)) + max(0, int(w_collected or 0))
    _safe_alert(db, "wechat_collect_done", "WeChat MP collect done",
                f"公众号采集{tag} 完成：合集通道新发现 {collected} 篇 / "
                f"读书通道新发现 {w_collected} 篇"
                + (f"（读书通道出错：{w_err[:80]}）" if w_err else "")
                + ("（共 %d 篇新文 ⇒ 立即触发入库）" % _new_total if _new_total > 0
                   else f"（无新文，不触发入库；定时任务 {_WECHAT_INGEST_TIMES} 仍会兜底）"),
                severity="info")

    if _new_total > 0:
        _safe_alert(db, "wechat_ingest_triggered", "WeChat MP ingest triggered",
                    f"采集到 {_new_total} 篇新文 ⇒ 立即入库（默认串行，避免与研报三源抢 LLM）",
                    severity="info")
        _run_wechat_ingest(tag)


_INGEST_LOCK = threading.Lock()


def _run_wechat_ingest(time_str: str = ""):
    """② 公众号入库**入口**：进程级互斥，避免两路并发跑同一批。

    【为什么需要】「采集后自动触发」与 19:05 定时任务可能同时起来；
      DB 去重发生在**每篇处理完之后**，并发时同一篇会被两路各跑一遍 LLM
      （既烧钱又可能撞 SQLite 写锁）。两路都在同一 web 进程内，进程级锁足够。
    """
    if not _INGEST_LOCK.acquire(blocking=False):
        _safe_alert(get_db(), "wechat_ingest_skipped", "WeChat MP ingest skipped",
                    "公众号入库已在进行中 ⇒ 跳过本次触发（幂等，本轮结束后下一轮会自动补）",
                    severity="info")
        return
    try:
        return _run_wechat_ingest_impl(time_str)
    finally:
        _INGEST_LOCK.release()


def _run_wechat_ingest_impl(time_str: str = ""):
    """② 公众号**入库**一步:把 inbox 里未入库的文章接进研报库(要走 LLM)。

    【关键逻辑】读 **wechat_inbox.jsonl(追加式)** 而不是 wechat_new.json(每轮被覆盖):
            采集后若入库失败/进程被杀,文章已在 inbox 留痕,**下一轮自动补入**;
            已入库的靠 research_reports.source_url/filename 去重跳过 ⇒ 幂等可重跑。
            每篇走 LLM 约 2-5 分钟,超时给到 60 分钟。失败只告警,不抛。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "wechat_ingest_started", "WeChat MP ingest started",
                f"公众号入库{tag} 开始（读 inbox，DB 去重，幂等）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()
    env["AGENTSENSE_WECHAT_DIR"] = WECHAT_STATE_DIR

    # 【2026-10-14 用户口径:今天优先 + 历史暂缓】只处理"最近 N 天"发布的文章。
    #   为什么必须加:入库器按 create_time **最旧优先** + --limit 20,若 inbox 里积压了
    #   历史基线(实测微信读书通道初始化灌入 118 篇,跨 2026-06-09~10-13),
    #   当天新文会被排在数轮之后 ⇒ 用户报"研报里看不到今天的公众号观点"。
    #   窗口取"今天-1"而非"仅今天":文章可能是"昨晚发布、今早采集",只按当天会漏;
    #   重复处理无害(DB 按 key/url 幂等跳过)。可用 WECHAT_INGEST_SINCE_DAYS 调整。
    _since_days = int(os.getenv("WECHAT_INGEST_SINCE_DAYS") or 1)
    _since = (datetime.now() - timedelta(days=_since_days)).strftime("%Y-%m-%d")
    out, err = _wechat_step(env, script_dir, venv_py, "ingest",
                            ["wechat_ingest.py", "--source", "inbox", "--since", _since], 3600)
    if err:
        _safe_alert(db, "wechat_ingest_failed", "WeChat MP ingest failed",
                    f"公众号入库{tag} 失败：{err}（采集已留痕 inbox，下轮自动补入）",
                    severity="error")
        return
    # 【取数】优先入库脚本自己写的结果文件;缺失时退化回解析 stdout(见 _wechat_result)
    inserted, retried = _wechat_ingest_counts(out, _wechat_result("ingest"))
    _safe_alert(db, "wechat_ingest_done", "WeChat MP ingest done",
                f"公众号入库{tag} 完成：新入库 {inserted} 篇，重跑残留 {retried} 篇",
                severity="info")


# ── 隆众资讯 (2026-10-14) ──────────────────────────────────────────────────
# 与公众号同构:采集(免登录 HTTP,**不占 LLM**)与入库(走 LLM)拆成两组 job。
# 【差异·必须限速】隆众对连续请求返回 429(实测连打 5~6 次即触发),
#   脚本内已内置 8s 间隔 + 退避;单轮最多 40 篇 × 8s ≈ 5 分钟,故超时给 30 分钟。
# 【复用】借 `_wechat_step` 跑子进程(它只把 name 用于日志文件名,与功能无关) ⇒
#   日志会落成 research_collect_wechat_oilchem_*.log —— 带 oilchem 字样,按名可搜到。
def _oilchem_result(name: str) -> dict:
    """读隆众子进程写的**机器可读结果文件**(比解析 stdout 可靠)。缺失/损坏返回 {}。

    【为什么】沿用公众号链路的实测结论:从服务进程启动子进程时,子进程 stdout 可能
    整段丢失(同一命令在交互式 shell 跑却正常) ⇒ 计数只认结果文件,不猜。
    【参数】name: "news"(采集) | "ingest"(入库)。
    """
    p = os.path.join(OILCHEM_STATE_DIR, "oilchem_%s_result.json" % name)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _run_oilchem_collect(time_str: str = ""):
    """① 隆众资讯**采集**(免登录,不占 LLM)。

    【关键逻辑】读追加式归档(pending 靠 DB 去重) ⇒ 崩溃后下轮自动补;
    被登录墙拦掉的与正文过短的不进归档(脚本内已判),不烧 LLM。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "oilchem_collect_started", "OilChem news collect started",
                f"隆众资讯采集{tag} 开始（免登录 HTTP，限速 8s/篇）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()
    env["AGENTSENSE_OILCHEM_DIR"] = OILCHEM_STATE_DIR

    out, err = _wechat_step(env, script_dir, venv_py, "oilchem_collect",
                            ["oilchem_news_collect.py", "--pages", "2"], 1800)
    if err:
        _safe_alert(db, "oilchem_collect_failed", "OilChem news collection failed",
                    f"隆众资讯采集{tag} 失败：{err}", severity="error")
        return
    r = _oilchem_result("news")
    _safe_alert(db, "oilchem_collect_done", "OilChem news collect done",
                f"隆众资讯采集{tag} 完成：新增可读 {r.get('collected', '?')} 篇 / "
                f"登录墙 {r.get('walled', '?')} / 失败 {r.get('failed', '?')}",
                severity="info")


def _run_oilchem_ingest(time_str: str = ""):
    """② 隆众资讯**入库**(走 LLM)。读归档,DB 去重,幂等可重跑。

    【关键】入库后会自动跑**归属校验**(`oilchem_ingest.post_verify_attribution`,
    不额外烧 LLM),修正两类归属缺陷:
      ① 多品种行首项是池外/上游 ⇒ 改归到标题主体对应的池内码;
      ② LLM 未输出品种、走关键词兜底误判(隆众常把「橡胶」当下游需求方提及) ⇒ 标 skipped。
    """
    db = get_db()
    tag = ("@" + time_str) if time_str else ""
    _safe_alert(db, "oilchem_ingest_started", "OilChem news ingest started",
                f"隆众资讯入库{tag} 开始（读归档，DB 去重，含归属校验）", severity="info")
    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = _collector_env()
    env["AGENTSENSE_OILCHEM_DIR"] = OILCHEM_STATE_DIR

    out, err = _wechat_step(env, script_dir, venv_py, "oilchem_ingest",
                            ["oilchem_ingest.py", "--limit", "20"], 5400)
    if err:
        _safe_alert(db, "oilchem_ingest_failed", "OilChem news ingest failed",
                    f"隆众资讯入库{tag} 失败：{err}（采集已留档，下轮自动补入）",
                    severity="error")
        return
    r = _oilchem_result("ingest")
    _safe_alert(db, "oilchem_ingest_done", "OilChem news ingest done",
                f"隆众资讯入库{tag} 完成：新入库 {r.get('inserted', '?')} 篇"
                f"（有效观点 {r.get('usable', '?')} 篇）",
                severity="info")


def start_scheduler(schedule_times: list[str] = None, research_times: list[str] = None,
                    warrant_times: list[str] = None,
                    wechat_collect_times: list[str] = None,
                    wechat_ingest_times: list[str] = None,
                    archive_times: list[str] = None,
                    oilchem_collect_times: list[str] = None,
                    oilchem_ingest_times: list[str] = None):
    """启动后台调度器(每日定时管道 + 开盘前研报接入 + 30 分钟健康检查)。

    【功能】创建并启动 APScheduler 后台调度器,注册每日管道任务、开盘前研报
            接入任务与健康检查任务,同时初始化默认管理员账号。
    【参数】schedule_times: 每日执行管道的时间点列表,如 ["08:00","18:00"];
            默认 ["08:00","18:00"]。
            research_times: 每日开盘前研报接入的时间点列表,默认
            ["08:10","18:00"](日盘 09:00 前 + 夜盘 21:00 前各一次)。
            warrant_times: 每日仓单采集的时间点列表,默认 ["08:00"](2026-09-21
            新增:每天早 8 点采四所仓单日报并重建「仓单分析」板块数据)。
            wechat_collect_times: 公众号**采集**时点,默认 ["08:30","18:30"](零登录
            「合集」通道游标增量,不占 LLM)。
            wechat_ingest_times: 公众号**入库**时点,默认 ["09:05","19:05"] ——
            刻意比采集晚 35 分钟,**避开研报三源的 LLM 并发窗口**(见 _WECHAT_INGEST_TIMES)。
            oilchem_collect_times: 隆众资讯**采集**时点,默认 ["08:20","17:10"](2026-10-14
            新增;免登录 HTTP,不占 LLM;08:20 收早读、17:10 收当日聚焦收尾)。
            oilchem_ingest_times: 隆众资讯**入库**时点,默认 ["10:00","20:00"] ——
            刻意避开研报三源窗口(08:25~09:45 / 18:15~19:35),见 OILCHEM_INGEST_TIMES。
    【返回】BackgroundScheduler: 已启动的调度器对象(全局 _scheduler)。
    【关键逻辑】
            - 每个管道时间点注册一个 CronTrigger 触发的 _run_daily_pipeline 任务。
            - 每个研报时间点注册三个 CronTrigger 任务(2026-09-09 起三源,发现报告
              源已剔除):_run_htfc_collection(华泰天玑,research_htfc_{time})、
              _run_gtja_collection(国君,research_gtja_{time})、
              _run_dongzheng_collection(东证繁微,research_dz_{time})。
            - 另注册 _health_check 任务,每 30 分钟(minute="*/30")运行一次。
            - 启动后立即调用 get_db().ensure_default_user() 确保管理员存在。
            - daemon=True:调度器作为守护线程运行,不阻塞主进程退出。

    Start the background scheduler.

    Args:
        schedule_times: List of cron-style times, e.g. ["08:00", "18:00"].
                        Default: ["08:00", "18:00"].
        research_times: List of cron-style times for daily research report
                        ingestion, e.g. ["08:10", "18:00", "22:00"].
                        Default: ["08:10", "18:00", "22:00"].
        warrant_times: List of cron-style times for warrant (仓单) daily
                       collection, e.g. ["08:00"]. Default: ["08:00"].
    """
    global _scheduler

    if schedule_times is None:
        schedule_times = ["08:00", "18:00"]  # 【变量】默认执行时间点(早 8 点/晚 6 点各跑一次管道)
    if research_times is None:
        # 【变量】研报接入时间点:开盘前 08:10 + 晚 18:00 + 夜盘 22:00 补充(2026-09-10 新增)
        research_times = ["08:10", "18:00", "22:00"]
    if warrant_times is None:
        # 【变量】仓单采集时间点(2026-09-21 需求:采集+更新整个仓单分析模块)
        #   08:00 —— 用户指定:早上打开看板即拿到「最近一个已发布交易日」全量数据;
        #   16:40 —— 追加:四所仓单日报在收盘后(~16:00 起)陆续发布, 只跑早间的话
        #            当天数据要等次日早上才进看板, 白天存在十几小时"日报已发布但
        #            看板还没有"的空窗, 用户会读成"采集了但日期不动"。16:40 补齐当日。
        warrant_times = ["08:00", "16:40"]
    if wechat_collect_times is None:
        # 【变量】公众号采集时点(用户指定 08:30/18:30;这一步不烧 LLM)
        wechat_collect_times = list(_WECHAT_COLLECT_TIMES)
    if wechat_ingest_times is None:
        # 【变量】公众号入库时点(后移 35 分钟,避开研报三源 LLM 并发窗口)
        wechat_ingest_times = list(_WECHAT_INGEST_TIMES)
    if archive_times is None:
        # 【变量】研报归档时点(2026-09-28): 09:20 —— 早间研报 LLM 并发窗口
        #   (08:10 华泰/08:25 国君/08:40 东证 + 公众号入库 09:05)结束后跑, 不抢 LLM。
        archive_times = ["09:20"]
    if oilchem_collect_times is None:
        # 【变量】隆众采集时点(不占 LLM):早读 07:30 后 / 当日聚焦收尾(约 16:22)后。
        oilchem_collect_times = list(OILCHEM_COLLECT_TIMES)
    if oilchem_ingest_times is None:
        # 【变量】隆众入库时点:**刻意避开研报三源窗口**(见 OILCHEM_INGEST_TIMES 注释)。
        oilchem_ingest_times = list(OILCHEM_INGEST_TIMES)

    _scheduler = BackgroundScheduler(daemon=True)  # 【调用函数】创建后台调度器(守护线程,不阻塞主进程退出)

    for time_str in schedule_times:
        hour, minute = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册每日管道任务(CronTrigger 按时间点触发)
            _run_daily_pipeline,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"daily_{time_str}",
            name=f"Daily pipeline {time_str}",
        )

    global _WARRANT_TIMES
    _WARRANT_TIMES = list(warrant_times)  # 【变量】登记时点, 供 _warrant_startup_catchup 判漏采

    for time_str in warrant_times:
        hour, minute = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册每日仓单采集任务(2026-09-21 新增)
            _run_warrant_collection,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"warrant_collect_{time_str}",
            name=f"Warrant collection {time_str}",
        )

    # 【启动补采】延后 90s 跑一次: 若今天的时点已过却漏采(服务当时没在运行)立刻补上。
    # 延后是为了避开启动瞬间的重活(僵尸研报回收 / RAG worker 拉起), 不与它们抢资源。
    _scheduler.add_job(  # 【调用函数】注册一次性启动补采(2026-09-21)
        _warrant_startup_catchup,
        DateTrigger(run_date=datetime.now() + timedelta(seconds=90)),
        id="warrant_catchup",
        name="Warrant catch-up on startup",
    )

    # 【2026-09-21】公众号:采集(零登录「合集」游标增量,不占 LLM)与入库(走 LLM)
    # 拆成两组 job —— 采集准点跑,入库后移 35 分钟,避开研报三源的 LLM 并发窗口。
    # max_instances=1:入库可能跑十几分钟,上一轮未完时 APScheduler 跳过本次而不叠加。
    for time_str in wechat_collect_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册公众号采集任务
            _run_wechat_collect,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"wechat_collect_{time_str}",
            name=f"WeChat MP collect {time_str}",
            max_instances=1,
            misfire_grace_time=1800,
        )
    for time_str in wechat_ingest_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册公众号入库任务(读 inbox,DB 去重,幂等)
            _run_wechat_ingest,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"wechat_ingest_{time_str}",
            name=f"WeChat MP ingest {time_str}",
            max_instances=1,
            misfire_grace_time=3600,
        )

    # 【2026-10-14】隆众资讯:采集(免登录,不占 LLM)与入库(走 LLM)同样拆两组 job。
    # max_instances=1:入库可能跑几十分钟,上一轮未完时 APScheduler 跳过本次而不叠加。
    # misfire_grace_time:采集 30 分钟 / 入库 60 分钟(与公众号同口径),服务短暂重启后仍会补跑。
    for time_str in oilchem_collect_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册隆众资讯采集任务
            _run_oilchem_collect,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"oilchem_collect_{time_str}",
            name=f"OilChem collect {time_str}",
            max_instances=1,
            misfire_grace_time=1800,
        )
    for time_str in oilchem_ingest_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册隆众资讯入库任务(读归档,DB 去重,含归属校验)
            _run_oilchem_ingest,
            CronTrigger(hour=int(h), minute=int(m)),
            args=[time_str],
            id=f"oilchem_ingest_{time_str}",
            name=f"OilChem ingest {time_str}",
            max_instances=1,
            misfire_grace_time=3600,
        )

    for time_str in research_times:
        hour, minute = time_str.split(":")
        # 【关键】三源错峰注册(15/30/40 分钟偏移):若同一时刻并跑,峰值 LLM 并发 =
        # 4(HTFC)+4(GTJA)+2(DZ)=10 > 账户并发上限 5 → 429。
        # DZ 用 +40 且自带 2 线程:即使 GTJA(≤30 篇,90min 超时)仍未跑完,
        # 4(GTJA)+2(DZ)=6 仍略超 5——DZ 并发已压到 2 是可接受折中(动态快评单条
        # 短,429 重试由 LLM 客户端兜底);job id 仍用原 time_str 保持唯一/可读。
        # 【源裁撤】2026-09-09 起发现报告源(fxbaogao,_run_research_collection,
        # job id research_{time})剔除:三家 API 源已覆盖且重复度高。
        base_minutes = int(hour) * 60 + int(minute)
        for offset, fn, prefix, name in (  # 【变量】(偏移分钟, 任务函数, job id 前缀, 任务名)
            (15, _run_htfc_collection, "research_htfc_", "HTFC research collection"),
            (30, _run_gtja_collection, "research_gtja_", "GTJA research collection"),
            (40, _run_dongzheng_collection, "research_dz_", "Dongzheng research collection"),
            # 【RAG 兜底】三源采集跑完后再补一次向量索引(单篇即时索引失败时的兜底)。
            (75, _run_rag_index, "research_rag_", "RAG index pending reports"),
            # 【日报生成 2026-10-12】采集链收尾:等当天研报全部处理完自动生成日报。
            #   +95min 在 RAG(+75)之后 20 分钟,给最后入队的研报留出 LLM/RAG 收尾时间。
            (95, _run_daily_summary, "research_daily_", "Daily research summary"),
        ):
            h, m = divmod(base_minutes + offset, 60)
            _scheduler.add_job(  # 【调用函数】注册错峰研报接入任务
                fn,
                CronTrigger(hour=h, minute=m),
                id=f"{prefix}{time_str}",
                name=f"{name} {time_str}+{offset}min",
            )

    # 【2026-09-11 需求三】每周一 08:05 自动生成「上一周」研报周报总结(周一早上,
    # 上周周日~周六区间已完整;08:05 与 08:00 每日管道错开)。
    _scheduler.add_job(  # 【调用函数】注册每周一周报总结生成任务
        _run_weekly_summary,
        CronTrigger(day_of_week="mon", hour=8, minute=5),
        id="weekly_research_summary",
        name="Weekly research summary (Mon 08:05)",
    )

    # 【2026-09-28】每日增量归档 >14 天研报(总结化→RAG→软删), 时点见 archive_times。
    for time_str in archive_times:
        h, m = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册研报归档任务
            _run_summary_archive,
            CronTrigger(hour=int(h), minute=int(m)),
            id=f"summary_archive_{time_str}",
            name=f"Research summary archive {time_str}",
        )

    # 【2026-09-11 需求三】每日 02:30 清理超过 14 天的研报原件文件(凌晨低峰,
    # 避免与采集/管道抢 IO;聚合 JSON / RAG / 结论全部保留)。
    _scheduler.add_job(  # 【调用函数】注册研报原件清理任务
        _run_research_file_cleanup,
        CronTrigger(hour=2, minute=30),
        id="research_file_cleanup",
        name="Research original file cleanup (daily 02:30, >14d)",
    )

    # 【2026-09-11】每日 02:40 聚合 JSON 对账自愈:done 行缺失的聚合条目从 DB
    # 重建(零 LLM),防"逐品种观点总览与总结不对齐"复发。
    _scheduler.add_job(  # 【调用函数】注册聚合对账任务
        _run_aggregate_repair,
        CronTrigger(hour=2, minute=40),
        id="research_aggregate_repair",
        name="Research aggregate reconciliation (daily 02:40)",
    )

    # 【2026-09-24】抖音半区轮: 早晚各两轮(06:00/08:00 与 18:00/20:00), 与统一管道
    # (13 词/轮 + rotate 游标)互补, 每天 4 轮把词池全部 25 词各采 2 遍 —— 早间 8 点前、
    # 晚间 8 点后都各有完整一轮数据。相邻两轮间隔 2h > 抖音 45min 限流冷却期, 互不传染。
    _scheduler.add_job(  # 【调用函数】注册 06:00 抖音早轮
        _run_douyin_round,
        CronTrigger(hour=6, minute=0),
        id="douyin_morning",
        name="Douyin half-pool (06:00)",
    )
    _scheduler.add_job(  # 【调用函数】注册 20:00 抖音晚轮(与 18:00 管道轮互补)
        _run_douyin_round,
        CronTrigger(hour=20, minute=0),
        id="douyin_evening",
        name="Douyin half-pool (20:00)",
    )

    _scheduler.start()  # 【调用函数】启动调度器(任务开始按时触发)

    # Also run a health check every 30 minutes
    _scheduler.add_job(  # 【调用函数】注册健康检查任务(每 30 分钟)
        _health_check,
        CronTrigger(minute="*/30"),
        id="health_check",
        name="Health check",
    )

    # Initialize default user
    get_db().ensure_default_user()  # 【调用函数】确保默认管理员账号存在(首次启动自动创建)

    return _scheduler


# 【2026-09-17】同类健康告警的抑制窗口(小时)。
# 采集每天只有 08:00/18:00 两轮,一次失败若不去重,会在这两轮之间的长空窗里被
# **每 30 分钟重复上报**(实测 09-16 21:30 → 09-17 04:00 连发 13 条,而管道其实
# 早在 02:02 就写下了 pipeline_complete),把告警中心刷成噪声、淹没真问题。
HEALTH_ALERT_SUPPRESS_HOURS = 6


def _health_check():
    """周期性健康检查——发现采集异常并告警(同类告警带去重抑制)。

    【功能】每 30 分钟检查**每个平台各自最近一次**采集,若存在失败则写健康告警;
            同类告警在抑制窗口内只报一次。
    【参数】无。
    【返回】无。
    【关键逻辑】
            - 用 get_latest_collection_per_platform 取"各平台各自最新一条"。
              旧实现取**全局**最近 5 条,两端都错:一条失败既可能被后续其他平台的
              记录**挤出窗口**(过早丢失,判断不出哪个平台坏了),又会在两轮采集
              之间的 8 小时里**长期滞留**(同一失败被反复告警)。按平台取最新,
              语义才对——该平台下一次采集成功即自动覆盖,无需"挤出"。
            - 命中失败后先查抑制窗口内是否已有同类告警,有则直接返回,不重复刷屏。
    """
    db = get_db()  # 【调用函数】取数据库实例(查最近采集历史)

    latest = db.get_latest_collection_per_platform()  # 【调用函数】各平台各自最近一次采集
    failures = {p: r for p, r in latest.items() if r.get("status") == "error"}  # 【变量】failures:最近一次采集失败的平台
    if not failures:
        return

    # 抑制:窗口内已有同类告警就直接跳过(失败的平台下一次采集成功后会自然消失)
    if db.get_latest_alert("health_warning", hours=HEALTH_ALERT_SUPPRESS_HOURS):  # 【调用函数】查窗口内是否已告警过
        return

    detail = "; ".join(
        f"{_p}: {(r.get('error_msg') or 'error')[:60]}" for _p, r in sorted(failures.items())
    )  # 【变量】detail:失败平台及原因摘要
    _safe_alert(db,
        "health_warning",  # 【调用函数】写入"健康检查"告警
        "Recent collection failures detected",
        f"{len(failures)} 个平台最近一次采集失败(共 {len(latest)} 个平台) | {detail}",
        severity="warning",
    )


def stop_scheduler():
    """停止后台调度器。

    【功能】关闭全局调度器并置空引用,通常在应用退出/重启时调用。
    【参数】无。
    【返回】无。
    【关键逻辑】shutdown(wait=False) 不等待任务结束,立即返回,避免阻塞退出。
    """
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)  # 【调用函数】关闭调度器(不等待进行中任务,立即返回)
        _scheduler = None
