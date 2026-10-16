# -*- coding: utf-8 -*-
"""UX/视觉评审取证：截取 AgentSense 全部页签（暗/亮双主题）+ 关键交互态。

产物落 probe/_ux/*.png，并输出一份 DOM 度量摘要 probe/_ux_metrics.json：
字体栈、字号层级、按钮尺寸、对比度候选色、卡片数、可见选项数等，
用于「设计评审」阶段量化判断，避免只靠肉眼。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:5000"
OUT = ROOT / "probe" / "_ux"
OUT.mkdir(exist_ok=True)

TABS = ["run", "research", "sentiment", "dashboard", "warrant", "foreign", "warehouse"]

METRICS_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const cs = (el) => getComputedStyle(el);
  const out = {};

  out.viewport = { w: innerWidth, h: innerHeight };
  out.fontStackBody = cs(document.body).fontFamily;

  // 字号层级：收集所有可见文字元素的 fontSize 分布
  const sizes = {};
  const weights = {};
  const families = {};
  document.querySelectorAll('body *').forEach(el => {
    if (!el.offsetParent && cs(el).position !== 'fixed') return;
    if (!el.textContent || !el.textContent.trim()) return;
    if (el.children.length > 0) return;
    const s = Math.round(px(cs(el).fontSize) * 10) / 10;
    sizes[s] = (sizes[s] || 0) + 1;
    const w = cs(el).fontWeight;
    weights[w] = (weights[w] || 0) + 1;
    families[cs(el).fontFamily.split(',')[0]] = (families[cs(el).fontFamily.split(',')[0]] || 0) + 1;
  });
  out.fontSizes = Object.entries(sizes).sort((a,b)=>b[1]-a[1]).slice(0,14)
                    .map(([k,v]) => ({size:+k, count:v}));
  out.fontWeights = Object.entries(weights).sort((a,b)=>b[1]-a[1]).slice(0,10)
                    .map(([k,v]) => ({weight:k, count:v}));
  out.fontFamilies = Object.entries(families).sort((a,b)=>b[1]-a[1]).slice(0,8)
                    .map(([k,v]) => ({family:k, count:v}));

  // 行高 / 行长（正文字块的 measure）
  out.paragraphs = [...document.querySelectorAll('p, li')].slice(0, 400).map(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 40) return null;
    const fs = px(cs(el).fontSize);
    const lh = cs(el).lineHeight === 'normal' ? fs * 1.2 : px(cs(el).lineHeight);
    return { w: Math.round(r.width), fs, lh: Math.round(lh*10)/10,
             ch: Math.round(r.width / (fs * 0.55)), ratio: +(lh/fs).toFixed(2) };
  }).filter(Boolean);

  // 交互元素尺寸（触达目标 44x44 基准）
  const clickable = [...document.querySelectorAll('button, a, [role="button"], input[type="file"], summary')]
    .filter(el => el.offsetParent);
  out.clickTargets = clickable.map(el => {
    const r = el.getBoundingClientRect();
    return { tag: el.tagName.toLowerCase(),
             text: (el.textContent || '').trim().slice(0, 22),
             cls: (el.className || '').toString().slice(0, 30),
             w: Math.round(r.width), h: Math.round(r.height) };
  });
  out.smallTargets = out.clickTargets.filter(t => t.h < 32 || t.w < 32).length;
  out.totalTargets = out.clickTargets.length;

  // 可见选项数（每个决策点的选择项）
  out.choiceGroups = {
    navButtons: document.querySelectorAll('nav button').length,
    visibleButtons: clickable.filter(e => e.tagName === 'BUTTON').length,
    inputs: [...document.querySelectorAll('input, select')].filter(e => e.offsetParent).length,
  };

  // 卡片与网格
  out.cards = document.querySelectorAll('.card').length;
  out.visibleCards = [...document.querySelectorAll('.card')].filter(e => e.offsetParent).length;

  // 硬阴影 / 圆角 / 边框 使用统计
  const radius = {}, shadow = {};
  document.querySelectorAll('body *').forEach(el => {
    if (!el.offsetParent) return;
    const c = cs(el);
    radius[c.borderRadius] = (radius[c.borderRadius] || 0) + 1;
    if (c.boxShadow && c.boxShadow !== 'none') shadow[c.boxShadow.slice(0, 60)] = (shadow[c.boxShadow.slice(0,60)] || 0) + 1;
  });
  out.radii = Object.entries(radius).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([k,v])=>({v:k,n:v}));
  out.shadowVariants = Object.keys(shadow).length;
  out.radiusVariants = Object.keys(radius).length;

  // 颜色使用（文字色 / 背景色）
  const fg = {}, bg = {};
  document.querySelectorAll('body *').forEach(el => {
    if (!el.offsetParent) return;
    if (!el.textContent || !el.textContent.trim()) return;
    const c = cs(el);
    fg[c.color] = (fg[c.color] || 0) + 1;
    if (c.backgroundColor !== 'rgba(0, 0, 0, 0)') bg[c.backgroundColor] = (bg[c.backgroundColor] || 0) + 1;
  });
  out.textColors = Object.entries(fg).sort((a,b)=>b[1]-a[1]).slice(0,14).map(([k,v])=>({c:k,n:v}));
  out.bgColors = Object.entries(bg).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([k,v])=>({c:k,n:v}));
  out.textColorVariants = Object.keys(fg).length;
  out.bgColorVariants = Object.keys(bg).length;

  // 首屏 h1/h2 层级
  out.headings = [...document.querySelectorAll('h1,h2,h3,h4')].filter(e=>e.offsetParent)
    .slice(0,40).map(e => ({ tag: e.tagName, size: Math.round(px(cs(e).fontSize)*10)/10,
                             weight: cs(e).fontWeight,
                             text: (e.textContent||'').trim().slice(0,30) }));

  // 文档高度（纵向滚动长度，反映"一屏塞多少"）
  out.docHeight = document.documentElement.scrollHeight;
  out.screensTall = +(document.documentElement.scrollHeight / innerHeight).toFixed(1);

  return out;
}
"""


