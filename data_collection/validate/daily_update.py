"""
每日一键更新: 多平台采集 → 聚合 → 价格 → 看板
=============================================
Usage: python daily_update.py
       python daily_update.py --platforms xhs weibo
       python daily_update.py --per-kw 20 --max-detail 5
"""

import argparse  # 【调用包】命令行参数解析(--platforms/--per-kw/--max-detail)
import os  # 【调用包】切换工作目录(os.chdir)
import subprocess  # 【调用包】运行外部采集/聚合/价格/看板子进程
import sys  # 【调用包】阻塞步骤失败时退出
from pathlib import Path  # 【调用包】定位 validate 目录(os.chdir 目标)

# 默认每日更新配置
DEFAULT_PLATFORMS = ["xhs"]  # 【变量】默认平台(默认只跑小红书, 微博/知乎按需启用)
DEFAULT_PER_KW = 30  # 【变量】每关键词默认搜索条数
DEFAULT_MAX_DETAIL = 5  # 【变量】每关键词默认深挖条数


# 【功能】运行一个更新步骤的子进程, 打印命令并判定成败。
# 【参数】name: 步骤名; cmd: 子进程命令参数列表; blocking: 失败时是否立即退出。
# 【返回】bool 是否成功(失败且 blocking=True 时 sys.exit(1))。
def run_step(name: str, cmd: list[str], blocking: bool = False):
    """运行一个步骤，返回是否成功"""
    print(f"\n{'=' * 50}\n  {name}\n  {' '.join(cmd)}\n{'=' * 50}")
    result = subprocess.run(cmd, capture_output=False)  # 【调用函数】外部系统调用: 运行采集/聚合/价格/看板脚本
    if result.returncode != 0:
        print(f"  ⚠️  FAILED: {name}")
        if blocking:
            sys.exit(1)
        return False
    print(f"  ✅  {name} done")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日一键更新: 多平台采集 → 聚合 → 看板")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=DEFAULT_PLATFORMS,
        help="目标平台 (默认 xhs, 可多选: xhs weibo zhihu)",
    )
    parser.add_argument("--per-kw", type=int, default=DEFAULT_PER_KW, help="每关键词搜索条数")
    parser.add_argument(
        "--max-detail", type=int, default=DEFAULT_MAX_DETAIL, help="每关键词深挖条数"
    )
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)  # 【调用函数】副作用: 切到 validate/ 目录, 使相对路径/子脚本定位生效

    # Step 1: 逐平台采集 (任一失败不阻塞其他)
    for platform in args.platforms:
        run_step(
            f"Collect from {platform}",  # 【调用函数】同文件: 调 batch_collect.py 采集单平台
            [
                "python",
                "batch_collect.py",
                "--platform",
                platform,
                "--per-kw",
                str(args.per_kw),
                "--max-detail",
                str(args.max_detail),
            ],
            blocking=False,
        )

    # Step 2: 聚合情绪 (从所有 batch_*.jsonl 汇总)
    run_step("Aggregate sentiment", ["python", "trend_aggregator.py"], blocking=False)  # 【调用函数】同文件: 聚合各品种情感时序

    # Step 3: 拉取价格
    run_step("Fetch prices", ["python", "price_fetcher.py"], blocking=False)  # 【调用函数】同文件: 拉取 akshare 品种价格

    # Step 4: 生成看板
    run_step("Build dashboard", ["python", "dashboard.py"], blocking=False)  # 【调用函数】同文件: 生成离线看板 HTML

    print(f"\n{'=' * 50}")
    print("  每日更新完成!")
    print("  看板: output/trends/dashboard.html")
    print(f"{'=' * 50}")
