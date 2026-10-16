# -*- coding: utf-8 -*-
"""v2 专项验证：逐条断言用户截图中提出的六个问题是否真的修好。
不靠肉眼 —— 每条都给实测数值。
"""
import json, colorsys, pathlib
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]
OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_v2")
OUT.mkdir(exist_ok=True)

def purple_hits(rgb):
    """判断 rgb 串是否属于紫色系（色相 248-315 且饱和 >0.15）。"""
    import re
    m = re.findall(r'[\d.]+', rgb or '')
    if len(m) < 3:
        return False
    r, g, b = (float(x) / 255 for x in m[:3])
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return s > 0.15 and 248 <= h * 360 <= 315

report = []
with sync_playwright() as p:
    b = p.chromium.launch()

    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.evaluate("localStorage.setItem('agentsense-theme', '%s')" % theme)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)
        pg.evaluate("document.fonts.ready")

        if theme == "light":
            # ══ 全局断言（只在亮色跑一次）══
            g = pg.evaluate("""() => {
              const vis = el => el.offsetParent !== null;
              const w = window.innerWidth;
              const main = document.querySelector('main');
              const mcs = getComputedStyle(main);
              const mrc = main.getBoundingClientRect();

              // 字体实测：用宽度指纹判断实际生效的字族
              const t = document.createElement('span');
              t.style.cssText = 'position:absolute;left:-9999px;top:0;font-size:100px;white-space:nowrap';
              t.textContent = 'Hamburgefonstiv';
              document.body.appendChild(t);
              const stack = getComputedStyle(document.body).fontFamily;
              const widths = {};
              for (const [k, v] of [['stack', stack], ['serif', 'serif'], ['inter', 'Inter'],
                                    ['noto', '"Noto Sans SC"'], ['yahei', '"Microsoft YaHei"']]) {
                t.style.fontFamily = v;
                widths[k] = Math.round(t.getBoundingClientRect().width * 100) / 100;
              }
              t.remove();

              // 卡片纵向间距：找同列上下相邻的卡片
              const cards = [...document.querySelectorAll('.card')].filter(vis)
                .map(c => { const r = c.getBoundingClientRect();
                            return { x: r.x, y: r.y, w: r.width, h: r.height, top: r.top + scrollY, bot: r.bottom + scrollY }; });
              cards.sort((a, z) => a.top - z.top);
              const gaps = [];
              for (let i = 1; i < cards.length; i++) {
                const a = cards[i-1], c = cards[i];
                const ox = Math.min(a.x + a.w, c.x + c.w) - Math.max(a.x, c.x);
                if (ox > Math.min(a.w, c.w) * 0.5 && c.top >= a.bot - 1) {
                  gaps.push(Math.round(c.top - a.bot));
                }
              }

              // 筛选器横向间距：同一父容器内相邻输入控件
              const fgaps = [];
              const rows = [...document.querySelectorAll('div')].filter(d => {
                const s = getComputedStyle(d);
                return s.display === 'flex' && d.querySelectorAll('input,select').length >= 2;
              });
              rows.forEach(row => {
                const fs = [...row.children].filter(vis);
                for (let i = 1; i < fs.length; i++) {
                  const a = fs[i-1].getBoundingClientRect(), c = fs[i].getBoundingClientRect();
                  if (Math.abs(a.top - c.top) < 4 && c.left >= a.right - 1) fgaps.push(Math.round(c.left - a.right));
                }
              });

              // 字号
              const sizes = {};
              document.querySelectorAll('*').forEach(el => {
                if (!vis(el) || el.children.length) return;
                if (!(el.textContent || '').trim()) return;
                const fs = getComputedStyle(el).fontSize;
                sizes[fs] = (sizes[fs] || 0) + 1;
              });
              const px = Object.keys(sizes).map(parseFloat);

              // 按钮高度
              const bh = [...document.querySelectorAll('.btn')].filter(vis)
                .map(e => Math.round(e.getBoundingClientRect().height))
                .filter(h => h > 0);

              // 紫色扫描
              let purple = [];
              document.querySelectorAll('*').forEach(el => {
                if (!vis(el)) return;
                const cs = getComputedStyle(el);
                purple.push([cs.color, cs.backgroundColor, cs.borderTopColor]);
              });

              return { viewportW: w, mainW: Math.round(mrc.width), mainMaxW: mcs.maxWidth,
                       mainPad: mcs.padding,
                       fonts: { stack, widths, interLoaded: document.fonts.check('16px Inter') },
                       cardGaps: gaps.slice(0, 30), cardGapMin: gaps.length ? Math.min(...gaps) : null,
                       formGaps: fgaps.slice(0, 20), formGapMin: fgaps.length ? Math.min(...fgaps) : null,
                       fontSizes: sizes, minFont: Math.min(...px), fontKinds: px.length,
                       btnHeights: [...new Set(bh)].sort((a,z)=>a-z),
                       purpleSample: purple };
            }""")

            print("═══ ① 宽度铺满 ═══")
            print(f"  视口宽 {g['viewportW']}px / main 宽 {g['mainW']}px / max-width={g['mainMaxW']} / padding={g['mainPad']}")
            print(f"  {'OK 铺满' if abs(g['mainW'] - g['viewportW']) <= 1 else '✗ 未铺满'}")

            print("\n═══ ② 实际生效字体 ═══")
            f = g['fonts']
            print(f"  font-family 栈: {f['stack'][:110]}...")
            print(f"  Inter 已加载(document.fonts.check): {f['interLoaded']}")
            print(f"  宽度指纹 'Hamburgefonstiv'@100px: {json.dumps(f['widths'], ensure_ascii=False)}")
            w = f['widths']
            identical_to_inter = abs(w['stack'] - w['inter']) < 0.6
            print(f"  {'OK 实际用的是 Inter' if identical_to_inter else '✗ 实际不是 Inter（命中 '+str(w['stack'])+'，Inter 应为 '+str(w['inter'])+'）'}")

            print("\n═══ ③ 卡片间距（修复「贴在一起」）═══")
            print(f"  同列相邻卡片间距: {g['cardGaps']}")
            print(f"  最小值 = {g['cardGapMin']}px  {'OK 全部有间距' if g['cardGapMin'] and g['cardGapMin'] >= 12 else '✗ 仍有贴一起'}")

            print("\n═══ ④ 筛选器间距 ═══")
            print(f"  相邻表单控件横向间距: {g['formGaps']}")
            print(f"  最小值 = {g['formGapMin']}px  {'OK' if g['formGapMin'] is None or g['formGapMin'] >= 10 else '✗ 过窄'}")

            print("\n═══ ⑤ 字号 ═══")
            print(f"  最小字号 {g['minFont']}px / 档位数 {g['fontKinds']}")
            print(f"  分布: {json.dumps(g['fontSizes'], ensure_ascii=False)}")

            print("\n═══ ⑥ 按钮高度统一 ═══")
            print(f"  出现的高度集合: {g['btnHeights']}  {'OK 统一' if len(g['btnHeights']) <= 2 else '✗ 不统一'}")

            print("\n═══ ⑦ 紫色残留（计算样式扫描）═══")
            ph = [x for x in g['purpleSample'] if purple_hits(x[0]) or purple_hits(x[1]) or purple_hits(x[2])]
            print(f"  命中紫色系的元素: {len(ph)}")
            for x in ph[:5]:
                print(f"    {x}")

        # ══ 逐页签：溢出 / 控制台错误 / 字号地板 ══
        rows = []
        for t in TABS:
            pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
            pg.wait_for_timeout(2400)
            m = pg.evaluate("""() => {
              const vis = el => el.offsetParent !== null;
              const sizes = {};
              document.querySelectorAll('*').forEach(el => {
                if (!vis(el) || el.children.length) return;
                if (!(el.textContent||'').trim()) return;
                const fs = getComputedStyle(el).fontSize; sizes[fs] = (sizes[fs]||0)+1;
              });
              const px = Object.keys(sizes).map(parseFloat);
              return { docH: document.documentElement.scrollHeight,
                       overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                       minFont: Math.min(...px), fontKinds: px.length };
            }""")
            rows.append((theme, t, m))
            pg.screenshot(path=str(OUT / f"{theme}_{t}.png"))
        report.append((theme, rows, errs))
        pg.close()

    # ══ 移动端 ══
    mob = []
    for tab in ("run", "research"):
        pg = b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2200)
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(2400)
        m = pg.evaluate("""() => { const n = document.querySelector('nav#nav');
          const rc = n.getBoundingClientRect();
          return { navBottom: Math.round(rc.bottom), winH: window.innerHeight,
                   navTop: Math.round(rc.y),
                   overflowX: document.documentElement.scrollWidth > window.innerWidth + 1 }; }""")
        mob.append((tab, m))
        pg.screenshot(path=str(OUT / f"mobile_{tab}.png"))
        pg.close()
    b.close()

print("\n\n═══ 逐页签（9 × 双主题）═══")
print(f"{'主题':<6}{'页签':<11}{'页高':>8}{'横向溢出':>10}{'最小字':>8}{'字号档':>8}")
for theme, rows, errs in report:
    for th, t, m in rows:
        print(f"{th:<6}{t:<11}{m['docH']:>8}{str(m['overflowX']):>10}{m['minFont']:>8}{m['fontKinds']:>8}")
print()
for theme, rows, errs in report:
    print(f"  控制台错误 [{theme}]: {len(errs)} 条" + (": " + " | ".join(e[:80] for e in errs[:4]) if errs else ""))
print()
print("═══ 移动端底栏 ═══")
for tab, m in mob:
    ok = abs(m['navBottom'] - m['winH']) <= 2
    print(f"  {tab:<11} navBottom={m['navBottom']} winH={m['winH']} 溢出={m['overflowX']} {'OK 贴底' if ok else '✗ 未贴底'}")
