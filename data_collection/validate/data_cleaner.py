"""
数据清洗模块 — 处理缺失值/异常值/范围校验

在聚合之前调用: cleaned = clean_records(raw_records)
"""

from datetime import datetime, timedelta  # 【调用包】时间边界判断(未来/远古日期过滤)


# 【功能】清洗记录列表: 过滤无效数据并修复缺失值(日期/空内容/情感分/粉丝数/互动数)。
# 【参数】records: 原始记录列表; verbose: 是否打印清洗统计。
# 【返回】清洗后的记录列表(就地修改 rec 并追加到新列表)。
# 【关键】日期缺失/未来/远古 → 跳过; 空内容 → 跳过; sentiment_score 截断到 [-1,1];
#        author_fans 限制在 [0, 1亿]; like/comment/share 缺失填 0。
def clean_records(records: list[dict], verbose: bool = True) -> list[dict]:
    """清洗记录列表，过滤无效数据，修复缺失值。

    Checks:
      1. 必要字段缺失 → 跳过
      2. publish_time 异常(未来/太久远) → 跳过
      3. sentiment_score 超出[-1,1] → 截断
      4. author_fans 异常值(负数/过大) → 修正
      5. like_count/comment_count 缺失 → 填0
      6. 空标题+空内容 → 跳过
    """
    stats = {  # 【变量】清洗统计计数(各类跳过/修复/保留数量)
        "total": len(records),
        "skipped_no_date": 0,
        "skipped_future_date": 0,
        "skipped_ancient_date": 0,
        "skipped_empty_content": 0,
        "fixed_sentiment": 0,
        "fixed_fans": 0,
        "filled_engagement": 0,
        "kept": 0,
    }

    cleaned = []
    now = datetime.now()
    max_future = now + timedelta(days=1)  # 【变量】未来时间上界(允许 1 天时区偏移)
    min_valid_date = "2015-01-01"  # 【变量】最早有效日期(2015 前无中国社交媒体期货内容)

    for _i, rec in enumerate(records):
        # --- 1. Date validation ---
        pub_time = (rec.get("publish_time", "") or "").strip()
        if not pub_time or len(pub_time) < 8:
            stats["skipped_no_date"] += 1
            continue

        try:
            pub_date = pub_time[:10]  # 【变量】发布日期(yyyy-mm-dd 前 10 位)
            if pub_date > max_future.strftime("%Y-%m-%d"):
                stats["skipped_future_date"] += 1
                continue
            if pub_date < min_valid_date:
                stats["skipped_ancient_date"] += 1
                continue
        except Exception:
            stats["skipped_no_date"] += 1
            continue

        # --- 2. Empty content ---
        title = (rec.get("title", "") or "").strip()
        content = (rec.get("content", "") or rec.get("desc", "") or "").strip()
        if not title and not content:
            stats["skipped_empty_content"] += 1
            continue

        # --- 3. Sentiment score range [-1, 1] ---
        score = rec.get("sentiment_score")  # 【变量】情感分(超界截断到 [-1, 1])
        if score is not None:
            try:
                score = float(score)
                if score > 1.0:
                    score = 1.0
                    stats["fixed_sentiment"] += 1
                elif score < -1.0:
                    score = -1.0
                    stats["fixed_sentiment"] += 1
            except (ValueError, TypeError):
                score = 0.0
            rec["sentiment_score"] = round(score, 3)
        else:
            rec["sentiment_score"] = 0.0

        # --- 4. Author fans (non-negative, reasonable max) ---
        fans = rec.get("author_fans", 0)  # 【变量】作者粉丝数(负数归零, 超 1 亿截断)
        try:
            fans = int(fans) if fans else 0
            if fans < 0:
                fans = 0
                stats["fixed_fans"] += 1
            elif fans > 100_000_000:  # 1亿粉丝 — unrealistic for a single platform
                fans = 100_000_000
                stats["fixed_fans"] += 1
        except (ValueError, TypeError):
            fans = 0
        rec["author_fans"] = fans

        # --- 5. Engagement counts (fill missing with 0) ---
        for field in ["like_count", "comment_count", "share_count"]:
            val = rec.get(field)
            if val is None or val == "":
                rec[field] = 0
                stats["filled_engagement"] += 1
            else:
                try:
                    rec[field] = int(val)
                except (ValueError, TypeError):
                    rec[field] = 0
                    stats["filled_engagement"] += 1

        cleaned.append(rec)
        stats["kept"] += 1

    if verbose:
        print(f"Data cleaning: {stats['total']} records → {stats['kept']} kept")
        if stats["skipped_no_date"]:
            print(f"  [SKIP] {stats['skipped_no_date']} skipped (no publish_time)")
        if stats["skipped_future_date"]:
            print(f"  [SKIP] {stats['skipped_future_date']} skipped (future date)")
        if stats["skipped_empty_content"]:
            print(f"  [SKIP] {stats['skipped_empty_content']} skipped (empty content)")
        if stats["fixed_sentiment"]:
            print(f"  [FIX] {stats['fixed_sentiment']} sentiment scores clamped to [-1,1]")
        if stats["fixed_fans"]:
            print(f"  [FIX] {stats['fixed_fans']} fan counts fixed")
        if stats["filled_engagement"]:
            print(f"  [FIX] {stats['filled_engagement']} engagement fields filled with 0")

    return cleaned
