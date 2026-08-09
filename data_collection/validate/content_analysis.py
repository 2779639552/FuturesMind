"""
模块4：内容策略分析 (P2)
— 视频vs图文、正文长度vs互动、标签、发布时间、地域
"""

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
from report_utils import (
    chart_to_html,
    dataframe_to_html,
    stat_cards_html,
)


def analyze(df: pd.DataFrame) -> dict:
    """内容特征与互动关系分析"""

    # === 1. 图文 vs 视频 ===
    image_notes = df[~df["is_video"]]
    video_notes = df[df["is_video"]]

    text = f"{'=' * 70}\n"
    text += "  内容形式对比\n"
    text += f"{'=' * 70}\n"
    text += f"  图文笔记: {len(image_notes)} 条 ({len(image_notes) / len(df) * 100:.0f}%)"
    if len(image_notes) > 0:
        text += f", 均互动: {image_notes['engagement'].mean():.0f}"
    text += f"\n  视频笔记: {len(video_notes)} 条 ({len(video_notes) / len(df) * 100:.0f}%)"
    if len(video_notes) > 0:
        text += f", 均互动: {video_notes['engagement'].mean():.0f}"

    # === 2. 正文长度 vs 互动 ===
    df["desc_len_bucket"] = pd.cut(
        df["desc_length"],
        bins=[0, 50, 100, 200, 400, 800, 9999],
        labels=["0-50", "50-100", "100-200", "200-400", "400-800", "800+"],
    )
    len_stats = (
        df.groupby("desc_len_bucket", observed=False)
        .agg(
            count=("note_id", "count"),
            avg_engagement=("engagement", "mean"),
            avg_likes=("like_count", "mean"),
        )
        .round(1)
    )

    text += "\n\n  正文长度与互动关系:"
    for bucket, row in len_stats.iterrows():
        if int(row["count"]) > 0:
            bar = "█" * max(1, int(row["avg_engagement"] // 2))
            text += f"\n    {bucket}字: {int(row['count']):3d}条, 均互动{row['avg_engagement']:5.0f}, 均赞{row['avg_likes']:4.0f} {bar}"

    # === 3. 高频标签 ===
    all_tags = []
    for tags in df["tags"]:
        if isinstance(tags, list):
            all_tags.extend(tags)
    tag_counts = Counter(all_tags).most_common(20)

    if tag_counts:
        text += "\n\n  高频标签 Top 20:"
        for tag, cnt in tag_counts:
            text += f" [{tag}({cnt})]"

    # === 4. 发布时间分布 ===
    df["hour"] = df["dt"].dt.hour
    hour_stats = (
        df.groupby("hour")
        .agg(
            count=("note_id", "count"),
            avg_engagement=("engagement", "mean"),
        )
        .reset_index()
    )

    # 最佳时段
    if len(hour_stats) > 0:
        best_hour = hour_stats.loc[hour_stats["avg_engagement"].idxmax()]
        text += f"\n\n  最佳发帖时段: {int(best_hour['hour'])}:00 (均互动 {best_hour['avg_engagement']:.0f})"

    # === 5. 地域分布 ===
    geo_counts = Counter(df["ip_location"].dropna()).most_common(10)
    if geo_counts:
        text += "\n\n  地域分布 Top 10:"
        for loc, cnt in geo_counts:
            if loc and loc.strip():
                text += f" {loc}({cnt})"

    # ================================================================
    # HTML 图表
    # ================================================================

    charts_html = ""

    charts_html += stat_cards_html(
        {
            "图文笔记": f"{len(image_notes)}条",
            "视频笔记": f"{len(video_notes)}条",
            "平均正文": f"{int(df['desc_length'].mean())}字",
            "平均标签": f"{df['tags'].apply(lambda x: len(x) if isinstance(x, list) else 0).mean():.1f}个",
        }
    )

    # Chart 1: 正文长度 vs 互动散点
    fig1 = go.Figure()
    fig1.add_trace(
        go.Scatter(
            x=df["desc_length"],
            y=df["engagement"],
            mode="markers",
            marker={
                "size": 8,
                "opacity": 0.6,
                "color": df["is_video"].map({True: "#e74c3c", False: "#3498db"}),
            },
            text=df["title"].str[:30],
            hovertemplate="%{text}...<br>长度: %{x}字<br>互动: %{y}<extra></extra>",
        )
    )
    # Add trend line
    if len(df) > 5:
        z = pd.DataFrame({"x": df["desc_length"], "y": df["engagement"]}).dropna()
        if len(z) > 2:
            try:
                import numpy as np

                coeffs = np.polyfit(z["x"], z["y"], 1)
                trend_x = [z["x"].min(), z["x"].max()]
                trend_y = [coeffs[0] * trend_x[0] + coeffs[1], coeffs[0] * trend_x[1] + coeffs[1]]
                fig1.add_trace(
                    go.Scatter(
                        x=trend_x,
                        y=trend_y,
                        mode="lines",
                        name="趋势线",
                        line={"dash": "dash", "color": "#e74c3c", "width": 2},
                    )
                )
            except Exception:
                pass

    fig1.update_layout(
        title="正文长度 vs 互动量 (蓝=图文, 红=视频)",
        xaxis_title="正文长度(字)",
        yaxis_title="互动总分",
    )
    charts_html += chart_to_html(fig1, "desc_len_eng", 400)

    # Chart 2: 内容形式箱线图
    fig2 = go.Figure()
    for label, subset in [("图文", image_notes), ("视频", video_notes)]:
        if len(subset) > 0:
            fig2.add_trace(
                go.Box(
                    y=subset["engagement"],
                    name=label,
                    marker_color="#3498db" if label == "图文" else "#e74c3c",
                )
            )
    fig2.update_layout(title="图文 vs 视频 互动分布", yaxis_title="互动总分")
    charts_html += chart_to_html(fig2, "type_box", 350)

    # Chart 3: 时段热力图
    fig3 = go.Figure()
    fig3.add_trace(
        go.Bar(
            x=hour_stats["hour"],
            y=hour_stats["count"],
            name="发文数",
            marker_color="#3498db",
            marker_opacity=0.7,
        )
    )
    fig3.add_trace(
        go.Scatter(
            x=hour_stats["hour"],
            y=hour_stats["avg_engagement"],
            mode="lines+markers",
            name="均互动",
            yaxis="y2",
            marker={"size": 8, "color": "#e74c3c"},
            line={"width": 2},
        )
    )
    fig3.update_layout(
        title="发布时间分布 (发文量 vs 互动量)",
        xaxis_title="小时",
        yaxis={"title": "发文量"},
        yaxis2={"title": "平均互动", "overlaying": "y", "side": "right"},
    )
    charts_html += chart_to_html(fig3, "hour_heat", 350)

    return {
        "text": text,
        "html": f"""
        {charts_html}
        <h3>正文长度分组统计</h3>
        {dataframe_to_html(len_stats.reset_index())}
        """,
    }
