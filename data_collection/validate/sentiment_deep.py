"""
模块2：情感深度分析 (P0)
— 7级情感分布、高确信信号、时间维度、品种-情感矩阵


本文件在"情绪数据生产链"中的角色
--------------------------------
    这是【情感结果的事后深度分析 + 可视化】模块 (报表层), 不是情感打分引擎。
    它读取的是"已经打好情感标签"的数据表 (DataFrame), 产出:
      1. 7 级情感分布与牛熊比
      2. 高确信度信号 (confidence > 0.5 的非中性帖子)
      3. 时间维度分布 (短期/中期/长期)
      4. 品种 × 情感 矩阵 (提及 >= 3 次的品种)
    同时生成 HTML 图表 (堆叠柱状图 / 热力图 / 置信度直方图)。

    ⚠️ 与任务描述的澄清:
      任务描述把 sentiment_deep.py 称为"LLM 深度情感引擎"。
      实际代码是 pandas + plotly 的统计分析模块, 并不调用 LLM。
      真正的 LLM 深度情感引擎在 llm_sentiment.py。
      【待确认】若期望此处调用 LLM, 请确认版本或参考 llm_sentiment.py。
    输入数据来源: analyze.py 加载 batch_*.jsonl 后构造的 DataFrame,
      其中 sentiment / sentiment_confidence / variety_sentiments 等字段
      由 sentiment.py / batch_collect.py 的 enrich 流程提前打好。
"""

from collections import Counter  # 【调用包】7级情感标签频次统计(Counter)

import pandas as pd  # 【调用包】DataFrame 数据处理与品种-情感矩阵构造
import plotly.graph_objects as go  # 【调用包】Plotly 图表对象(柱状图/热力图/直方图)
from report_utils import (  # 【调用包】report_utils: 情感标签/颜色常量 + 终端/HTML 渲染工具
    SENTIMENT_COLORS,
    SENTIMENT_LABELS,
    SENTIMENT_ORDER,
    chart_to_html,
    dataframe_to_html,
    expand_varieties,
    stat_cards_html,
    terminal_table,
)


