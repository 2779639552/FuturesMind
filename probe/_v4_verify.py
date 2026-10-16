# -*- coding: utf-8 -*-
"""v4 验证：逐条对应用户八项反馈。"""
import json, colorsys, pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_v4")
OUT.mkdir(exist_ok=True)

def lum(rgb):
    def f(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])

def ratio(fg, bg):
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)

def parse(s):
    n = [float(x) for x in __import__('re').findall(r'[\d.]+', s or '')]
    return tuple(int(x) for x in n[:3]) if len(n) >= 3 else None

with sync_playwright() as p:
    b = p.chromium.launch()

    # ═══ 亮色：run 页 ═══
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.evaluate("localStorage.setItem('agentsense-theme','light')")
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_timeout(3500)

    print("═══ ① 控制条不再吸顶 ═══")
    r = pg.evaluate("""() => {
      const pt = document.querySelector('.page-tools');
      if (!pt) return 'no .page-tools';
      const before = Math.round(pt.getBoundingClientRect().top);
      const pos = getComputedStyle(pt).position;
      window.scrollTo(0, 600);
      const after = Math.round(pt.getBoundingClientRect().top);
      window.scrollTo(0, 0);
      return { position: pos, topBefore: before, topAfterScroll: after };
    }""")
    print(f"  position={r['position']} 滚动前 top={r['topBefore']} 滚动 600px 后 top={r['topAfterScroll']}")
    print(f"  {'OK 随页面滚走（不再冻结）' if r['position'] == 'static' and r['topAfterScroll'] < r['topBefore'] - 100 else '✗ 仍在吸顶'}")

    print("\n═══ ② 版本号 ═══")
    v = pg.evaluate("() => document.querySelector('.header-badge')?.textContent")
    print(f"  顶栏徽标 = {v!r}  {'OK' if v == 'v2.9' else '✗'}")

    print("\n═══ ③ 灰底文字对比度（AA 标准 4.5:1）═══")
    c = pg.evaluate("""() => {
      const vis = el => el.offsetParent !== null;
      const bgOf = (el) => {
        let n = el;
        while (n) {
          const bg = getComputedStyle(n).backgroundColor;
          const m = (bg || '').match(/[\\d.]+/g);
          if (m && (m.length < 4 || parseFloat(m[3]) > 0.5)) return bg;
          n = n.parentElement;
        }
        return 'rgb(255,255,255)';
      };
      const samples = [];
      ['#variety-info', '#run-input-body'].forEach(sel => {
        const el = document.querySelector(sel);
        if (el && vis(el)) samples.push({ sel, fg: getComputedStyle(el).color, bg: bgOf(el) });
      });
      // 卡片内所有次级文字
      document.querySelectorAll('.card span, .card div').forEach(el => {
        if (!vis(el) || el.children.length) return;
        const t = (el.textContent || '').trim();
        if (t.length < 3 || samples.length > 40) return;
        samples.push({ sel: (el.className || el.tagName).toString().slice(0, 20),
                       fg: getComputedStyle(el).color, bg: bgOf(el) });
      });
      return samples;
    }""")
    worst = []
    for s in c:
        fg, bg = parse(s["fg"]), parse(s["bg"])
        if fg and bg:
            worst.append((ratio(fg, bg), s["sel"], s["fg"], s["bg"]))
    worst.sort()
    print(f"  采样 {len(worst)} 处，对比度最低的 6 处：")
    for rt, sel, fg, bg in worst[:6]:
        flag = "OK" if rt >= 4.5 else ("偏弱" if rt >= 3 else "不足")
        print(f"    {rt:5}:1  {sel:<22} fg={fg:<20} bg={bg:<20} {flag}")
    bad = [w for w in worst if w[0] < 4.5]
    print(f"  未达 AA 的: {len(bad)} 处 / {len(worst)}")

    print("\n═══ ⑥ 分析深度 → 分段控件 ═══")
    seg = pg.evaluate("""() => {
      const s = document.querySelector('.seg');
      if (!s) return 'no .seg';
      const cs = getComputedStyle(s);
      const btns = [...s.querySelectorAll('.depth-preset')].map(x => ({
        txt: x.textContent.trim(), active: x.classList.contains('active'),
        bg: getComputedStyle(x).backgroundColor, color: getComputedStyle(x).color,
        radius: getComputedStyle(x).borderTopLeftRadius, h: Math.round(x.getBoundingClientRect().height),
      }));
      return { bg: cs.backgroundColor, radius: cs.borderTopLeftRadius, display: cs.display, btns };
    }""")
    print(f"  容器: 底={seg['bg']} 圆角={seg['radius']} display={seg['display']}")
    for x in seg["btns"]:
        print(f"    {x['txt']:<5} active={str(x['active']):<6} 底={x['bg']:<22} 字={x['color']:<22} 圆角={x['radius']:<8} 高={x['h']}")
    ok_seg = any(x["active"] and "29, 29, 31" in x["bg"] for x in seg["btns"])
    print(f"  {'OK 选中项为深色药丸（苹果分段控件）' if ok_seg else '✗ 选中项未变深'}")

    # 功能验证：点「轻量」
    pg.evaluate("() => document.querySelector('.seg .depth-preset[data-preset=\"light\"]').click()")
    pg.wait_for_timeout(600)
    r2 = pg.evaluate("""() => {
      const a = document.querySelector('.seg .depth-preset.active');
      return { active: a ? a.dataset.preset : null,
               debate: document.getElementById('mod-debate')?.checked,
               synth: document.getElementById('mod-synthesis')?.checked,
               scen: document.getElementById('mod-scenario')?.checked };
    }""")
    print(f"  点击「轻量」后: active={r2['active']} 辩论={r2['debate']} 综合={r2['synth']} 情景={r2['scen']}")
    print(f"  {'OK 切换功能正常' if r2['active'] == 'light' and r2['debate'] is False else '✗ 功能异常'}")
    pg.evaluate("() => document.querySelector('.seg .depth-preset[data-preset=\"full\"]').click()")
    pg.wait_for_timeout(400)

    print("\n═══ ④ 卡片内是否还有「框中框」═══")
    nf = pg.evaluate("""() => {
      const vis = el => el.offsetParent !== null;
      const out = [];
      document.querySelectorAll('.card *').forEach(d => {
        if (!vis(d)) return;
        const cs = getComputedStyle(d);
        const bw = parseFloat(cs.borderTopWidth) || 0;
        const r = parseFloat(cs.borderTopLeftRadius) || 0;
        const bg = cs.backgroundColor;
        const opaque = bg && bg !== 'rgba(0, 0, 0, 0)';
        const rc = d.getBoundingClientRect();
        if (rc.width > 200 && rc.height > 30 && (bw >= 1 || (opaque && r > 0))) {
          out.push({ tag: d.tagName.toLowerCase(), cls: (d.className || '').toString().slice(0, 22),
                     bw, bg, r, w: Math.round(rc.width), h: Math.round(rc.height) });
        }
      });
      return out;
    }""")
    print(f"  卡片内「有边框或带底+圆角」的块: {len(nf)} 个")
    for x in nf[:5]:
        print(f"    <{x['tag']}> bw={x['bw']} bg={x['bg']} r={x['r']} {x['w']}x{x['h']}")

    print(f"\n  控制台错误: {len(errs)}" + (": " + " | ".join(e[:70] for e in errs[:3]) if errs else ""))
    pg.screenshot(path=str(OUT / "light_run.png"))
    pg.close()

    # ═══ 外文研报页：⑤ 控件横排 + ⑦ 两列 ═══
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errs2 = []
    pg.on("pageerror", lambda e: errs2.append(str(e)))
    pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
    pg.evaluate("localStorage.setItem('agentsense-theme','light')")
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.evaluate("document.querySelector('nav button[data-tab=\"foreign\"]').click()")
    pg.wait_for_timeout(5000)

    print("\n═══ ⑦ 历史编译任务 → 两列 ═══")
    g = pg.evaluate("""() => {
      const box = document.getElementById('fg-list');
      if (!box) return 'no #fg-list';
      const cs = getComputedStyle(box);
      const kids = [...box.children].filter(k => k.offsetParent);
      const xs = [...new Set(kids.map(k => Math.round(k.getBoundingClientRect().x)))].sort((a,z)=>a-z);
      // 按 x 分组统计行数
      const colCount = xs.length;
      const boxRc = box.getBoundingClientRect();
      return { display: cs.display, cols: cs.gridTemplateColumns, n: kids.length,
               distinctX: xs, colCount, boxW: Math.round(boxRc.width),
               firstRowW: kids[0] ? Math.round(kids[0].getBoundingClientRect().width) : 0,
               halfW: Math.round(boxRc.width / 2) };
    }""")
    print(f"  display={g['display']} columns={g['cols']}")
    print(f"  子项 {g['n']} 个，出现的 x 坐标: {g['distinctX']}（{g['colCount']} 组）")
    print(f"  首项宽={g['firstRowW']} / 容器宽={g['boxW']}（半宽={g['halfW']}）")
    two_col = g["colCount"] == 2 and abs(g["firstRowW"] - g["halfW"]) < 60
    print(f"  {'OK 已是两列' if two_col else '✗ 仍是一列'}")

    print("\n═══ ⑤ 研报每日总结右侧控件是否横排 ═══")
    pg.evaluate("document.querySelector('nav button[data-tab=\"research\"]').click()")
    pg.wait_for_timeout(5000)
    ctl = pg.evaluate("""() => {
      const box = document.getElementById('res-daily-type')?.closest('div');
      if (!box) return 'not found';
      const rc = box.getBoundingClientRect();
      const kids = [...box.children].filter(k => k.offsetParent);
      const ys = [...new Set(kids.map(k => Math.round(k.getBoundingClientRect().y)))];
      return { w: Math.round(rc.width), h: Math.round(rc.height), n: kids.length,
               distinctY: ys, rows: ys.length,
               kids: kids.map(k => ({ tag: k.tagName.toLowerCase(), id: k.id || '',
                                      w: Math.round(k.getBoundingClientRect().width),
                                      y: Math.round(k.getBoundingClientRect().y) })) };
    }""")
    print(f"  控件组: {ctl['w']}x{ctl['h']}，{ctl['n']} 个子元素，占 {ctl['rows']} 行")
    for k in ctl["kids"]:
        print(f"    <{k['tag']}> {k['id'] or '-':<20} 宽={k['w']}")
    print(f"  {'OK 已横排（1 行）' if ctl['rows'] == 1 else ' 占 ' + str(ctl['rows']) + ' 行'}")
    print(f"  控制台错误: {len(errs2)}")
    pg.screenshot(path=str(OUT / "light_foreign.png"))
    # 研报页控件区放大截图
    el = pg.query_selector('#res-daily-type')
    if el:
        card = el.evaluate_handle("e => e.closest('.card')")
        try:
            card.as_element().screenshot(path=str(OUT / "zoom_daily_card.png"))
        except Exception as e:
            print("  截图失败:", e)
    pg.close()
    b.close()

print("\n截图 ->", OUT)
