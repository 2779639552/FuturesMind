"""有界并行地跑"每个平台一个采集子进程"的多平台采集任务。

为什么单独成模块
----------------
定时管道(scheduler._run_daily_pipeline)与网页"一键更新"(web_app /api/update_data)
两条链路都要把"六平台串行采集"改成并行, 但两者的子进程参数、日志方式不同。
把**调度**部分抽出来共用, 才能保证两条链路的并发度规则、异常隔离、结果顺序
完全一致 —— 否则"改了 A 忘了 B"就是下一个静默不一致。

关键约束(都是踩过的坑)
----------------------
1) **有界**: 每个采集子进程内部会各起一个 Chromium(抖音/知乎/雪球/小红书都是
   Playwright), 无界并发会把内存和 CPU 打满, 反而让每个平台都变慢甚至超时
   ⇒ 默认 3, 硬上限 6, 可用环境变量 AGENTSENSE_COLLECT_CONCURRENCY 覆盖。
2) **顺序**: 并行完成顺序不确定, 但返回结果必须按调用方给定的平台顺序排列 ——
   否则告警文案与前端日志会随机抖动, 排查时无法与历史记录比对。
3) **隔离**: 单平台抛错只记进 errors, 绝不冒泡打断其余平台(某个平台 Cookie
   失效是常态, 不能让整个管道缺项)。

并发度解析规则(刻意写死, 便于确定性单测)
----------------------------------------
- 环境变量取不到/不是整数/小于 1  → 回退默认 3(手滑不该让采集变成 0 并发)
- 大于硬上限 6                    → 钳到 6(意图是"要更多", 但不能给到 99)
- 最终再被平台数兜一层            → max(1, min(n, platform_count))
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

DEFAULT_CONCURRENCY = 3  # 【常量】默认并发度(墙钟近似减半 vs 浏览器实例数不炸的折中)
MAX_CONCURRENCY = 6  # 【常量】硬上限: 每个采集子进程会各起一个 Chromium
ENV_VAR = "AGENTSENSE_COLLECT_CONCURRENCY"  # 【常量】并发度覆盖用的环境变量名


def resolve_concurrency(platform_count: int, env: dict | None = None) -> int:
    """解析采集并发度(纯函数, 便于确定性单测)。

    【参数】
        platform_count: 本轮要采的平台数(结果不会超过它)
        env: 环境变量映射, 默认 os.environ(测试可注入)
    【返回】并发度, 范围 [1, min(6, platform_count)]
    """
    source = os.environ if env is None else env
    try:
        n = int(str(source.get(ENV_VAR, "")).strip())
    except (TypeError, ValueError):
        n = DEFAULT_CONCURRENCY
    else:
        if n < 1:
            n = DEFAULT_CONCURRENCY
        elif n > MAX_CONCURRENCY:
            n = MAX_CONCURRENCY
    return max(1, min(n, max(1, int(platform_count))))


def run_platforms(
    platforms: Iterable[str],
    worker: Callable[[str], Any],
    *,
    concurrency: int | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """在**有界线程池**里为每个平台跑一次 worker, 返回 (结果, 错误)。

    【参数】
        platforms: 平台名序列(返回结果按此顺序排列)
        worker:   单平台任务 callable(platform) -> 任意结果; 抛异常即视为该平台失败
        concurrency: 显式并发度; None 则按 resolve_concurrency() 解析
    【返回】(results, errors):
        results: {平台: worker 返回值}, 按 platforms 顺序, **失败平台不出现**
        errors:  {平台: 错误摘要},     也按 platforms 顺序
    【关键逻辑】worker 内部若自己吞掉异常并返回结论(如 scheduler 的做法),
        则该平台只会出现在 results 里 —— 两种风格都支持, 互不冲突。
    """
    platform_list = list(platforms)
    workers = concurrency if concurrency is not None else resolve_concurrency(len(platform_list))
    workers = max(1, min(int(workers), max(1, len(platform_list))))

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if not platform_list:
        return results, errors

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        futures = {pool.submit(worker, p): p for p in platform_list}
        for fut in as_completed(futures):
            platform = futures[fut]
            try:
                results[platform] = fut.result()
            except Exception as exc:  # noqa: BLE001  单平台失败绝不能拖垮其余平台
                errors[platform] = str(exc)[:300]

    # 还原调用方给定的顺序(并行完成顺序是不确定的)
    ordered_results = {p: results[p] for p in platform_list if p in results}
    ordered_errors = {p: errors[p] for p in platform_list if p in errors}
    return ordered_results, ordered_errors
