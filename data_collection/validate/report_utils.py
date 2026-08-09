"""
共享工具：数据加载、图表样式、HTML报告生成
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

# ============================================================
# 常量
# ============================================================

SENTIMENT_ORDER = [
    "strong_bullish",
    "bullish",
    "slightly_bullish",
    "neutral",
    "slightly_bearish",
    "bearish",
    "strong_bearish",
]

SENTIMENT_LABELS = {
    "strong_bullish": "强烈看多",
    "bullish": "看多",
    "slightly_bullish": "略看多",
    "neutral": "中性",
    "slightly_bearish": "略看空",
    "bearish": "看空",
    "strong_bearish": "强烈看空",
}

SENTIMENT_COLORS = {
    "strong_bullish": "#e74c3c",
    "bullish": "#e67e73",
    "slightly_bullish": "#f5b7b1",
    "neutral": "#95a5a6",
    "slightly_bearish": "#a0d2a0",
    "bearish": "#5cb85c",
    "strong_bearish": "#2e7d32",
}

SECTOR_COLORS = {
    "黑色系": "#e74c3c",
    "有色金属": "#f39c12",
    "能源化工": "#3498db",
    "农产品": "#27ae60",
    "金融期货": "#9b59b6",
    "其他": "#95a5a6",
}

# 品种→板块映射 (从 config 提取)
VARIETY_SECTOR_MAP = {
    "螺纹钢": "黑色系",
    "铁矿石": "黑色系",
    "热卷": "黑色系",
    "焦炭": "黑色系",
    "焦煤": "黑色系",
    "硅铁": "黑色系",
    "锰硅": "黑色系",
    "线材": "黑色系",
    "铜": "有色金属",
    "铝": "有色金属",
    "锌": "有色金属",
    "铅": "有色金属",
    "镍": "有色金属",
    "锡": "有色金属",
    "黄金": "有色金属",
    "白银": "有色金属",
    "碳酸锂": "有色金属",
    "工业硅": "有色金属",
    "原油": "能源化工",
    "PTA": "能源化工",
    "甲醇": "能源化工",
    "PVC": "能源化工",
    "PP": "能源化工",
    "塑料": "能源化工",
    "橡胶": "能源化工",
    "沥青": "能源化工",
    "尿素": "能源化工",
    "纯碱": "能源化工",
    "玻璃": "能源化工",
    "乙二醇": "能源化工",
    "苯乙烯": "能源化工",
    "短纤": "能源化工",
    "豆粕": "农产品",
    "豆油": "农产品",
    "棕榈油": "农产品",
    "菜粕": "农产品",
    "菜油": "农产品",
    "白糖": "农产品",
    "棉花": "农产品",
    "玉米": "农产品",
    "淀粉": "农产品",
    "鸡蛋": "农产品",
    "生猪": "农产品",
    "苹果": "农产品",
    "红枣": "农产品",
    "花生": "农产品",
    "股指期货": "金融期货",
    "国债期货": "金融期货",
}

OUTPUT_DIR = Path(__file__).parent / "output"
REPORT_DIR = OUTPUT_DIR / "reports"
CHART_DIR = OUTPUT_DIR / "charts"


def get_sector(name: str) -> str:
    """品种名 → 板块名"""
    return VARIETY_SECTOR_MAP.get(name, "其他")


# ============================================================
# 数据加载
# ============================================================


def load_data(jsonl_path: str) -> pd.DataFrame:
    """加载 JSONL → DataFrame, 展开嵌套字段"""
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)

            # 展开 varieties 列表
            varieties = rec.get("varieties", []) or []
            rec["variety_names"] = [v.get("name", "") for v in varieties]
            rec["variety_sectors"] = [v.get("sector", "") for v in varieties]
            rec["variety_count"] = len(varieties)

            # 展开 variety_sentiments
            var_sents = rec.get("variety_sentiments", []) or []
            rec["var_sent_map"] = {vs.get("variety", ""): vs for vs in var_sents}

            # 平台兼容: 旧数据无 platform 字段默认 xhs
            rec.setdefault("platform", "xhs")

            # 统一时间
            pt = rec.get("publish_time", "")
            if pt:
                try:
                    rec["dt"] = pd.to_datetime(pt)
                except Exception:
                    rec["dt"] = None
            else:
                rec["dt"] = None

            # 互动总分
            rec["engagement"] = (
                (rec.get("like_count") or 0)
                + (rec.get("comment_count") or 0) * 2
                + (rec.get("collect_count") or 0) * 3
                + (rec.get("share_count") or 0) * 4
            )

            records.append(rec)

    df = pd.DataFrame(records)
    return df


def expand_varieties(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 varieties 从每行列表展开为每品种一行 (适合品种级分析)
    保留原 note 的所有字段 + variety-level 字段
    """
    rows = []
    for _, row in df.iterrows():
        varieties = row.get("varieties") or []
        var_sents = row.get("variety_sentiments") or []
        sent_map = {vs.get("variety", ""): vs for vs in var_sents}

        if not varieties:
            rows.append(
                {
                    **row.to_dict(),
                    "variety_name": "",
                    "variety_sector": "",
                    "variety_matched": "",
                    "var_sentiment": row.get("sentiment", "neutral"),
                    "var_sentiment_score": row.get("sentiment_score", 0),
                    "var_confidence": row.get("sentiment_confidence", 0),
                    "var_certainty": 0,
                    "var_time_horizon": "",
                }
            )
        else:
            for v in varieties:
                vname = v.get("name", "")
                vs = sent_map.get(vname, {})
                rows.append(
                    {
                        **row.to_dict(),
                        "variety_name": vname,
                        "variety_sector": v.get("sector", get_sector(vname)),
                        "variety_matched": v.get("matched", vname),
                        "var_sentiment": vs.get("sentiment", row.get("sentiment", "neutral")),
                        "var_sentiment_score": vs.get("score", row.get("sentiment_score", 0)),
                        "var_confidence": vs.get("confidence", row.get("sentiment_confidence", 0)),
                        "var_certainty": vs.get("certainty", 0),
                        "var_time_horizon": vs.get("time_horizon", ""),
                    }
                )

    return pd.DataFrame(rows)


