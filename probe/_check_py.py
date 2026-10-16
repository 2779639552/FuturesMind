# -*- coding: utf-8 -*-
import re, pathlib, collections

for name in ("web_app.py", "web_template.html"):
    p = pathlib.Path(r"D:\AgentSense\AgentSense") / name
    t = p.read_text(encoding="utf-8", errors="replace")
    # JS 里的图表硬编码色（ECharts 读不到 CSS 变量，必须走令牌）
    hexes = collections.Counter(re.findall(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', t))
    # 常见 AI 味配色
    ai = {k: v for k, v in hexes.items() if k.lower() in
          ("#c7c7ff", "#ef4444", "#22c55e", "#60a5fa", "#a78bfa", "#2dd4bf", "#fbbf24",
           "#b9b9ff", "#e4dcff", "#149,149,255", "#6366f1", "#818cf8", "#a855f7")}
    print(f"=== {name} ===")
    print(f"  hex 色总数: {sum(hexes.values())}  种类: {len(hexes)}")
    print(f"  疑似旧 AI 配色残留: {sum(ai.values())} 处 -> {dict(list(ai.items())[:10])}")
    print(f"  style= 属性数: {t.count('style=\"')}")
    print(f"  emoji(彩色): {len(re.findall(r'[\U0001F300-\U0001FAFF]', t))}")
    print()
