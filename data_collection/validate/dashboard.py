"""
Dashboard: futures sentiment vs price comparison
Output: output/trends/dashboard.html (self-contained, no server needed)
"""

import json  # 【调用包】序列化看板 JS 内嵌数据(data_json / gm_json)与读取回测权重
import os  # 【调用包】输出文件大小统计(os.path.getsize)
from datetime import datetime  # 【调用包】看板生成时间戳(ts)
from pathlib import Path  # 【调用包】跨平台路径处理(TRENDS_DIR / plotly.min.js 路径)

import plotly.graph_objects as go  # 【调用包】Plotly 图表对象(迷你概览柱状图)

TRENDS_DIR = Path(__file__).parent / "output" / "trends"  # 【变量】时序数据与看板输出目录


# 【功能】扫描 TRENDS_DIR 加载全部品种的情绪与价格 JSON 到同一 dict。
# 【返回】{品种: {"sentiment": {...}, "price": {...}}}; 无数据品种缺省字段。
# 【关键】按文件名 glob 匹配 *_sentiment.json 与 *_price.json; 品种名取文件名去后缀。
def load_all_data():
    all_data = {}
    for f in sorted(TRENDS_DIR.glob("*_sentiment.json")):
        variety = f.stem.replace("_sentiment", "")
        with open(f, encoding="utf-8") as fh:
            all_data.setdefault(variety, {})["sentiment"] = json.load(fh)  # 【调用函数】落盘读取: 品种情绪时序
    for f in sorted(TRENDS_DIR.glob("*_price.json")):
        variety = f.stem.replace("_price", "")
        with open(f, encoding="utf-8") as fh:
            all_data.setdefault(variety, {})["price"] = json.load(fh)  # 【调用函数】落盘读取: 品种价格时序
    return all_data


# 【功能】组装看板 HTML: 排序品种 → 提取 JS 轻量数据 → 迷你概览图 → 内嵌 plotly.js → 写 dashboard.html。
# 【返回】生成的 dashboard.html 路径字符串。
# 【关键】把 JS 需要的最小字段嵌入 DATA/GM_DATA, 页面零外部依赖(plotly.min.js 内嵌)。
def build_dashboard():
    TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    all_data = load_all_data()

    # Rank by data richness
    ranked = sorted(  # 【变量】按情绪时序长度降序排品种(数据丰富度)
        all_data.keys(),
        key=lambda v: len(all_data[v].get("sentiment", {}).get("series", [])),
        reverse=True,
    )
    default_variety = ranked[0] if ranked else "螺纹钢"  # 【变量】默认展示品种(数据最丰富)

    # Extract lightweight data for JS embedding (strip heavy fields)
    js_data = {}  # 【变量】为 JS 渲染裁剪的轻量数据(去掉 author/raw 等重字段)
    for v, d in all_data.items():
        sent_series = d.get("sentiment", {}).get("series", [])
        price_series = d.get("price", {}).get("prices", [])
        # Only keep what JS needs for chart rendering
        js_data[v] = {
            "sentiment": {
                "series": [
                    {
                        "date": s["date"],
                        "avg_score": s["avg_score"],
                        "weighted_score": s.get("weighted_score"),
                        "note_count": s["note_count"],
                        "bull_count": s.get("bull_count", 0),
                        "bear_count": s.get("bear_count", 0),
                    }
                    for s in sent_series
                ],
                "stats": d.get("sentiment", {}).get("stats", {}),
            },
            "price": {
                "prices": [
                    {"date": p["date"], "close": p["close"], "change_pct": p.get("change_pct", 0)}
                    for p in price_series[-90:]
                ],  # last 90 days
            },
        }

    # Pre-render mini overview charts for all varieties (keep these server-rendered)
    mini_charts = ""  # 【变量】所有品种迷你概览图拼接后的 HTML 片段
    for v in ranked:
        sent = all_data[v].get("sentiment", {}).get("series", [])
        if len(sent) < 2:
            continue
        colors = ["#e74c3c" if s["avg_score"] >= 0 else "#27ae60" for s in sent]  # 【变量】柱色(红=多/绿=空)
        fig = go.Figure(
            go.Bar(
                x=[s["date"] for s in sent],
                y=[s["avg_score"] for s in sent],
                marker={"color": colors},
            )
        )
        fig.update_layout(
            title=v,
            plot_bgcolor="#161b22",
            paper_bgcolor="#161b22",
            font={"color": "#8b949e", "size": 10},
            height=200,
            margin={"l": 30, "r": 10, "t": 30, "b": 30},
            xaxis={"showgrid": False},
            yaxis={"range": [-1, 1], "showgrid": False},
            showlegend=False,
        )
        mini_charts += f'<div class="card" style="cursor:pointer" onclick="switchTo(\'{v}\')">{fig.to_html(full_html=False, include_plotlyjs=False)}</div>'  # 【调用函数】Plotly 图转 HTML 片段(不重复内嵌 plotly.js)

    # Variety selector
    variety_options = "\n".join(f'<option value="{v}">{v}</option>' for v in ranked)  # 【变量】下拉框品种选项 HTML

    # Embed plotly.js
    import plotly  # 【调用包】定位安装包内的 plotly.min.js 静态资源

    js_path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    with open(js_path, encoding="utf-8") as f:
        plotly_js = f.read()  # 【变量】plotly.min.js 完整内容(内嵌进 HTML 实现离线运行)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")  # 【变量】看板生成时间戳
    data_json = json.dumps(js_data, ensure_ascii=False)  # 【调用函数】序列化轻量数据为 JS 对象(DATA)

    # 加载全局回测指标
    gw_path = TRENDS_DIR / "_global_weights.json"
    global_metrics = {}  # 【变量】回测平台权重/指标(渲染顶部回测指标面板)
    if gw_path.exists():
        with open(gw_path, encoding="utf-8") as f:
            gw = json.load(f)  # 【调用函数】落盘读取: 全局回测权重与平台指标
        global_metrics = {
            "weights": gw.get("weights", {}),
            "metrics": gw.get("metrics", {}),
            "source": gw.get("weight_source", "N/A"),
            "total_points": gw.get("total_data_points", 0),
        }
    gm_json = json.dumps(global_metrics, ensure_ascii=False)  # 【调用函数】序列化回测指标为 JS 对象(GM_DATA)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>Futures Sentiment vs Price</title>
