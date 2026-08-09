"""
期货社交媒体数据分析 — 主入口
===============================
编排5个分析模块，输出终端摘要 + HTML交互报告

使用:
  python analyze.py output/batch_20260716_084428.jsonl
  python analyze.py output/batch_20260716_084428.jsonl --no-html   # 仅终端输出
  python analyze.py output/batch_20260716_084428.jsonl -m 1 2     # 仅运行模块1,2
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add self to path
sys.path.insert(0, str(Path(__file__).parent))

from report_utils import REPORT_DIR, generate_html_report, load_data


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
    t0 = time.time()
    print("加载数据...")
    df = load_data(args.input)
    print(f"  已加载 {len(df)} 条笔记\n")
    print(f"  加载耗时: {time.time() - t0:.1f}s")

    # Module registry
    module_names = {
        1: ("品种热度与情绪仪表盘", "variety_dashboard"),
        2: ("情感深度分析", "sentiment_deep"),
        3: ("作者影响力分析", "author_analysis"),
        4: ("内容策略分析", "content_analysis"),
        5: ("事件与关联发现", "event_discovery"),
    }

    results = {}
    html_sections = []

    for mod_id in args.modules:
        if mod_id not in module_names:
            print(f"  警告: 未知模块 {mod_id}, 跳过")
            continue

        mod_label, mod_file = module_names[mod_id]
        print(f"\n[{mod_id}/5] 运行: {mod_label}...")

        try:
            mod = __import__(mod_file)
            t1 = time.time()
            result = mod.analyze(df)
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
            import traceback

            traceback.print_exc()

    # Generate HTML report
    if not args.no_html and html_sections:
        print(f"\n{'=' * 70}")
        print("  生成HTML报告...")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(REPORT_DIR / f"analysis_report_{ts}.html")
        generate_html_report(html_sections, len(df), output_path)
        print(f"  报告: {output_path}")

    # Final summary
    total_time = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"  分析完成 | 总耗时: {total_time:.1f}s | 笔记: {len(df)}条")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
