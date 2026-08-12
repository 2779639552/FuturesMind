"""
情绪时序聚合: JSONL → 按(品种, 日期)聚合情感分数
==============================================
v3: 作者影响力加权 — 粉丝数(log压缩) + 领域发帖量 + 互动数三方融合
    - author_fans: 粉丝数对数压缩, 500万粉→~2.3x, 100粉→~1.0x
    - author_volume: 该作者在数据集中发帖数越多, 领域专注度越高
    - engagement: 点赞+评论×2, 开根号压缩

输出: output/trends/{variety}_sentiment.json
"""

import json  # 【调用包】读写品种情感时序 JSON 与回测权重 JSON
import math  # 【调用包】log/log10 压缩作者粉丝数与发帖量权重
import sys  # 【调用包】__main__ 中无数据文件时退出
from collections import defaultdict  # 【调用包】按品种/日期/平台聚合的默认字典
from pathlib import Path  # 【调用包】跨平台路径处理(OUTPUT_DIR)

from dedupe import load_unique_records_with_platform_fallback  # 【调用包】跨文件(dedupe): 多 JSONL 去重加载(带平台回退)

OUTPUT_DIR = Path(__file__).parent / "output" / "trends"  # 【变量】聚合时序结果输出目录


# 【功能】全数据集扫描构建作者画像索引(发帖量/总互动/最大粉丝/分品种发帖数/均互动)。
# 【参数】records: 去重清洗后的笔记记录列表。
# 【返回】{author_id: {total_posts, total_engagement, total_fans, name, variety_posts, avg_engagement}}。
# 【关键】粉丝数取多次出现的最大值(防止单次缺失); 分品种发帖数为领域专注度依据。
def _build_author_index(records: list[dict]) -> dict:
    """构建全数据集作者画像索引。

    返回: {author_id: {total_posts, total_fans, avg_engagement, ...}}
    """
    author_index: dict[str, dict] = {}

    for note in records:
        aid = note.get("author_id", "")
        if not aid:
            continue

        engagement = (  # 【变量】单条笔记互动分(评论×2、分享×0.5 加权)
            (note.get("like_count") or 0)
            + (note.get("comment_count") or 0) * 2
            + (note.get("share_count") or 0) * 0.5
        )
        fans = note.get("author_fans", 0) or 0

        if aid not in author_index:
            author_index[aid] = {
                "total_posts": 0,
                "total_engagement": 0,
                "total_fans": 0,
                "name": note.get("author_name", ""),
                "variety_posts": {},  # per-variety post counts
            }

        author_index[aid]["total_posts"] += 1
        author_index[aid]["total_engagement"] += engagement
        # Track per-variety posts
        varieties = note.get("varieties", [])
        if isinstance(varieties, list):
            for v in varieties:
                vname = v["name"] if isinstance(v, dict) else str(v)
                author_index[aid]["variety_posts"][vname] = (
                    author_index[aid]["variety_posts"].get(vname, 0) + 1
                )
        # 取多次出现的最大粉丝数 (防止单次未取到)
        if fans > author_index[aid]["total_fans"]:
            author_index[aid]["total_fans"] = fans

    # 计算均值
    for aid in author_index:
        info = author_index[aid]
        info["avg_engagement"] = (
            info["total_engagement"] / info["total_posts"] if info["total_posts"] > 0 else 0
        )

    return author_index


# 【功能】计算单条笔记的综合权重 = 互动权重 × 作者粉丝权重 × 作者品种专注度权重。
# 【参数】note: 笔记记录; author_index: _build_author_index 的产物。
# 【返回】float 权重(任一维度缺失时取 1.0, 三项独立相乘)。
# 【关键】粉丝数用 log10、专注度用 ln 压缩, 均设上限防大 V/爆款主导。
def _compute_note_weight(
    note: dict,
    author_index: dict[str, dict],
) -> float:
    """计算单条笔记的综合权重。

    权重 = 互动权重 × 作者粉丝权重 × 作者领域权重
    三项独立相乘, 任何一项缺失均不影响其他维度。
    """
    # --- 1. 互动权重 (per-post buzz, capped to prevent viral dominance) ---
    engagement = (  # 【变量】单条笔记互动分(评论×2、分享×0.5 加权)
        (note.get("like_count") or 0)
        + (note.get("comment_count") or 0) * 2
        + (note.get("share_count") or 0) * 0.5
    )
    engagement_weight = 1.0 + (engagement**0.25)  # gentler exponent
    engagement_weight = min(engagement_weight, 2.0)  # cap at 2x

    # --- 2. 作者粉丝权重 (log10, 更高区分度) ---
    author_fans = note.get("author_fans", 0) or 0
    if author_fans > 0:
        # log10: 100粉→1.24x, 1K粉→1.36x, 1万粉→1.48x,
        #        10万粉→1.60x, 100万粉→1.72x, 1000万粉→1.84x
        fans_weight = 1.0 + math.log10(1 + author_fans) * 0.12
        fans_weight = min(fans_weight, 2.5)  # raised cap
    else:
        fans_weight = 1.0

    # --- 3. 作者品种专注度权重 (在该品种上发帖越多→越专业) ---
    aid = note.get("author_id", "")
    author_info = author_index.get(aid, {})
    # Use per-variety post count if available, otherwise fall back to total
    varieties = note.get("varieties", [])
    variety_name = ""
    if isinstance(varieties, list) and len(varieties) > 0:
        v0 = varieties[0]
        variety_name = v0["name"] if isinstance(v0, dict) else str(v0)
    variety_posts = author_info.get("variety_posts", {}).get(variety_name, 0) if variety_name else 0
    # Use variety count if >1, otherwise use total (with floor of 1)
    effective_posts = max(variety_posts, 1) if variety_name else author_info.get("total_posts", 1)
    if effective_posts > 1:
        volume_weight = 1.0 + math.log(effective_posts) * 0.08
        volume_weight = min(volume_weight, 1.4)
    else:
        volume_weight = 1.0

    # --- 最终权重 ---
    final_weight = engagement_weight * fans_weight * volume_weight

    return final_weight