def analyze(df: pd.DataFrame) -> dict:
    """
    【功能】对已打好情感标签的数据做"深度分析", 返回终端文本 + HTML 图表。
    【参数】df: pandas.DataFrame, 至少应包含 sentiment / sentiment_confidence 等字段。
    【返回】dict, 形如 {"text": 终端可读的文本报告, "html": 拼接好的 HTML 片段}。
    【关键逻辑】
      - expand_varieties(df): 把"一条帖子多个品种"拆成"多行", 每行一个品种,
        便于做品种级统计 (来自 report_utils)。
      - 文本报告与 HTML 图表并行构建, 最后一起返回。
    """
    vdf = expand_varieties(df)  # 【调用函数】跨文件(report_utils): 一帖多品种拆多行, 便于品种级统计
    vdf_valid = vdf[vdf["variety_name"] != ""].copy()  # 【变量】只保留带有效品种名的行(品种级分析样本)

    # === 1. 7级情感分布 ===
    # Counter 统计每个 7 级标签出现的次数 (即情感分布的直方图数据)
    sent_dist = Counter()
    for s in df["sentiment"]:
        sent_dist[s] += 1
    total = len(df)

    text = terminal_table(  # 【调用函数】跨文件(report_utils): 生成终端等宽表格文本
        ["情感等级", "条数", "占比", "分布"],
        [
            [SENTIMENT_LABELS.get(k, k), str(v), f"{v / total * 100:.0f}%", "█" * (v // 3)]
            for k, v in sorted(
                sent_dist.items(),
                key=lambda x: SENTIMENT_ORDER.index(x[0]) if x[0] in SENTIMENT_ORDER else 99,
            )
        ],
        title="整体情感分布 (7级)",
        col_widths=[14, 6, 8, 30],
    )

    # 牛熊比: 把三档看多(strong/bullish/slightly)合成"牛", 三档看空合成"熊",
    # 再算比例; 分母 +1 避免除零。
    bull_total = (
        sent_dist.get("strong_bullish", 0)
        + sent_dist.get("bullish", 0)
        + sent_dist.get("slightly_bullish", 0)
    )
    bear_total = (
        sent_dist.get("strong_bearish", 0)
        + sent_dist.get("bearish", 0)
        + sent_dist.get("slightly_bearish", 0)
    )
    neutral_total = sent_dist.get("neutral", 0)  # 【变量】中性帖子数(7级中的 neutral)
    text += f"\n  牛熊比: {bull_total}牛 / {neutral_total}中 / {bear_total}熊 = {bull_total / (bear_total + 1):.2f}:1"

    # === 2. 高确信度信号 ===
    # 布尔索引筛选: 只保留"置信度>0.5 且非中性"的帖子, 视为高确信信号
    high_conf = df[(df["sentiment_confidence"] > 0.5) & (df["sentiment"] != "neutral")]
    high_conf_bull = high_conf[high_conf["sentiment"].str.contains("bullish")]
    high_conf_bear = high_conf[high_conf["sentiment"].str.contains("bearish")]

    text += f"\n\n{'=' * 70}"
    text += "\n  高确信度信号 (confidence > 0.5)"
    text += f"\n  {'=' * 70}"
    text += f"\n  总计: {len(high_conf)} 条 (占非中性笔记的 {len(high_conf) / max(bull_total + bear_total, 1) * 100:.0f}%)"
    text += f"\n  看多高确信: {len(high_conf_bull)} 条"
    text += f"\n  看空高确信: {len(high_conf_bear)} 条"

    if len(high_conf) > 0:
        # Top 高确信
        hc_samples = high_conf.nlargest(10, "sentiment_confidence")[
            ["title", "sentiment", "sentiment_score", "sentiment_confidence", "variety_names"]
        ]
        hc_rows = []
        for _, r in hc_samples.iterrows():
            title = (r["title"] or r.get("desc", ""))[:40]
            vars_str = ", ".join(r["variety_names"][:3])
            hc_rows.append(
                [
                    title,
                    SENTIMENT_LABELS.get(r["sentiment"], r["sentiment"]),
                    f"{r['sentiment_score']:+.2f}",
                    f"{r['sentiment_confidence']:.2f}",
                    vars_str,
                ]
            )
        text += terminal_table(  # 【调用函数】跨文件(report_utils): 高确信信号 Top10 表格
            ["内容预览", "情感", "分数", "确信", "涉及品种"],
            hc_rows,
            title="最高确信度信号 Top 10",
            col_widths=[22, 10, 6, 6, 20],
        )

    # === 3. 时间维度分析 ===
    # 统计各条品种级记录的时间维度 (short/mid/long), 看观点是偏短期还是偏长期
    time_horizon_dist = Counter()
    for _, row in vdf_valid.iterrows():
        th = row.get("var_time_horizon", "") or ""
        if th:
            time_horizon_dist[th] += 1

    if time_horizon_dist:
        text += "\n\n  时间维度分布: "
        th_labels = {"short": "短期", "mid": "中期", "long": "长期"}
        text += ", ".join(f"{th_labels.get(k, k)}: {v}" for k, v in time_horizon_dist.most_common())

    # === 4. 品种-情感矩阵 ===
    # 取提及次数 >= 3 的品种 (样本太少统计无意义), 最多展示 Top 20
    vc = vdf_valid["variety_name"].value_counts()
    top_varieties = vc[vc >= 3].index.tolist()[:20]  # Top 20

    matrix_data = []
    for vname in top_varieties:
        vnotes = vdf_valid[vdf_valid["variety_name"] == vname]
        row = {"品种": vname}
        for sent in SENTIMENT_ORDER:
            count = (vnotes["var_sentiment"] == sent).sum()
            row[sent] = count
        matrix_data.append(row)

    matrix_df = pd.DataFrame(matrix_data)  # 【变量】品种×情感 计数矩阵, 用于热力图与明细表

    # ================================================================
    # HTML 图表
    # ================================================================
    # 以下用 Plotly 生成 3 张图并转成内嵌 HTML 片段, 拼接进 charts_html:
    #   fig1: 7级情感分布柱状图
    #   fig2: 品种 × 情感 热力图
    #   fig3: 置信度分布直方图 (看多/看空/中性 叠加)
    # chart_to_html() 来自 report_utils, 负责把 Plotly 图转成离线可用的 HTML。

    charts_html = ""

    # 统计卡片
    charts_html += stat_cards_html(  # 【调用函数】跨文件(report_utils): 生成 HTML 统计卡片
        {
            "笔记总数": str(total),
            "牛熊比": f"{bull_total}:{bear_total}",
            "中性占比": f"{neutral_total / total * 100:.0f}%",
            "高确信信号": str(len(high_conf)),
            f"看多({bull_total})": f"{(bull_total) / total * 100:.0f}%",
            f"看空({bear_total})": f"{(bear_total) / total * 100:.0f}%",
        }
    )

    # Chart 1: 7级情感堆叠柱状图
    sent_ordered = [SENTIMENT_LABELS.get(k, k) for k in SENTIMENT_ORDER]
    sent_counts = [sent_dist.get(k, 0) for k in SENTIMENT_ORDER]
    sent_colors_mapped = [SENTIMENT_COLORS.get(k, "#95a5a6") for k in SENTIMENT_ORDER]

    fig1 = go.Figure()
    fig1.add_trace(
        go.Bar(
            x=sent_ordered,
            y=sent_counts,
            marker_color=sent_colors_mapped,
            text=sent_counts,
            textposition="outside",
        )
    )
    fig1.update_layout(
        title="7级情感分布",
        xaxis_title="情感等级",
        yaxis_title="笔记数量",
    )
    charts_html += chart_to_html(fig1, "sent_dist", 400)  # 【调用函数】跨文件(report_utils): Plotly 图转内嵌 HTML 片段

    # Chart 2: 品种-情感热力图
    if len(matrix_df) >= 3:
        # Prepare heatmap data
        sent_labels_short = ["强多", "看多", "略多", "中性", "略空", "看空", "强空"]
        z_data = matrix_df[SENTIMENT_ORDER].values.tolist()
        y_labels = matrix_df["品种"].tolist()

        fig2 = go.Figure(
            data=go.Heatmap(
                z=z_data,
                x=sent_labels_short,
                y=y_labels,
                colorscale=[
                    [0, "#27ae60"],
                    [0.3, "#a0d2a0"],
                    [0.45, "#ecf0f1"],
                    [0.5, "#f8f9fa"],
                    [0.55, "#fdebd0"],
                    [0.7, "#f5b7b1"],
                    [1, "#e74c3c"],
                ],
                text=[[str(v) if v > 0 else "" for v in row] for row in z_data],
                texttemplate="%{text}",
                hoverongaps=False,
            )
        )
        fig2.update_layout(
            title="品种 × 情感矩阵 (提及 >= 3次)",
            xaxis_title="情感等级",
            yaxis_title="品种",
        )
        charts_html += chart_to_html(fig2, "variety_sent_heatmap", max(300, len(y_labels) * 22))  # 【调用函数】跨文件(report_utils): 品种×情感热力图转内嵌 HTML

    # Chart 3: Confidence 分布
    fig3 = go.Figure()
    conf_bull = df[df["sentiment"].str.contains("bullish", na=False)]["sentiment_confidence"]  # 【变量】看多帖子的置信度序列
    conf_bear = df[df["sentiment"].str.contains("bearish", na=False)]["sentiment_confidence"]  # 【变量】看空帖子的置信度序列
    conf_neutral = df[df["sentiment"] == "neutral"]["sentiment_confidence"]  # 【变量】中性帖子的置信度序列

    fig3.add_trace(
        go.Histogram(
            x=conf_bull,
            name="看多",
            marker_color="#e74c3c",
            opacity=0.7,
            xbins={"start": 0, "end": 1, "size": 0.1},
        )
    )
    fig3.add_trace(
        go.Histogram(
            x=conf_bear,
            name="看空",
            marker_color="#27ae60",
            opacity=0.7,
            xbins={"start": 0, "end": 1, "size": 0.1},
        )
    )
    fig3.add_trace(
        go.Histogram(
            x=conf_neutral,
            name="中性",
            marker_color="#95a5a6",
            opacity=0.5,
            xbins={"start": 0, "end": 1, "size": 0.1},
        )
    )
    fig3.update_layout(
        title="情感确信度分布 (confidence)",
        xaxis_title="置信度",
        yaxis_title="笔记数",
        barmode="overlay",
    )
    charts_html += chart_to_html(fig3, "conf_hist", 350)  # 【调用函数】跨文件(report_utils): 置信度分布直方图转内嵌 HTML

    return {
        "text": text,
        "html": f"""
        {charts_html}
        <h3>品种-情感明细矩阵</h3>
        {dataframe_to_html(matrix_df)}
        """,
    }