def main():
    metrics = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 950}, device_scale_factor=1)
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)

        for theme in ("dark", "light"):
            pg.evaluate(f"() => {{ localStorage.setItem('agentsense-theme','{theme}'); }}")
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(2200)
            for t in TABS:
                pg.evaluate(f"() => App.switchTab('{t}')")
                pg.wait_for_timeout(2000)
                # 让 ECharts 有机会完成首帧
                pg.wait_for_timeout(1200)
                pg.screenshot(path=str(OUT / f"{theme}_{t}.png"), full_page=False)
                if theme == "dark":
                    m = pg.evaluate(METRICS_JS)
                    m["consoleErrors"] = errs[:20]
                    metrics[t] = m

        # 移动端视口（响应式检查）
        pg2 = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        pg2.goto(BASE, wait_until="domcontentloaded")
        pg2.wait_for_timeout(2500)
        for t in ("run", "foreign"):
            pg2.evaluate(f"() => App.switchTab('{t}')")
            pg2.wait_for_timeout(2000)
            pg2.screenshot(path=str(OUT / f"mobile_{t}.png"), full_page=False)
        m = pg2.evaluate(METRICS_JS)
        m["consoleErrors"] = []
        m["overflowX"] = pg2.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        metrics["mobile_run"] = m

        b.close()

    (ROOT / "probe" / "_ux_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")

    # 摘要打印
    lines = []
    for k, v in metrics.items():
        lines.append(f"### {k}  高={v['screensTall']}屏  doc={v['docHeight']}px")
        lines.append("  字号分布: " + ", ".join(f"{x['size']}px×{x['count']}" for x in v["fontSizes"][:10]))
        lines.append("  字重分布: " + ", ".join(f"{x['weight']}×{x['count']}" for x in v["fontWeights"][:6]))
        lines.append("  字体族: " + ", ".join(f"{x['family']}({x['count']})" for x in v["fontFamilies"][:4]))
        if v.get("paragraphs"):
            ps = v["paragraphs"]
            import statistics as st
            lines.append(f"  段落 {len(ps)} 个: 宽中位 {st.median([p['w'] for p in ps]):.0f}px, "
                         f"字符/行中位 {st.median([p['ch'] for p in ps]):.0f}, "
                         f"行高中位比 {st.median([p['ratio'] for p in ps]):.2f}")
        lines.append(f"  点击目标 {v['totalTargets']} 个, 其中 <32px 的 {v['smallTargets']} 个")
        lines.append(f"  圆角变体 {v['radiusVariants']} 种, 阴影变体 {v['shadowVariants']} 种, "
                     f"文字色 {v['textColorVariants']} 种, 背景色 {v['bgColorVariants']} 种")
        lines.append(f"  导航按钮 {v['choiceGroups']['navButtons']} 个, 可见按钮 {v['choiceGroups']['visibleButtons']} 个, 输入项 {v['choiceGroups']['inputs']} 个")
        lines.append(f"  卡片 .card 共 {v['cards']} 个（可见 {v['visibleCards']}）")
        if v.get("overflowX"):
            lines.append(f"  移动端横向溢出 {v['overflowX']}px")
        if v.get("consoleErrors"):
            lines.append(f"  控制台错误 {len(v['consoleErrors'])} 条: {v['consoleErrors'][:3]}")
        lines.append("  文字色 TOP: " + ", ".join(f"{x['c']}×{x['n']}" for x in v["textColors"][:6]))
        lines.append("")
    (ROOT / "probe" / "_ux_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("OK")


if __name__ == "__main__":
    main()
