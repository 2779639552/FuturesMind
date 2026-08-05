"""
模块5：事件与关联发现 (P1)
— 品种共现矩阵、情感时序异常、爆款笔记品种触发
"""

import pandas as pd
import plotly.graph_objects as go
from collections import Counter, defaultdict
from itertools import combinations
from report_utils import (
    load_data, expand_varieties, terminal_table,
    chart_to_html, stat_cards_html, dataframe_to_html,
    SENTIMENT_LABELS, get_sector
)


def analyze(df: pd.DataFrame) -> dict:
    """事件发现与关联分析"""
    vdf = expand_varieties(df)
    vdf_valid = vdf[vdf["variety_name"] != ""].copy()

    # === 1. 品种共现矩阵 ===
    # 统计每篇笔记中同时出现的品种对
    cooccur = Counter()
    variety_notes = defaultdict(set)  # 品种→笔记ID集合

    for _, row in df.iterrows():
        vnames = [v.get("name", "") for v in (row.get("varieties") or []) if v.get("name")]
        note_id = row["note_id"]
        for v in vnames:
            variety_notes[v].add(note_id)
        # 品种对
        for a, b in combinations(sorted(set(vnames)), 2):
            cooccur[(a, b)] += 1

    # 共现矩阵 Top
    top_pairs = cooccur.most_common(15)
    text = ""
    if top_pairs:
        # Jaccard 相似度
        jaccard_pairs = []
        for (a, b), cnt in cooccur.items():
            union = len(variety_notes[a] | variety_notes[b])
            jac = cnt / union if union > 0 else 0
            if cnt >= 2:  # 至少共现2次
                jaccard_pairs.append((a, b, cnt, round(jac, 3)))

        jaccard_pairs.sort(key=lambda x: x[3], reverse=True)

        text = f"{'='*70}\n"
        text += f"  品种共现分析\n"
        text += f"{'='*70}"

        # 按频次
        text += f"\n\n  共现频次 Top 15:"
        for (a, b), cnt in top_pairs:
            text += f"\n    {a} ↔ {b}: {cnt}次"

        # 按Jaccard
        text += f"\n\n  共现紧密度 (Jaccard) Top 10:"
        for a, b, cnt, jac in jaccard_pairs[:10]:
            text += f"\n    {a} ↔ {b}: {jac:.3f} (共{cnt}次)"

    # === 2. 爆款笔记分析 ===
    high_eng = df.nlargest(10, "engagement")
    text += f"\n\n{'='*70}"
    text += f"\n  爆款笔记 (Top 10 互动)\n"
    text += f"{'='*70}"

    for i, (_, row) in enumerate(high_eng.iterrows()):
        title = (row["title"] or row.get("desc", ""))[:50]
        vnames = ", ".join(row.get("variety_names", [])[:4])
        text += f"\n  {i+1}. [{row['engagement']}互动] {title}"
        text += f"\n     品种: {vnames or '无'} | 情感: {SENTIMENT_LABELS.get(row['sentiment'], row['sentiment'])} | 作者: {row['author_name']}"

    # 爆款涉及的品种
    hot_varieties = Counter()
    for _, row in high_eng.iterrows():
        for v in (row.get("variety_names") or []):
            hot_varieties[v] += 1
    text += f"\n\n  爆款集中品种: {', '.join(f'{v}({c})' for v, c in hot_varieties.most_common(5))}"

    # === 3. 情感时序异常检测 ===
    text += f"\n\n{'='*70}"
    text += f"  情感时序分析\n"
    text += f"{'='*70}"

    # 按日期聚合
    df["date"] = df["dt"].dt.date
    daily_sent = df.groupby("date").agg(
        avg_sentiment=("sentiment_score", "mean"),
        count=("note_id", "count"),
        std_sentiment=("sentiment_score", "std"),
    ).dropna().reset_index()

    if len(daily_sent) >= 3:
        # 简单异常检测：情感偏离均值 > 1.5 标准差
        overall_mean = daily_sent["avg_sentiment"].mean()
        overall_std = daily_sent["avg_sentiment"].std()
        anomalies = daily_sent[
            (daily_sent["avg_sentiment"] - overall_mean).abs() > 1.5 * overall_std
        ] if overall_std > 0 else pd.DataFrame()

        text += f"\n  日期跨度: {daily_sent['date'].min()} ~ {daily_sent['date'].max()}"
        text += f"\n  日均情感: {overall_mean:+.3f} (σ={overall_std:.3f})"

        if len(anomalies) > 0:
            text += f"\n  异常日期 ({len(anomalies)}天):"
            for _, r in anomalies.iterrows():
                direction = "[偏多]异常" if r["avg_sentiment"] > overall_mean else "[偏空]异常"
                text += f"\n    {r['date']}: 情感{r['avg_sentiment']:+.3f} ({r['count']}条) {direction}"
        else:
            text += f"\n  未检测到显著异常日期"
    else:
        text += f"\n  数据天数不足 ({len(daily_sent)}天), 无法做时序分析"

    # ================================================================
    # HTML 图表
    # ================================================================

    charts_html = ""

    charts_html += stat_cards_html({
        "品种对总数": str(len(cooccur)),
        "最强关联": f"{top_pairs[0][0][0]}↔{top_pairs[0][0][1]}" if top_pairs else "N/A",
        "共现次数": str(top_pairs[0][1]) if top_pairs else "N/A",
        "异常日": str(len(anomalies)) if len(daily_sent) >= 3 else "N/A",
    })

    # Chart 1: 共现网络 (简化版: 用热力图表示)
    top_v = list(variety_notes.keys())
    # 取提及最多的15个品种
    vc = vdf_valid["variety_name"].value_counts()
    top15_v = vc.head(15).index.tolist()

    if len(top15_v) >= 3:
        # 构建共现矩阵
        matrix = []
        for va in top15_v:
            row = []
            for vb in top15_v:
                if va == vb:
                    row.append(0)
                else:
                    key = (min(va, vb), max(va, vb))
                    row.append(cooccur.get(key, 0))
            matrix.append(row)

        fig1 = go.Figure(data=go.Heatmap(
            z=matrix, x=top15_v, y=top15_v,
            colorscale="YlOrRd",
            text=[[str(v) if v > 0 else "" for v in row] for row in matrix],
            texttemplate="%{text}",
        ))
        fig1.update_layout(
            title="品种共现热力图 (同一篇笔记中同时出现)",
        )
        charts_html += chart_to_html(fig1, "cooccur_heatmap", 450)

    # Chart 2: 情感时序
    if len(daily_sent) >= 2:
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=daily_sent["date"], y=daily_sent["count"],
            name="笔记数", marker_color="#bdc3c7", marker_opacity=0.5,
        ))
        fig2.add_trace(go.Scatter(
            x=daily_sent["date"], y=daily_sent["avg_sentiment"],
            mode="lines+markers", name="日均情感", yaxis="y2",
            marker=dict(size=10, color="#e74c3c",
                        colorscale=[[0, "#27ae60"], [0.5, "#95a5a6"], [1, "#e74c3c"]]),
            line=dict(width=3),
        ))
        # Anomaly markers
        if len(daily_sent) >= 3 and len(anomalies) > 0:
            fig2.add_trace(go.Scatter(
                x=anomalies["date"], y=anomalies["avg_sentiment"],
                mode="markers", name="异常", yaxis="y2",
                marker=dict(size=16, color="#e74c3c", symbol="x", line=dict(width=2)),
            ))
        fig2.update_layout(
            title="情感时序 (柱=笔记量, 线=日均情感)",
            xaxis_title="日期",
            yaxis=dict(title="笔记数"),
            yaxis2=dict(title="日均情感分数", overlaying="y", side="right", range=[-1, 1]),
        )
        charts_html += chart_to_html(fig2, "sentiment_timeline", 350)

    # Chart 3: 爆款笔记
    if len(high_eng) > 0:
        fig3 = go.Figure()
        top8 = high_eng.head(8)
        fig3.add_trace(go.Bar(
            y=top8["title"].str[:30],
            x=top8["engagement"],
            orientation="h",
            marker_color=[
                "#e74c3c" if s in ("strong_bullish", "bullish") else
                "#27ae60" if s in ("strong_bearish", "bearish") else "#f39c12"
                for s in top8["sentiment"]
            ],
            text=top8["engagement"].astype(int),
            textposition="outside",
        ))
        fig3.update_layout(
            title="爆款笔记 Top 8 (红=多, 绿=空, 橙=中性)",
            xaxis_title="互动总分", yaxis_title="",
        )
        charts_html += chart_to_html(fig3, "hot_notes", 350)

    return {
        "text": text,
        "html": f"""
        {charts_html}
        """
    }
