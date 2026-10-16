# -*- coding: utf-8 -*-
"""v3 验证：逐条对应用户五点需求 + 回归测试。"""
import json, pathlib
from playwright.sync_api import sync_playwright

TABS = ["run", "research", "sentiment", "quant", "fund", "warrant", "foreign", "warehouse", "gtja"]
OUT = pathlib.Path(r"D:\AgentSense\AgentSense\probe\_v3")
OUT.mkdir(exist_ok=True)

def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.evaluate("localStorage.setItem('agentsense-theme', '%s')" % theme)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2400)

        if theme == "light":
            r = pg.evaluate("""() => {
              const vis = el => el.offsetParent !== null;
              // ③ tab
              const navBtn = [...document.querySelectorAll('nav button')].filter(vis);
              const act = navBtn.find(x => x.classList.contains('active')) || navBtn[0];
              const idle = navBtn.find(x => x !== act);
              const acs = getComputedStyle(act), ics = getComputedStyle(idle);
              // ④ 主按钮
              const pri = [...document.querySelectorAll('.btn-primary')].filter(vis)[0];
              const pcs = pri ? getComputedStyle(pri) : null;
              // ⑤ KPI
              const vals = [...document.querySelectorAll('.stat-val')].filter(vis)
                .map(e => ({ txt: e.textContent.trim(), fs: parseFloat(getComputedStyle(e).fontSize) }));
              // ① 表格
              const tables = [...document.querySelectorAll('table')].filter(vis).map(t => {
                const tc = t.getBoundingClientRect();
                const card = t.closest('.card');
                const cc = card ? card.getBoundingClientRect() : null;
                return { w: Math.round(tc.width), cardW: cc ? Math.round(cc.width) : 0,
                         ratio: cc ? Math.round(tc.width / cc.width * 100) / 100 : null };
              });
              // ② 空间利用率：首屏可见卡片的填充密度
              const cards = [...document.querySelectorAll('.card')].filter(vis).map(c => {
                const rc = c.getBoundingClientRect();
                const kids = [...c.children].reduce((s, k) => s + k.getBoundingClientRect().height, 0);
                return { h: Math.round(rc.height), contentH: Math.round(kids),
                         fill: rc.height > 0 ? Math.round(kids / rc.height * 100) / 100 : 0 };
              });
              const pagH = document.documentElement.scrollHeight;
              const firstScreen = cards.filter(c => c.h > 0);
              return {
                nav: { activeColor: acs.color, activeBg: acs.backgroundColor,
                       activeBorderB: acs.borderBottomWidth, activeShadow: acs.boxShadow,
                       idleColor: ics.color, idleBg: ics.backgroundColor,
                       fontSize: acs.fontSize, activeWeight: acs.fontWeight, h: Math.round(act.getBoundingClientRect().height) },
                primaryBtn: pcs ? { bg: pcs.backgroundColor, color: pcs.color, h: Math.round(pri.getBoundingClientRect().height),
                                    radius: pcs.borderRadius } : null,
                kpi: { count: vals.length, maxFont: vals.length ? Math.max(...vals.map(v => v.fs)) : 0,
                       fonts: [...new Set(vals.map(v => v.fs))], sample: vals.slice(0, 6) },
                tables,
                cardFill: { avg: firstScreen.length ? Math.round(firstScreen.reduce((s,c)=>s+c.fill,0)/firstScreen.length*100)/100 : 0,
                            n: firstScreen.length,
                            worst: firstScreen.sort((a,b)=>a.fill-b.fill).slice(0,3) },
                pageH: pagH,
                cardGap: (() => {
                  const cs = [...document.querySelectorAll('.card')].filter(vis)
                    .map(c => { const r = c.getBoundingClientRect(); return {x:r.x,w:r.width,top:r.top+scrollY,bot:r.bottom+scrollY}; })
                    .sort((a,z)=>a.top-z.top);
                  const g = [];
                  for (let i=1;i<cs.length;i++){ const a=cs[i-1],c=cs[i];
                    const ox=Math.min(a.x+a.w,c.x+c.w)-Math.max(a.x,c.x);
                    if(ox>Math.min(a.w,c.w)*0.5 && c.top>=a.bot-1) g.push(Math.round(c.top-a.bot)); }
                  return { min: g.length?Math.min(...g):null, vals: g.slice(0,12) };
                })(),
              };
            }""")

            print("═══ ③ 顶部 tab（苹果式）═══")
            nv = r["nav"]
            print(f"  激活态: 字色={nv['activeColor']} 底色={nv['activeBg']} 下边框={nv['activeBorderB']} 阴影={nv['activeShadow']} 字重={nv['activeWeight']}")
            print(f"  未激活: 字色={nv['idleColor']} 底色={nv['idleBg']}")
            print(f"  字号={nv['fontSize']} 高度={nv['h']}px")
            transparent = nv["activeBg"] in ("rgba(0, 0, 0, 0)", "transparent")
            no_shadow = nv["activeShadow"] == "none"
            print(f"  {'OK 无药丸底+无阴影，激活只靠字色（苹果做法）' if transparent and no_shadow else '✗ 仍有底色/阴影'}")

            print("\n═══ ④ 主按钮颜色 ═══")
            pb = r["primaryBtn"]
            print(f"  {pb}")
            if pb:
                rgb = pb["bg"].replace("rgb(", "").replace(")", "").split(",")
                dark_bg = all(int(x.strip()) < 60 for x in rgb[:3])
                print(f"  {'OK 已是黑色' if dark_bg else '✗ 仍不是黑色'}（护眼主题下应为黑底白字）")

            print("\n═══ ⑤ KPI 字号（不应再是大号数字）═══")
            k = r["kpi"]
            print(f"  共 {k['count']} 个指标，字号集合={k['fonts']}，最大 {k['maxFont']}px")
            print("  样例:", json.dumps(k["sample"], ensure_ascii=False))
            print(f"  {'OK 已收敛到 ≤17px' if k['maxFont'] <= 17 else '✗ 仍有大号数字 ' + str(k['maxFont'])}")

            print("\n═══ ① 表格宽度（应为卡片可用宽的 ~100%）═══")
            for t in r["tables"]:
                print(f"  表宽={t['w']} 卡片宽={t['cardW']} 占比={t['ratio']}")
            bad = [t for t in r["tables"] if t["ratio"] is not None and t["ratio"] < 0.9]
            print(f"  {'OK 全部铺满' if not bad else '✗ ' + str(len(bad)) + ' 张表仍偏窄'}")

            print("\n═══ ② 空间利用率（卡片内容填充率）═══")
            cf = r["cardFill"]
            print(f"  可见卡片 {cf['n']} 张，平均填充率 {cf['avg']}")
            print(f"  最低 3 张: {cf['worst']}")
            print(f"  卡片间距: 最小={r['cardGap']['min']} 取值={r['cardGap']['vals']}")

        # 逐页签回归
        rows = []
        for t in TABS:
            pg.evaluate(f"document.querySelector('nav button[data-tab=\"{t}\"]').click()")
            pg.wait_for_timeout(2600)
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
                       minFont: Math.min(...px), kinds: px.length };
            }""")
            rows.append((theme, t, m))
            pg.screenshot(path=str(OUT / f"{theme}_{t}.png"))
        print(f"\n═══ 回归 · {theme} ═══")
        for th, t, m in rows:
            flag = "" if not m["overflowX"] and m["minFont"] >= 12 else "  ← 注意"
            print(f"  {t:<11} 页高={m['docH']:>6} 溢出={str(m['overflowX']):<6} 最小字={m['minFont']:<6} 档位={m['kinds']}{flag}")
        print(f"  控制台错误: {len(errs)} 条" + (": " + " | ".join(e[:70] for e in errs[:3]) if errs else ""))
        pg.close()

    # 移动端
    for tab in ("run", "warrant"):
        pg = b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg.goto("http://127.0.0.1:5000/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2400)
        pg.evaluate(f"document.querySelector('nav button[data-tab=\"{tab}\"]').click()")
        pg.wait_for_timeout(3000)
        m = pg.evaluate("""() => { const n=document.querySelector('nav#nav'); const rc=n.getBoundingClientRect();
          return { navBottom: Math.round(rc.bottom), winH: window.innerHeight,
                   overflowX: document.documentElement.scrollWidth > window.innerWidth + 1 }; }""")
        print(f"\n═══ 移动端 {tab} ═══ navBottom={m['navBottom']} winH={m['winH']} 溢出={m['overflowX']} "
              f"{'OK 贴底' if abs(m['navBottom']-m['winH'])<=2 else '✗ 未贴底'}")
        pg.screenshot(path=str(OUT / f"mobile_{tab}.png"))
        pg.close()
    b.close()
print("\n截图 ->", OUT)
