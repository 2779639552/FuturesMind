"""
模块3：作者影响力分析 (P1)
— KOL排行、情感倾向、发文频率、互动集中度
"""

from collections import Counter  # 【调用包】作者主品种/情感倾向分布频次统计

import pandas as pd  # 【调用包】DataFrame 作者聚合(groupby/agg)
import plotly.graph_objects as go  # 【调用包】Plotly 图表对象(影响力柱状图/频率散点)
from report_utils import (  # 【调用包】report_utils: HTML/终端表格/统计卡片渲染
    chart_to_html,
    dataframe_to_html,
    stat_cards_html,
    terminal_table,
)


# 【功能】作者影响力分析: KOL 排行、互动集中度(Gini 系数)、情感倾向分布, 返回终端文本 + HTML。
# 【参数】df: 含 author_name/author_id/互动数/sentiment_score 的笔记 DataFrame。
# 【返回】{"text": 终端报告文本, "html": 图表 HTML 片段}。
def analyze(df: pd.DataFrame) -> dict:
    """作者影响力分析"""
    # === 1. 作者聚合统计 ===
    author_stats = (  # 【变量】按(作者名, id)聚合的互动/情感/主品种统计表
        df.groupby(["author_name", "author_id"])
        .agg(
            post_count=("note_id", "count"),
            total_likes=("like_count", "sum"),
            total_comments=("comment_count", "sum"),
            total_collects=("collect_count", "sum"),
            total_shares=("share_count", "sum"),
            total_engagement=("engagement", "sum"),
            avg_engagement=("engagement", "mean"),
            avg_sentiment_score=("sentiment_score", "mean"),
            avg_confidence=("sentiment_confidence", "mean"),
            top_variety=(
                "variety_names",
                lambda x: Counter([v for vs in x if vs for v in vs]).most_common(1),
            ),
        )
        .reset_index()
    )

    # 互动总分
    author_stats["influence_score"] = (  # 【变量】影响力分 = 总互动 × (1 + |平均情感分|), 情感越极端权重越高
        author_stats["total_engagement"] * (1 + author_stats["avg_sentiment_score"].abs())
    ).round(1)

    author_stats = author_stats.sort_values("influence_score", ascending=False)

    # 情感标签
    # 【功能】把平均情感分映射为中文倾向标签(多/略多/中性/略空/空头)。
    # 【参数】score: 作者平均情感分数。
    # 【返回】倾向字符串(±0.2 强倾向, ±0.05 弱倾向, 中间为中性)。
    def sentiment_label(score):
        if score > 0.2:
            return "多头"
        elif score > 0.05:
            return "略多"
        elif score < -0.2:
            return "空头"
        elif score < -0.05:
            return "略空"
        else:
            return "中性"

    author_stats["bias"] = author_stats["avg_sentiment_score"].apply(sentiment_label)  # 【变量】作者情感倾向标签列

    # === 终端输出 ===
    top_authors = author_stats.head(15)  # 【变量】影响力 Top15 作者(终端排行)
    table_rows = []
    for _, r in top_authors.iterrows():
        name = r["author_name"][:12]
        top_var = r["top_variety"][0][0] if r["top_variety"] else "-"
        table_rows.append(
            [
                name,
                str(r["post_count"]),
                r["bias"],
                str(int(r["total_engagement"])),
                str(int(r["avg_engagement"])),
                f"{r['avg_sentiment_score']:+.2f}",
                f"{r['avg_confidence']:.2f}",
                top_var,
            ]
        )

    text = terminal_table(  # 【调用函数】跨文件(report_utils): 生成终端等宽排行表格
        ["作者", "发文", "倾向", "总互动", "均互动", "情感", "确信", "主品种"],
        table_rows,
        title="作者影响力排行榜 (Top 15)",
        col_widths=[12, 5, 8, 8, 8, 6, 6, 10],
    )

    # === 2. 互动集中度 ===
    total_eng = author_stats["total_engagement"].sum()  # 【变量】全作者总互动量
    top3_pct = (  # 【变量】Top3 作者贡献互动占比(%)
        author_stats.head(3)["total_engagement"].sum() / total_eng * 100 if total_eng > 0 else 0
    )
    top10_pct = (  # 【变量】Top10% 作者贡献互动占比(%)
        author_stats.head(max(1, len(author_stats) // 10))["total_engagement"].sum()
        / total_eng
        * 100
        if total_eng > 0
        else 0
    )

    text += f"\n  总作者数: {len(author_stats)}"
    text += f"\n  总互动量: {total_eng}"
    text += f"\n  Top 3 作者贡献: {top3_pct:.0f}% 互动"
    text += f"\n  Top 10% 作者贡献: {top10_pct:.0f}% 互动"

    # Gini系数近似
    sorted_eng = sorted(author_stats["total_engagement"])  # 【变量】按总互动升序排列(供 Gini 公式使用)
    n = len(sorted_eng)
    if n > 1 and sum(sorted_eng) > 0:
        index = sum((2 * i - n - 1) * sorted_eng[i - 1] for i in range(1, n + 1))  # 【变量】Gini 公式分子(累计差加权)
        gini = index / (n * sum(sorted_eng))  # 【变量】互动集中度 Gini 系数(0=均匀, 1=极度集中)
        text += f"\n  互动集中度 (Gini): {gini:.3f} (0=均匀, 1=极度集中)"

    # === 3. 作者情感分布 ===
    bias_dist = Counter(author_stats["bias"])  # 【变量】作者情感倾向分布(多头/空头人数)
    text += "\n\n  作者情感倾向分布:"
    for bias, count in bias_dist.most_common():
        text += f"\n    {bias}: {count} 人 ({count / len(author_stats) * 100:.0f}%)"

    # ================================================================
    # HTML 图表
    # ================================================================

    charts_html = ""

    charts_html += stat_cards_html(  # 【调用函数】跨文件(report_utils): 生成 HTML 统计卡片
        {
            "作者总数": str(len(author_stats)),
            "Top 3 集中度": f"{top3_pct:.0f}%",
            "Gini 系数": f"{gini:.3f}" if n > 1 else "N/A",
            "最活跃作者": author_stats.iloc[0]["author_name"][:10]
            if len(author_stats) > 0
            else "-",
        }
    )

    # Chart 1: 作者影响力柱状图
    top10 = author_stats.head(10)  # 【变量】影响力 Top10 作者(柱状图数据)
    fig1 = go.Figure()
    colors_author = []  # 【变量】Top10 作者柱色(>0.1 红多 / <-0.1 绿空 / 其余灰)
    for _, r in top10.iterrows():
        if r["avg_sentiment_score"] > 0.1:
            colors_author.append("#e74c3c")
        elif r["avg_sentiment_score"] < -0.1:
            colors_author.append("#27ae60")
        else:
            colors_author.append("#95a5a6")

    fig1.add_trace(
        go.Bar(
            x=top10["author_name"].str[:10],
            y=top10["influence_score"],
            marker_color=colors_author,
            text=top10["influence_score"].round(0).astype(int),
            textposition="outside",
        )
    )
    fig1.update_layout(
        title="作者影响力 Top 10 (颜色=情感倾向, 红多绿空)",
        xaxis_title="作者",
        yaxis_title="影响力分数",
    )
    charts_html += chart_to_html(fig1, "author_influence", 400)  # 【调用函数】跨文件(report_utils): 作者影响力图转内嵌 HTML

    # Chart 2: 发文频率 vs 互动
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=author_stats["post_count"],
            y=author_stats["avg_engagement"],
            mode="markers",
            marker={
                "size": author_stats["total_engagement"].clip(1, 500) ** 0.4,
                "color": author_stats["avg_sentiment_score"],
                "colorscale": [[0, "#27ae60"], [0.5, "#95a5a6"], [1, "#e74c3c"]],
                "colorbar": {"title": "情感"},
                "showscale": True,
            },
            text=author_stats["author_name"],
            hovertemplate="%{text}<br>发文: %{x}篇<br>均互动: %{y:.0f}<extra></extra>",
        )
    )
    fig2.update_layout(
        title="发文频率 vs 平均互动 (气泡=总互动, 颜色=情感)",
        xaxis_title="发文数",
        yaxis_title="平均互动",
    )
    charts_html += chart_to_html(fig2, "author_freq_eng", 400)  # 【调用函数】跨文件(report_utils): 发文频率vs互动散点转内嵌 HTML

    return {
        "text": text,
        "html": f"""
        {charts_html}
        <h3>作者完整排行</h3>
        {dataframe_to_html(author_stats.head(30)[["author_name", "post_count", "bias", "total_engagement", "avg_sentiment_score", "avg_confidence"]])}
        """,
    }