# ============================================================
# 终端输出工具
# ============================================================


def terminal_table(
    headers: list[str], rows: list[list[str]], title: str = "", col_widths: list[int] = None
) -> str:
    """生成对齐的终端表格"""
    lines = []
    if title:
        lines.append(f"\n{'=' * 70}")
        lines.append(f"  {title}")
        lines.append(f"{'=' * 70}")

    if not col_widths:
        col_widths = []
        for i, h in enumerate(headers):
            max_w = len(h)
            for r in rows:
                if i < len(r):
                    max_w = max(max_w, len(str(r[i])))
            col_widths.append(min(max_w, 40))

    # Header
    header_parts = [f"{h:<{col_widths[i]}}" for i, h in enumerate(headers)]
    lines.append("  " + "  ".join(header_parts))
    lines.append("  " + "  ".join("-" * w for w in col_widths))

    # Rows
    for row in rows:
        parts = []
        for i in range(len(headers)):
            val = str(row[i]) if i < len(row) else ""
            if len(val) > col_widths[i]:
                val = val[: col_widths[i] - 3] + "..."
            parts.append(f"{val:<{col_widths[i]}}")
        lines.append("  " + "  ".join(parts))

    return "\n".join(lines)


def sentiment_bar(value: float, width: int = 12) -> str:
    """情感分数 → 终端柱状条: -1.0 → +1.0"""
    if value >= 0:
        n = int(value * width)
        return " " * (width - n) + "█" * n
    else:
        n = int(abs(value) * width)
        return "█" * n + " " * (width - n)


# ============================================================
# HTML 报告生成
# ============================================================

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>期货社交媒体分析报告</title>
<script src="https://cdn.plot.ly/plotly-3.0.1.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f6fa; color: #2c3e50; line-height: 1.6; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
  .header {{ background: linear-gradient(135deg, #2c3e50, #3498db); color: #fff;
             padding: 30px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
  .header .meta {{ opacity: 0.8; font-size: 14px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 24px;
           margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .card h2 {{ font-size: 20px; margin-bottom: 16px; color: #2c3e50;
              border-bottom: 2px solid #3498db; padding-bottom: 8px; }}
  .chart {{ width: 100%; min-height: 400px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .stat-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat {{ background: #f8f9fa; border-radius: 8px; padding: 16px 20px;
            text-align: center; min-width: 100px; flex: 1; }}
  .stat .value {{ font-size: 28px; font-weight: 700; color: #2c3e50; }}
  .stat .label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #ecf0f1; }}
  th {{ background: #f8f9fa; font-weight: 600; color: #7f8c8d; font-size: 12px;
         text-transform: uppercase; }}
  tr:hover td {{ background: #f8f9ff; }}
  @media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>期货社交媒体分析报告</h1>
    <div class="meta">数据来源: 多平台（小红书/微博/知乎） | 生成时间: {timestamp} | 笔记总数: {total_notes}</div>
  </div>
  {body}
</div>
</body>
</html>"""


def generate_html_report(
    sections: list[dict], total_notes: int, output_path: str | None = None
) -> str:
    """
    生成完整HTML报告
    sections: [{"title": "...", "content": html_string}, ...]
    """
    body_parts = []
    for sec in sections:
        body_parts.append(f'<div class="card"><h2>{sec["title"]}</h2>{sec["content"]}</div>')

    html = _HTML_TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_notes=total_notes,
        body="\n".join(body_parts),
    )

    if output_path:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  报告已保存: {output_path}")

    return html


def chart_to_html(fig, chart_id: str = "", height: int = 400) -> str:
    """Plotly figure → HTML div (不依赖 plotly HTML, 直接用 JSON+JS)"""
    fig.update_layout(
        height=height,
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
        font={"family": "-apple-system, BlinkMacSystemFont, sans-serif", "size": 12},
    )
    chart_json = fig.to_json()
    div_id = chart_id or f"chart_{id(fig)}"
    return f"""
    <div id="{div_id}" class="chart"></div>
    <script>
      (function() {{
        var data = {chart_json};
        Plotly.newPlot('{div_id}', data.data, data.layout, {{responsive: true, displayModeBar: false}});
      }})();
    </script>"""


# ============================================================
# 快捷统计卡片
# ============================================================


def stat_cards_html(stats: dict[str, str]) -> str:
    """生成统计卡片行"""
    cards = []
    for label, value in stats.items():
        cards.append(
            f'<div class="stat"><div class="value">{value}</div><div class="label">{label}</div></div>'
        )
    return f'<div class="stat-row">{"".join(cards)}</div>'


def dataframe_to_html(df: pd.DataFrame, max_rows: int = 50) -> str:
    """DataFrame → HTML table"""
    html = df.head(max_rows).to_html(
        index=False, border=0, classes="dataframe", escape=False, justify="left"
    )
    return html.replace('class="dataframe"', 'style="width:100%;border-collapse:collapse"')
