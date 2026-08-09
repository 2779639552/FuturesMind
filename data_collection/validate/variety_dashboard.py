"""
模块1：品种热度与情绪仪表盘 (P0)
— 品种提及频率、加权热度、情感排名、板块对比
"""

import pandas as pd
import plotly.graph_objects as go
from report_utils import (
    SECTOR_COLORS,
    chart_to_html,
    dataframe_to_html,
    expand_varieties,
    get_sector,
    sentiment_bar,
    stat_cards_html,
    terminal_table,
)


def analyze(df: pd.DataFrame) -> dict:
    """品种热度+情绪分析，返回 {text, html}"""
    vdf = expand_varieties(df)
    # 过滤空品种
    vdf_valid = vdf[vdf["variety_name"] != ""].copy()

    # === 1. 品种提及频率 ===
    variety_counts = vdf_valid["variety_name"].value_counts()
    # 品种→板块
    variety_sector = {v: get_sector(v) for v in variety_counts.index}

    # === 2. 品种加权热度 ===
    # engagement是note级互动总分, 同一品种被多篇笔记提及时累加
    variety_eng = vdf_valid.groupby("variety_name")["engagement"].sum()
    variety_avg_like = vdf_valid.groupby("variety_name")["like_count"].mean()

    # 加权热度 = log(提及+1) × log(总互动+1)
    variety_heat = {}
    for v in variety_counts.index:
        mentions = variety_counts.get(v, 0)
        eng = variety_eng.get(v, 0)
        variety_heat[v] = round(mentions * (1 + (eng**0.3)), 1)

    # === 3. 品种情感排名 ===
    variety_sent = vdf_valid.groupby("variety_name")["var_sentiment_score"].mean()
    variety_conf = vdf_valid.groupby("variety_name")["var_confidence"].mean()

    # 合并为 DataFrame
    rows = []
    for v in variety_counts.index:
        sent_score = round(variety_sent.get(v, 0), 3)
        rows.append(
            {
                "variety": v,
                "sector": variety_sector.get(v, "其他"),
                "mentions": int(variety_counts[v]),
                "heat": round(variety_heat.get(v, 0), 1),
                "avg_sentiment": sent_score,
                "sentiment_label": _score_to_label(sent_score),
                "avg_confidence": round(variety_conf.get(v, 0), 2),
                "avg_likes": round(variety_avg_like.get(v, 0), 1),
                "total_engagement": int(variety_eng.get(v, 0)),
            }
        )

    var_df = pd.DataFrame(rows).sort_values("heat", ascending=False)

    # === 4. 板块对比 ===
    sector_stats = (
        var_df.groupby("sector")
        .agg(
            total_mentions=("mentions", "sum"),
            avg_sentiment=("avg_sentiment", "mean"),
            variety_count=("variety", "count"),
            total_engagement=("total_engagement", "sum"),
        )
        .round(3)
        .reset_index()
    )

    # ================================================================
    # 终端输出
    # ================================================================

    # Top 20 表格
    top20 = var_df.head(20)
    table_rows = []
    for _, r in top20.iterrows():
        bar = sentiment_bar(r["avg_sentiment"])
        table_rows.append(
            [
                r["variety"],
                r["sector"],
                str(r["mentions"]),
                f"{r['avg_sentiment']:+.2f}",
                bar,
                r["sentiment_label"],
                str(r["avg_confidence"]),
            ]
        )

    text = terminal_table(
        ["品种", "板块", "提及", "情感", "情绪条", "方向", "确信度"],
        table_rows,
        title="品种热度与情绪仪表盘 (Top 20)",
        col_widths=[10, 8, 6, 8, 14, 8, 8],
    )

    # 板块对比表
    sec_rows = []
    for _, r in sector_stats.iterrows():
        sec_rows.append(
            [
                r["sector"],
                str(r["variety_count"]),
                str(r["total_mentions"]),
                f"{r['avg_sentiment']:+.3f}",
                str(r["total_engagement"]),
            ]
        )
    text += terminal_table(
        ["板块", "品种数", "总提及", "平均情感", "总互动"],
        sec_rows,
        title="板块级情绪对比",
        col_widths=[10, 8, 8, 10, 10],
    )

    # 极端品种
    most_bullish = var_df.nlargest(5, "avg_sentiment")
    most_bearish = var_df.nsmallest(5, "avg_sentiment")
    text += f"\n{'=' * 70}"
    text += f"\n  [多] 最看多品种: {', '.join(f'{r.variety}({r.avg_sentiment:+.2f})' for _, r in most_bullish.iterrows())}"
    text += f"\n  [空] 最看空品种: {', '.join(f'{r.variety}({r.avg_sentiment:+.2f})' for _, r in most_bearish.iterrows())}"
    text += f"\n{'=' * 70}"

    # ================================================================
    # HTML 图表
    # ================================================================

    charts_html = ""

    # 统计卡片
    bull_pct = (var_df["avg_sentiment"] > 0.05).sum() / len(var_df) * 100
    bear_pct = (var_df["avg_sentiment"] < -0.05).sum() / len(var_df) * 100
    100 - bull_pct - bear_pct
    charts_html += stat_cards_html(
        {
            "品种总数": str(len(var_df)),
            "总提及": str(var_df["mentions"].sum()),
            f"看多品种 ({bull_pct:.0f}%)": str((var_df["avg_sentiment"] > 0.05).sum()),
            f"看空品种 ({bear_pct:.0f}%)": str((var_df["avg_sentiment"] < -0.05).sum()),
            "中性品种": str((var_df["avg_sentiment"].abs() <= 0.05).sum()),
        }
    )

    # Chart 1: 品种热度-情感气泡图
    fig1 = go.Figure()
    for sector in var_df["sector"].unique():
        sdf = var_df[var_df["sector"] == sector]
        color = SECTOR_COLORS.get(sector, "#95a5a6")
        fig1.add_trace(
            go.Scatter(
                x=sdf["heat"],
                y=sdf["avg_sentiment"],
                mode="markers+text",
                name=sector,
                text=sdf["variety"],
                textposition="top center",
                marker={
                    "size": sdf["mentions"] * 2,
                    "color": color,
                    "opacity": 0.75,
                    "line": {"width": 1, "color": "#fff"},
                },
                hovertemplate="%{text}<br>热度: %{x:.1f}<br>情感: %{y:+.3f}<br>提及: %{marker.size}次<extra></extra>",
            )
        )

    fig1.update_layout(
        title="品种热度 × 情感矩阵",
        xaxis_title="加权热度",
        yaxis_title="情感分数 (-1看空 → +1看多)",
        yaxis={"range": [-1, 1], "zeroline": True, "zerolinecolor": "#ccc"},
        showlegend=True,
    )
    charts_html += chart_to_html(fig1, "variety_heat_sent", 500)

    # Chart 2: 板块柱状图
    sector_colors_mapped = [SECTOR_COLORS.get(s, "#95a5a6") for s in sector_stats["sector"]]
    fig2 = go.Figure()
    fig2.add_trace(
        go.Bar(
            x=sector_stats["sector"],
            y=sector_stats["total_mentions"],
            marker_color=sector_colors_mapped,
            text=sector_stats["total_mentions"],
            textposition="outside",
            name="总提及",
        )
    )
    fig2.add_trace(
        go.Scatter(
            x=sector_stats["sector"],
            y=sector_stats["avg_sentiment"] * 50,
            mode="lines+markers",
            name="平均情感(x50)",
            yaxis="y2",
            marker={"size": 12, "color": "#e74c3c"},
            line={"width": 3, "dash": "dot"},
        )
    )
    fig2.update_layout(
        title="板块提及量与平均情感",
        yaxis={"title": "提及次数"},
        yaxis2={"title": "情感分数", "overlaying": "y", "side": "right", "range": [-50, 50]},
        showlegend=True,
    )
    charts_html += chart_to_html(fig2, "sector_bar", 400)

    # Chart 3: Top 15 品种水平柱状图 (情感排序)
    top15_sent = var_df.nlargest(15, "avg_sentiment")
    colors_sent = []
    for _, r in top15_sent.iterrows():
        if r["avg_sentiment"] > 0.1:
            colors_sent.append("#e74c3c")
        elif r["avg_sentiment"] < -0.1:
            colors_sent.append("#27ae60")
        else:
            colors_sent.append("#95a5a6")

    fig3 = go.Figure()
    fig3.add_trace(
        go.Bar(
            y=top15_sent["variety"][::-1],
            x=top15_sent["avg_sentiment"][::-1],
            orientation="h",
            marker_color=list(reversed(colors_sent)),
            text=[f"{v:+.2f}" for v in top15_sent["avg_sentiment"][::-1]],
            textposition="outside",
        )
    )
    fig3.update_layout(
        title="品种情感排名",
        xaxis_title="情感分数 (-1看空 → +1看多)",
        xaxis={"range": [-1, 1], "zeroline": True, "zerolinecolor": "#ccc"},
    )
    charts_html += chart_to_html(fig3, "variety_sent_rank", 500)

    return {
        "text": text,
        "html": f"""
        {charts_html}
        <h3>品种数据明细</h3>
        {dataframe_to_html(var_df.sort_values("mentions", ascending=False).head(30))}
        """,
    }


def _score_to_label(score: float) -> str:
    if score > 0.3:
        return "看多 ↑"
    elif score > 0.05:
        return "略多 ↗"
    elif score < -0.3:
        return "看空 ↓"
    elif score < -0.05:
        return "略空 ↘"
    else:
        return "中性 →"
