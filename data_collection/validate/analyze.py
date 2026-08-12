"""
期货社交媒体数据分析 — 主入口
===============================
编排5个分析模块，输出终端摘要 + HTML交互报告

使用:
  python analyze.py output/batch_20260716_084428.jsonl
  python analyze.py output/batch_20260716_084428.jsonl --no-html   # 仅终端输出
  python analyze.py output/batch_20260716_084428.jsonl -m 1 2     # 仅运行模块1,2
"""

import argparse  # 【调用包】命令行参数解析(input/--no-html/-m)
import os  # 【调用包】检查输入 JSONL 文件是否存在
import sys  # 【调用包】文件不存在时退出 / 修改 sys.path
import time  # 【调用包】模块运行耗时统计
from datetime import datetime  # 【调用包】HTML 报告文件名时间戳
from pathlib import Path  # 【调用包】取 __file__ 父目录加入 sys.path

# Add self to path
sys.path.insert(0, str(Path(__file__).parent))  # 【调用函数】副作用: 将本文件目录加入 sys.path, 使同目录模块可直接 import

from report_utils import REPORT_DIR, generate_html_report, load_data  # 【调用包】跨文件(report_utils): 数据加载/HTML 报告生成/报告目录


# 【功能】CLI 主入口: 加载数据 → 依次运行指定分析模块 → 终端输出 + 生成 HTML 报告。
# 【关键】模块经 __import__ 动态加载并调用 analyze(df); 终端打印做 GBK 编码容错。
def main():
    parser = argparse.ArgumentParser(
        description="期货社交媒体数据分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
分析模块:
  1 = 品种热度与情绪仪表盘 (variety_dashboard)
  2 = 情感深度分析 (sentiment_deep)
  3 = 作者影响力分析 (author_analysis)
  4 = 内容策略分析 (content_analysis)
  5 = 事件与关联发现 (event_discovery)

示例:
  python analyze.py output/batch_20260716_084428.jsonl
  python analyze.py data.jsonl --no-html
  python analyze.py data.jsonl -m 1 2 5
        """,
    )
    parser.add_argument("input", help="JSONL数据文件路径")
    parser.add_argument("--no-html", action="store_true", help="仅输出终端文本, 不生成HTML报告")
    parser.add_argument(
        "-m",
        "--modules",
        type=int,
        nargs="+",
        default=[1, 2, 3, 4, 5],
        help="运行的分析模块编号 (默认全部: 1 2 3 4 5)",
    )

    args = parser.parse_args()

    # Validate input
    if not os.path.exists(args.input):
        print(f"错误: 文件不存在: {args.input}")
        sys.exit(1)

    print(f"\n{'=' * 70}")
    print("  期货社交媒体数据分析")
    print(f"{'=' * 70}")
    print(f"  数据文件: {args.input}")
    print(f"  分析模块: {args.modules}")
    print(f"  输出模式: {'终端' if args.no_html else '终端 + HTML报告'}")
    print()

    # Load data
    t0 = time.time()  # 【变量】起始时间戳(统计总耗时)
    print("加载数据...")
    df = load_data(args.input)  # 【调用函数】跨文件(report_utils): 从 JSONL 加载并构造 DataFrame
    print(f"  已加载 {len(df)} 条笔记\n")
    print(f"  加载耗时: {time.time() - t0:.1f}s")

    # Module registry
    module_names = {  # 【变量】模块编号→(中文名, 文件名) 注册表, 供 -m 选择运行
        1: ("品种热度与情绪仪表盘", "variety_dashboard"),
        2: ("情感深度分析", "sentiment_deep"),
        3: ("作者影响力分析", "author_analysis"),
        4: ("内容策略分析", "content_analysis"),
        5: ("事件与关联发现", "event_discovery"),
    }

    results = {}  # 【变量】各模块返回结果(以模块编号为键)
    html_sections = []  # 【变量】HTML 报告的各模块分区列表(供 generate_html_report 拼装)

    for mod_id in args.modules:
        if mod_id not in module_names:
            print(f"  警告: 未知模块 {mod_id}, 跳过")
            continue

        mod_label, mod_file = module_names[mod_id]
        print(f"\n[{mod_id}/5] 运行: {mod_label}...")

        try:
            mod = __import__(mod_file)  # 【调用函数】动态导入分析模块(品种仪表盘/情感深度/作者/内容/事件)
            t1 = time.time()
            result = mod.analyze(df)  # 【调用函数】调用分析模块统一入口 analyze(df)
            elapsed = time.time() - t1
            results[mod_id] = result
            print(f"  完成 ({elapsed:.1f}s)")

            # Print terminal output (safe for Windows GBK)
            if result.get("text"):
                try:
                    print(result["text"])
                except UnicodeEncodeError:
                    # Strip non-GBK characters for Windows console
                    safe_text = result["text"].encode("gbk", errors="replace").decode("gbk")
                    print(safe_text)
                    print("  (部分特殊字符已替换)")

            # Collect HTML
            if not args.no_html and result.get("html"):
                html_sections.append(
                    {"title": f"模块{mod_id}: {mod_label}", "content": result["html"]}
                )

        except Exception as e:
            print(f"  错误: {e}")
            import traceback  # 【调用包】打印模块运行异常堆栈(按需导入)

            traceback.print_exc()  # 【调用函数】输出完整异常堆栈便于排查

    # Generate HTML report
    if not args.no_html and html_sections:
        print(f"\n{'=' * 70}")
        print("  生成HTML报告...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(REPORT_DIR / f"analysis_report_{ts}.html")  # 【变量】报告输出路径(带时间戳)
        generate_html_report(html_sections, len(df), output_path)  # 【调用函数】跨文件(report_utils): 拼装并写出 HTML 报告
        print(f"  报告: {output_path}")

    # Final summary
    total_time = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"  分析完成 | 总耗时: {total_time:.1f}s | 笔记: {len(df)}条")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