# 【功能】主聚合: 多 JSONL → 按(品种, 日期)聚合加权情感分数, 输出每品种 *_sentiment.json 与汇总索引。
# 【参数】jsonl_paths: 采集数据 JSONL 文件路径列表。
# 【返回】{品种: {"series": [...], "stats": {...}}}; 无有效记录时返回空 dict。
# 【关键】链式流程: 去重 → 清洗 → 作者画像 → 按日加权平均; 存在回测权重时追加 weighted_score。
def aggregate(jsonl_paths: list[str]) -> dict:
    """聚合多个JSONL文件的情感时序数据 (多平台, 去重, 作者加权)"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 去重加载
    records = load_unique_records_with_platform_fallback(jsonl_paths, verbose=True)  # 【调用函数】跨文件(dedupe): 多批次去重加载, 后读覆盖先读
    if not records:
        print("No records to aggregate!")
        return {}

    # 数据清洗: 缺失值/异常值处理
    from data_cleaner import clean_records  # 【调用包】跨文件(data_cleaner): 清洗缺失/异常值

    records = clean_records(records, verbose=True)  # 【调用函数】跨文件(data_cleaner): 过滤无效日期/空内容, 截断异常分

    # 构建作者画像索引 (全数据集, 一次扫描)
    author_index = _build_author_index(records)  # 【调用函数】同文件: 一次扫描构建作者画像

    # 作者统计摘要
    total_authors = len(author_index)
    authors_with_fans = sum(1 for a in author_index.values() if a["total_fans"] > 0)
    multi_post_authors = sum(1 for a in author_index.values() if a["total_posts"] > 1)
    print(
        f"Author index: {total_authors} unique authors, "
        f"{authors_with_fans} with fan data, "
        f"{multi_post_authors} with >1 posts"
    )

    # {variety: {date: [scores]}}
    daily = defaultdict(lambda: defaultdict(list))  # 【变量】{品种: {日期: [含 score/weight 的记录]}} 原始聚合容器
    note_counts = defaultdict(lambda: defaultdict(int))  # 【变量】{品种: {日期: 笔记数}}
    platform_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # 【变量】{品种: {日期: {平台: 条数}}}

    # 作者维度统计 (用于 audit trail)
    author_variety_sentiment: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))  # 【变量】{作者: {品种: [情感分]}} 审计用

    for note in records:
        date = (note.get("publish_time", "") or "")[:10]
        if not date:
            continue

        platform = note.get("platform", "xhs")
        aid = note.get("author_id", "")

        # 计算综合权重 (v3: 包含作者影响力)
        weight = _compute_note_weight(note, author_index)  # 【调用函数】同文件: 互动×粉丝×专注度三项连乘权重

        # 品种级情感
        for vs in note.get("variety_sentiments", []) or []:
            vname = vs.get("variety", "")
            score = vs.get("score", 0)
            if vname and score != 0:
                daily[vname][date].append(
                    {
                        "score": score,
                        "weight": weight,
                        "sentiment": vs.get("sentiment", "neutral"),
                        "engagement": (
                            (note.get("like_count") or 0) + (note.get("comment_count") or 0) * 2
                        ),
                        "author_fans": note.get("author_fans", 0) or 0,
                        "author_id": aid,
                        "platform": platform,
                    }
                )
                note_counts[vname][date] += 1
                platform_counts[vname][date][platform] += 1

                # 记录作者-品种情感 (audit)
                if aid and score != 0:
                    author_variety_sentiment[aid][vname].append(score)

    # 写入每个品种的JSON
    result = {}
    for variety, dates in sorted(daily.items()):
        # ---- 加载回测权重 ----
        global_weights_path = OUTPUT_DIR / "_global_weights.json"
        variety_weights_path = OUTPUT_DIR / f"{variety}_weights.json"
        platform_weights: dict[str, float] = {}  # 【变量】平台权重(来自 backtest_weights 回测产物)
        weight_source = "equal"  # 【变量】权重来源说明(equal / global_backtest / single_platform / fallback_equal)
        combined_metrics: dict = {}  # 【变量】品种级回测指标(方向准确率/Pearson r/样本量)
        if global_weights_path.exists():
            try:
                with open(global_weights_path, encoding="utf-8") as wf:
                    wdata = json.load(wf)  # 【调用函数】落盘读取: 全局平台回测权重
                platform_weights = wdata.get("weights", {})
                weight_source = wdata.get("weight_source", "equal")
            except Exception:
                pass
        if variety_weights_path.exists():
            try:
                with open(variety_weights_path, encoding="utf-8") as vf:
                    vdata = json.load(vf)  # 【调用函数】落盘读取: 该品种回测指标
                combined_metrics = vdata.get("combined_metrics", {})
            except Exception:
                pass

        series = []
        for date in sorted(dates.keys()):
            items = dates[date]

            # 加权平均 (全平台, 含作者影响力)
            total_weight = sum(it["weight"] for it in items)  # 【变量】当日全部记录的权重和(加权平均分母)
            avg_score = (
                sum(it["score"] * it["weight"] for it in items) / total_weight
                if total_weight > 0
                else 0
            )  # 【变量】按权重加权的当日综合情感分

            # 简单平均 (对照, 不加权)
            simple_avg = sum(it["score"] for it in items) / len(items)  # 【变量】当日简单平均情感分(对照基准)

            sentiments = [it["sentiment"] for it in items]
            bull = sum(1 for s in sentiments if "bull" in s)
            bear = sum(1 for s in sentiments if "bear" in s)

            # ---- 分平台情绪 ----
            by_platform: dict[str, list] = {}
            for it in items:
                p = it.get("platform", "xhs")
                by_platform.setdefault(p, []).append(it)

            platform_scores = {}  # 【变量】分平台加权平均分与多空/条数统计
            for p, pitems in by_platform.items():
                pw_total = sum(i["weight"] for i in pitems)  # 【变量】该平台当日权重和
                pw_avg = (
                    sum(i["score"] * i["weight"] for i in pitems) / pw_total if pw_total > 0 else 0
                )
                p_sents = [i["sentiment"] for i in pitems]
                platform_scores[p] = {
                    "avg_score": round(pw_avg, 3),
                    "note_count": len(pitems),
                    "bull": sum(1 for s in p_sents if "bull" in s),
                    "bear": sum(1 for s in p_sents if "bear" in s),
                }

            # ---- 加权合成 (回测权重) ----
            weighted_score = None  # 【变量】按回测平台权重合成的综合情感分(无权重时为 None)
            if platform_weights and platform_scores:
                w_total = 0.0
                w_sum = 0.0
                for p, pdata in platform_scores.items():
                    w = platform_weights.get(p, 0)
                    w_sum += w * pdata["avg_score"]
                    w_total += w
                if w_total > 0:
                    weighted_score = round(w_sum / w_total, 3)

            # ---- 作者影响力摘要 (该日期的TOP作者) ----
            author_contribs = defaultdict(float)  # 【变量】{作者id: 当日权重贡献和}
            for it in items:
                aid = it.get("author_id", "")
                if aid:
                    author_contribs[aid] += it["weight"]
            top_authors = sorted(author_contribs.items(), key=lambda x: -x[1])[:3]  # 【变量】当日权重贡献前三作者

            # 展示作者名
            author_names = []  # 【变量】TOP 作者的展示信息(昵称/权重贡献/粉丝/发帖数)
            for aid, contrib in top_authors:
                info = author_index.get(aid, {})
                name = info.get("name", aid[:8])
                fans = info.get("total_fans", 0)
                posts = info.get("total_posts", 0)
                label = f"{name}" if not fans else f"{name}({fans / 10000:.0f}万粉)"
                author_names.append(
                    {
                        "name": label,
                        "weight_contrib": round(contrib, 2),
                        "fans": fans,
                        "posts": posts,
                    }
                )

            entry = {  # 【变量】单日情感聚合条目(供时序 series 使用)
                "date": date,
                "avg_score": round(avg_score, 3),
                "simple_avg": round(simple_avg, 3),
                "note_count": note_counts[variety][date],
                "bull_count": bull,
                "bear_count": bear,
                "platform_counts": dict(platform_counts[variety][date]),
                "platform_scores": platform_scores,
                "top_authors": author_names,
            }
            if weighted_score is not None:
                entry["weighted_score"] = weighted_score
            series.append(entry)

        # 计算趋势指标
        if len(series) >= 3:
            recent = [s["avg_score"] for s in series[-5:]]  # 【变量】最近5日的加权情感分(趋势判定窗口)
            trend = (  # 【变量】近5日均值趋势标签: >0.1 偏多 / <-0.1 偏空 / 其余震荡
                "偏多"
                if sum(recent) / len(recent) > 0.1
                else ("偏空" if sum(recent) / len(recent) < -0.1 else "震荡")
            )

            total_by_platform: dict[str, int] = {}
            for s in series:
                for p, c in s.get("platform_counts", {}).items():
                    total_by_platform[p] = total_by_platform.get(p, 0) + c

            # 品种级作者统计
            variety_authors = set()
            for it_list in dates.values():
                for it in it_list:
                    aid = it.get("author_id", "")
                    if aid:
                        variety_authors.add(aid)

            result[variety] = {
                "series": series,
                "stats": {
                    "total_days": len(series),
                    "total_notes": sum(s["note_count"] for s in series),
                    "avg_sentiment": round(sum(s["avg_score"] for s in series) / len(series), 3),
                    "recent_trend": trend,
                    "date_range": f"{series[0]['date']} ~ {series[-1]['date']}",
                    "by_platform": total_by_platform,
                    "platform_weights": platform_weights,
                    "weight_source": weight_source,
                    "combined_metrics": combined_metrics,
                    "unique_authors": len(variety_authors),
                    "authors_with_fans": sum(
                        1
                        for a in variety_authors
                        if author_index.get(a, {}).get("total_fans", 0) > 0
                    ),
                },
            }
        else:
            result[variety] = {"series": series}

        # 写文件
        out_path = OUTPUT_DIR / f"{variety}_sentiment.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result[variety], f, ensure_ascii=False, indent=2)  # 【调用函数】落盘: 写该品种情感时序 JSON

    # 写汇总索引
    index = {v: data.get("stats", {}) for v, data in result.items()}  # 【变量】{品种: 统计摘要} 汇总索引
    with open(OUTPUT_DIR / "_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)  # 【调用函数】落盘: 写 _index.json 汇总索引

    # 写作者画像索引 (audit trail)
    # 仅保留有粉丝数据的作者, 压缩输出
    author_export = {}  # 【变量】审计用作者画像(压缩字段, 仅保留有粉丝/多发作者)
    for aid, info in sorted(author_index.items(), key=lambda x: -x[1]["total_fans"]):
        if info["total_posts"] >= 2 or info["total_fans"] > 0:
            author_export[aid] = {
                "name": info["name"],
                "posts": info["total_posts"],
                "fans": info["total_fans"],
                "avg_engagement": round(info["avg_engagement"], 1),
            }
    if author_export:
        with open(OUTPUT_DIR / "_author_index.json", "w", encoding="utf-8") as f:
            json.dump(author_export, f, ensure_ascii=False, indent=2)  # 【调用函数】落盘: 写作者画像索引
        print(f"Author index saved: {len(author_export)} authors → _author_index.json")

    # 全局平台统计
    global_platform = defaultdict(int)  # 【变量】{平台: 笔记数} 全数据集平台分布
    for record in records:
        global_platform[record.get("platform", "xhs")] += 1
    plat_summary = ", ".join(f"{p}: {c}" for p, c in sorted(global_platform.items()))
    print(f"Aggregated {len(result)} varieties → {OUTPUT_DIR}")
    print(f"Platform distribution: {plat_summary}")

    # 权重来源说明
    print("Weight formula: engagement_weight × fans_weight × volume_weight")
    print("  engagement = 1 + (likes + comments×2 + shares×0.5) ^ 0.3")
    print("  fans       = 1 + ln(1 + followers) × 0.03  (max 1.5x)")
    print("  volume     = 1 + ln(total_posts) × 0.08    (max 1.4x)")

    return result


if __name__ == "__main__":
    import glob  # 【调用包】通配符匹配 output 目录下的 batch_*.jsonl 文件

    paths = sorted(glob.glob(str(Path(__file__).parent / "output" / "batch_*.jsonl")))  # 【调用函数】文件系统通配: 发现所有采集 JSONL
    if not paths:
        print("No batch JSONL files found!")
        sys.exit(1)

    print(f"Found {len(paths)} data files:")
    for p in paths:
        print(f"  {Path(p).name}")
    print()

    aggregate(paths)
