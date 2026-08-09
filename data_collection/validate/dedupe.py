"""
跨批次去重工具
=============
按 (platform, note_id) 去重，后读覆盖先读（同一笔记取最新互动数据）。
修复 trend_aggregator 中跨 batch 文件重复计数的 bug。
"""

import json
import os
from pathlib import Path


def load_unique_records(
    paths: list[str],
    dedup_key: str = "note_id",
    verbose: bool = True,
) -> list[dict]:
    """
    读取多个 JSONL 文件，按 dedup_key 去重。

    按文件名字母序（含时间戳）升序读取，后出现的记录覆盖先出现的。
    空行跳过，解析失败的行跳过并告警。

    Args:
        paths: JSONL 文件路径列表（将按字母序排列，确保时间顺序）
        dedup_key: 去重依据的字段名，默认 "note_id"
        verbose: 是否打印去重统计

    Returns:
        去重后的记录列表
    """
    seen: dict[str, dict] = {}
    total_read = 0
    parse_errors = 0

    for path in sorted(paths):
        if not os.path.exists(path):
            if verbose:
                print(f"  SKIP (not found): {path}")
            continue

        file_lines = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue

                key = record.get(dedup_key, "")
                if not key:
                    # 无 ID 的记录无法去重，直接保留（罕见情况）
                    seen[f"__no_id_{total_read}"] = record
                else:
                    seen[key] = record  # 后读覆盖先读

                total_read += 1
                file_lines += 1

        if verbose and file_lines > 0:
            print(f"  Loaded {file_lines} records from {Path(path).name}")

    dupes_removed = total_read - len(seen)
    if verbose:
        print(f"  Total: {total_read} records → {len(seen)} unique ({dupes_removed} dupes removed)")
        if parse_errors:
            print(f"  ⚠️ {parse_errors} parse errors skipped")

    return list(seen.values())


def load_unique_records_with_platform_fallback(
    paths: list[str],
    verbose: bool = True,
) -> list[dict]:
    """
    兼容旧数据的去重加载：
    - 有 platform 字段的记录 key = (platform, note_id)
    - 无 platform 字段的旧记录默认 platform="xhs"，key = ("xhs", note_id)
    """
    seen: dict[tuple, dict] = {}
    total_read = 0
    parse_errors = 0

    for path in sorted(paths):
        if not os.path.exists(path):
            if verbose:
                print(f"  SKIP (not found): {path}")
            continue

        file_lines = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue

                platform = record.get("platform", "xhs")  # 旧数据默认 xhs
                note_id = record.get("note_id", "")
                if not note_id:
                    note_id = f"__no_id_{total_read}"
                key = (platform, note_id)
                seen[key] = record

                total_read += 1
                file_lines += 1

        if verbose and file_lines > 0:
            print(f"  Loaded {file_lines} records from {Path(path).name}")

    dupes_removed = total_read - len(seen)
    if verbose:
        # 按平台统计
        platform_counts: dict[str, int] = {}
        for rec in seen.values():
            p = rec.get("platform", "xhs")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        plat_summary = ", ".join(f"{p}: {c}" for p, c in sorted(platform_counts.items()))
        print(f"  Total: {total_read} records → {len(seen)} unique ({dupes_removed} dupes)")
        print(f"  By platform: {plat_summary}")
        if parse_errors:
            print(f"  ⚠️ {parse_errors} parse errors skipped")

    return list(seen.values())