<script>{plotly_js}</script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; }}
.header {{ background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 20px 32px; border-bottom: 1px solid #30363d; }}
.header h1 {{ font-size: 22px; color: #58a6ff; }}
.header .meta {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
.controls {{ display: flex; gap: 10px; padding: 12px 32px; background: #161b22; border-bottom: 1px solid #30363d; align-items: center; flex-wrap: wrap; }}
.controls select {{ padding: 6px 12px; border-radius: 6px; border: 1px solid #30363d; background: #21262d; color: #c9d1d9; font-size: 14px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 32px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
.card h2 {{ font-size: 15px; color: #58a6ff; margin-bottom: 8px; }}
.stat-row {{ display: flex; gap: 10px; flex-wrap: wrap; padding: 0 32px 12px; }}
.stat {{ background: #21262d; border-radius: 6px; padding: 10px 14px; text-align: center; min-width: 70px; flex: 1; border: 1px solid #30363d; }}
.stat .value {{ font-size: 20px; font-weight: 700; }}
.stat .label {{ font-size: 10px; color: #8b949e; margin-top: 2px; }}
.overview-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; padding: 16px 32px; }}
.footer {{ text-align: center; padding: 12px; color: #484f58; font-size: 11px; }}
#status {{ color: #58a6ff; font-size: 13px; }}
</style></head>
<body>
<div class="header">
  <h1>Futures Sentiment vs Price Dashboard</h1>
  <div class="meta">多平台 (小红书/微博/知乎) + Sina Futures | {ts} | {len(ranked)} varieties | {global_metrics.get("total_points", 0)} 回测数据点</div>
</div>

<div id="metrics_panel" style="display:flex;gap:16px;padding:12px 32px;background:#161b22;border-bottom:1px solid #30363d;flex-wrap:wrap;align-items:center">
  <span style="font-size:13px;color:#8b949e;min-width:60px">回测指标</span>
</div>

<div class="controls">
  <label>Variety:</label>
  <select id="variety" onchange="switchTo(this.value)">{variety_options}</select>
  <span id="status">Ready</span>
</div>

<div id="stats_row" class="stat-row"></div>

<div class="grid">
  <div class="card" style="grid-column:1/-1"><h2 id="main_title">Sentiment vs Price</h2><div id="main_chart" style="min-height:450px"></div></div>
  <div class="card"><h2>Sentiment Distribution</h2><div id="pie_chart" style="min-height:300px"></div></div>
  <div class="card"><h2>Notes per Day</h2><div id="volume_chart" style="min-height:300px"></div></div>
</div>

<h2 style="padding:12px 32px;color:#58a6ff;font-size:16px">All Varieties Overview (click to switch)</h2>
<div class="overview_grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;padding:12px 32px">{mini_charts}</div>
<div class="footer">{ts}</div>

<script>
var DATA = {data_json};
var GM_DATA = {gm_json};
var CURRENT = null;

// ---- 渲染回测指标面板 ----
(function() {{
    var gm = GM_DATA;
    if (!gm || !gm.metrics || Object.keys(gm.metrics).length === 0) return;
    var panel = document.getElementById('metrics_panel');
    var order = ['xhs', 'weibo', 'zhihu'];
    var names = {{xhs:'小红书', weibo:'微博', zhihu:'知乎'}};
    var colors = {{xhs:'#e74c3c', weibo:'#f0a050', zhihu:'#58a6ff'}};
    var html = '<span style=\"font-size:13px;color:#8b949e;min-width:60px\">回测指标</span>';
    for (var i = 0; i < order.length; i++) {{
        var p = order[i];
        var m = gm.metrics[p];
        var w = gm.weights[p];
        if (!m) continue;
        var acc = (m.direction_accuracy * 100).toFixed(1);
        var r = m.pearson_r.toFixed(3);
        var rColor = r >= 0 ? '#e74c3c' : '#27ae60';
        var n = m.data_points;
        html += '<div style=\"background:#21262d;border:1px solid #30363d;border-left:3px solid ' + colors[p] +
            ';border-radius:6px;padding:8px 14px;text-align:center;min-width:90px\">' +
            '<div style=\"font-size:11px;color:' + colors[p] + ';font-weight:600\">' + names[p] +
            ' <span style=\"color:#8b949e\">' + (w*100).toFixed(0) + '%</span></div>' +
            '<div style=\"font-size:10px;color:#8b949e;margin-top:3px\">' +
            '准确率 <b style=\"color:#c9d1d9\">' + acc + '%</b> &nbsp;' +
            'r=<b style=\"color:' + rColor + '\">' + r + '</b> &nbsp;' +
            'n=<b style=\"color:#c9d1d9\">' + n + '</b>' +
            '</div></div>';
    }}
    html += '<div style=\"background:#21262d;border:1px solid #30363d;border-radius:6px;padding:8px 14px;text-align:center\">' +
        '<div style=\"font-size:10px;color:#8b949e\">数据来源</div>' +
        '<div style=\"font-size:12px;color:#58a6ff;font-weight:600\">' + gm.source + '</div>' +
        '<div style=\"font-size:10px;color:#8b949e\">' + gm.total_points + ' 点池化</div></div>';
    // 合成指标
    var cm = gm.combined_metrics;
    if (cm) {{
        var cAcc = (cm.direction_accuracy * 100).toFixed(1);
        var cR = cm.pearson_r.toFixed(3);
        var cRColor = cR >= 0 ? '#e74c3c' : '#27ae60';
        html += '<div style=\"background:#21262d;border:1px solid #58a6ff;border-radius:6px;padding:8px 14px;text-align:center\">' +
            '<div style=\"font-size:11px;color:#58a6ff;font-weight:600\">加权合成</div>' +
            '<div style=\"font-size:10px;color:#8b949e;margin-top:3px\">' +
            '准确率 <b style=\"color:#c9d1d9;font-size:14px\">' + cAcc + '%</b> &nbsp;' +
            'r=<b style=\"color:' + cRColor + ';font-size:13px\">' + cR + '</b>' +
            '</div><div style=\"font-size:10px;color:#8b949e\">n=' + cm.data_points + '</div></div>';
    }}
    panel.innerHTML = html;
}})();

function switchTo(v) {{
    if (!DATA[v]) return;
    CURRENT = v;
    document.getElementById('variety').value = v;
    document.getElementById('main_title').textContent = 'Sentiment vs Price - ' + v;
    document.getElementById('status').textContent = 'Rendering ' + v + '...';

    var d = DATA[v];
    var sent = d.sentiment?.series || [];
    var price = d.price?.prices || [];
    var stats = d.sentiment?.stats || {{}};

    // ---- Main chart: price line + sentiment bars + weighted line ----
    var traces = [];
    if (price.length > 0) {{
        traces.push({{
            x: price.map(p => p.date), y: price.map(p => p.close),
            type: 'scatter', mode: 'lines', name: 'Price', yaxis: 'y',
            line: {{ color: '#58a6ff', width: 2 }}
        }});
    }}
    if (sent.length > 0) {{
        var colors = sent.map(s => s.avg_score >= 0 ? 'rgba(231,76,60,0.7)' : 'rgba(39,174,96,0.7)');
        traces.push({{
            x: sent.map(s => s.date), y: sent.map(s => s.avg_score),
            type: 'bar', name: 'Sentiment (combined)', yaxis: 'y2',
            marker: {{ color: colors }}
        }});
        // Weighted sentiment line (dashed, only where available)
        var wDates = sent.filter(s => s.weighted_score != null).map(s => s.date);
        var wScores = sent.filter(s => s.weighted_score != null).map(s => s.weighted_score);
        if (wDates.length > 0) {{
            traces.push({{
                x: wDates, y: wScores,
                type: 'scatter', mode: 'lines+markers', name: 'Weighted (backtest)', yaxis: 'y2',
                line: {{ color: '#f0a050', width: 2, dash: 'dot' }},
                marker: {{ size: 6, color: '#f0a050' }}
            }});
        }}
    }}
    Plotly.react('main_chart', traces, {{
        xaxis: {{ title: 'Date', type: 'date', gridcolor: '#30363d' }},
        yaxis: {{ title: 'Price', side: 'left', color: '#58a6ff', gridcolor: '#30363d' }},
        yaxis2: {{ title: 'Sentiment', overlaying: 'y', side: 'right', range: [-1,1], gridcolor: '#30363d' }},
        plot_bgcolor: '#161b22', paper_bgcolor: '#161b22',
        font: {{ color: '#c9d1d9', size: 12 }},
        legend: {{ x: 0.01, y: 0.99 }}, hovermode: 'x unified',
        margin: {{ l: 50, r: 50, t: 20, b: 30 }}
    }}, {{ responsive: true }});

    // ---- Pie chart ----
    var bull = sent.reduce((a,s) => a + (s.bull_count||0), 0);
    var bear = sent.reduce((a,s) => a + (s.bear_count||0), 0);
    var total = sent.reduce((a,s) => a + (s.note_count||0), 0);
    var neutral = total - bull - bear;
    Plotly.react('pie_chart', [{{
        values: [bull, neutral, bear], labels: ['Bullish', 'Neutral', 'Bearish'],
        type: 'pie', hole: 0.4, marker: {{ colors: ['#e74c3c','#8b949e','#27ae60'] }},
        textinfo: 'label+percent'
    }}], {{
        plot_bgcolor: '#161b22', paper_bgcolor: '#161b22',
        font: {{ color: '#c9d1d9' }}, margin: {{ l:20, r:20, t:20, b:20 }}
    }}, {{ responsive: true }});

    // ---- Volume chart ----
    Plotly.react('volume_chart', [{{
        x: sent.map(s => s.date), y: sent.map(s => s.note_count),
        type: 'bar', marker: {{ color: '#58a6ff', opacity: 0.7 }}
    }}], {{
        xaxis: {{ title: 'Date', gridcolor: '#30363d' }},
        yaxis: {{ title: 'Notes', gridcolor: '#30363d' }},
        plot_bgcolor: '#161b22', paper_bgcolor: '#161b22',
        font: {{ color: '#c9d1d9' }}, margin: {{ l:20, r:20, t:20, b:20 }}
    }}, {{ responsive: true }});

    // ---- Stats cards ----
    var avgSent = stats.avg_sentiment || 0;
    var trend = stats.recent_trend || 'N/A';
    var lastPrice = price.length > 0 ? price[price.length-1].close.toFixed(1) : 'N/A';
    var lastChg = price.length > 0 ? (price[price.length-1].change_pct || 0).toFixed(2) : 'N/A';
    var chgColor = lastChg > 0 ? '#e74c3c' : (lastChg < 0 ? '#27ae60' : '#8b949e');
    var sentColor = avgSent > 0.1 ? '#e74c3c' : (avgSent < -0.1 ? '#27ae60' : '#8b949e');
    // Platform weights
    var pw = stats.platform_weights || {{}};
    var pwStr = Object.entries(pw).map(([k,v]) => k+':'+(v*100).toFixed(0)+'%').join(' ');
    var ws = stats.weight_source || 'equal';
    // Per-variety combined metrics
    var cm = stats.combined_metrics || {{}};
    var cmHtml = '';
    if (cm.direction_accuracy !== undefined && cm.direction_accuracy !== null) {{
        var cmAcc = (cm.direction_accuracy * 100).toFixed(1);
        var cmR = (cm.pearson_r || 0).toFixed(3);
        var cmRColor = cmR >= 0 ? '#e74c3c' : '#27ae60';
        cmHtml = '<div class=stat style=\"border-color:#58a6ff\"><div class=value style=font-size:18px;color:#58a6ff>' +
            cmAcc + '%</div><div class=label>合成准确率 (n=' + (cm.data_points||0) + ' r=' + cmR + ')</div></div>';
        console.log(v + ' combined: acc=' + cmAcc + '% r=' + cmR + ' n=' + (cm.data_points||0));
    }} else {{
        console.log(v + ' combined_metrics: missing or null', cm);
    }}
    document.getElementById('stats_row').innerHTML =
        '<div class=stat><div class=value style=color:'+sentColor+'>'+avgSent.toFixed(2)+'</div><div class=label>Avg Sentiment</div></div>' +
        '<div class=stat><div class=value>'+trend+'</div><div class=label>Trend</div></div>' +
        '<div class=stat><div class=value>'+lastPrice+'</div><div class=label>Latest Price</div></div>' +
        '<div class=stat><div class=value style=color:'+chgColor+'>'+lastChg+'%</div><div class=label>Change</div></div>' +
        '<div class=stat><div class=value>'+(stats.total_days||0)+'</div><div class=label>Days</div></div>' +
        '<div class=stat><div class=value>'+(stats.total_notes||0)+'</div><div class=label>Notes</div></div>' +
        '<div class=stat><div class=value style=font-size:13px>'+pwStr+'</div><div class=label>Weights ('+ws+')</div></div>' +
        cmHtml;

    document.getElementById('status').textContent = v + ' rendered in ' +
        (performance.now() - window._t0).toFixed(0) + 'ms';
}}

// Switch helper that also records timing
var _origSwitchTo = switchTo;
switchTo = function(v) {{
    window._t0 = performance.now();
    _origSwitchTo(v);
}};

// Load default on page ready
window.onload = function() {{
    switchTo('{default_variety}');
}};
</script></body></html>"""

    out_path = TRENDS_DIR / "dashboard.html"  # 【变量】看板输出路径
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)  # 【调用函数】落盘: 写入自包含看板 HTML

    print(f"Dashboard: {out_path}")
    print(f"Size: {os.path.getsize(out_path) / 1024:.0f}KB")
    print(f"Varieties: {len(ranked)}, Default: {default_variety}")
    return str(out_path)


if __name__ == "__main__":
    build_dashboard()  # 【调用函数】同文件: 直接生成看板
