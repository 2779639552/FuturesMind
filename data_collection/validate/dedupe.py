"""
跨批次去重工具
=============
按 (platform, note_id) 去重，后读覆盖先读（同一笔记取最新互动数据）。
修复 trend_aggregator 中跨 batch 文件重复计数的 bug。
"""

import json  # 【调用包】按行解析 JSONL 记录(json.loads)
import os  # 【调用包】检查文件是否存在
from pathlib import Path  # 【调用包】展示文件名(Path(path).name)


# 【功能】读取多个 JSONL 文件并按 dedup_key 去重, 后读覆盖先读(同 key 取最新互动数据)。
# 【参数】paths: JSONL 路径列表(按字母序升序读取, 含时间戳保证时间顺序); dedup_key: 去重字段, 默认 "note_id"; verbose: 是否打印统计。
# 【返回】去重后的记录列表; 无 ID 记录用 "__no_id_序号" 占位 key 保留。
# 【关键】空行/解析失败行跳过; 同名 key 后出现的记录覆盖先出现。
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
    seen: dict[str, dict] = {}  # 【变量】{去重key: 记录} 字典(后读覆盖先读)
    total_read = 0  # 【变量】总读取行数(用于计算去重数)
    parse_errors = 0  # 【变量】JSON 解析失败行数

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
                    record = json.loads(line)  # 【调用函数】解析单行 JSON(解析失败跳过)
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


# 【功能】兼容旧数据的去重加载: key = (platform, note_id), 无 platform 字段的旧记录默认 "xhs"。
# 【参数】paths: JSONL 路径列表; verbose: 是否打印去重统计。
# 【返回】去重后的记录列表(跨平台去重, 平台不同视为不同记录)。
# 【关键】修复 trend_aggregator 跨批次重复计数 bug; 后读覆盖先读, 同 key 取最新数据。
def load_unique_records_with_platform_fallback(
    paths: list[str],
    verbose: bool = True,
) -> list[dict]:
    """
    兼容旧数据的去重加载：
    - 有 platform 字段的记录 key = (platform, note_id)
    - 无 platform 字段的旧记录默认 platform="xhs"，key = ("xhs", note_id)
    """
    seen: dict[tuple, dict] = {}  # 【变量】{(平台, note_id): 记录} 字典(平台不同视为不同记录)
    total_read = 0  # 【变量】总读取行数(用于计算去重数)
    parse_errors = 0  # 【变量】JSON 解析失败行数

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
                    record = json.loads(line)  # 【调用函数】解析单行 JSON(解析失败跳过)
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
        platform_counts: dict[str, int] = {}  # 【变量】各平台去重后记录数(打印平台分布)
        for rec in seen.values():
            p = rec.get("platform", "xhs")
            platform_counts[p] = platform_counts.get(p, 0) + 1
        plat_summary = ", ".join(f"{p}: {c}" for p, c in sorted(platform_counts.items()))
        print(f"  Total: {total_read} records → {len(seen)} unique ({dupes_removed} dupes)")
        print(f"  By platform: {plat_summary}")
        if parse_errors:
            print(f"  ⚠️ {parse_errors} parse errors skipped")

    return list(seen.values())
