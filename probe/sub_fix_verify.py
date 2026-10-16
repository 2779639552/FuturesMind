# -*- coding: utf-8 -*-
"""验证 #12：分析工具子面板(asub-*)在筛选后能真正显示(非 0 高度)。
运行: python probe/sub_fix_verify.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"

def show(msg):
    print(msg.encode("gbk", errors="replace").decode("gbk"))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 960})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(1500)

    # 切到情绪信号 tab(分析工具在本 tab 内)
    pg.evaluate("App.switchTab('sentiment')")
    pg.wait_for_timeout(2500)
    pg.evaluate("App._buildSectionIndex('sentiment')")
    pg.wait_for_timeout(600)

    rows = pg.evaluate("""() => (App._secRows['sentiment']||[]).map((r,i)=>i+' '+r.label)""")
    show("── 索引条目 ──")
    for r in rows: show("  " + r)

    # 找出含「分析工具」的条目下标
    sub_idx = pg.evaluate("""() => {
      const rows = App._secRows['sentiment']||[];
      return rows.map((r,i)=>[i,r.label]).filter(([i,l])=>l.includes('分析工具')).map(([i,l])=>i);
    }""")
    show(f"\n分析工具条目下标: {sub_idx}")
    if not sub_idx:
        show("!! 没有分析工具条目")
        b.close(); sys.exit(1)

    # 逐个选中「分析工具 · X」，检查高度与 display
    for i in sub_idx:
        pg.evaluate("t => App._resetSecFilter(t)", "sentiment")
        pg.wait_for_timeout(300)
        res = pg.evaluate("""(i) => {
          const tab = document.getElementById('tab-sentiment');
          const idx = tab.querySelector('.sec-index');
          const rows = App._secRows['sentiment']||[];
          const r = rows[i];
          r.btn.dispatchEvent(new MouseEvent('click', {ctrlKey:true, bubbles:true}));
          const out = {label:r.label, blk:null, subs:[], realH:0};
          const b = (r.blocks||[])[0];
          out.blk = b ? {cls:b.className, h:b.offsetHeight, disp:getComputedStyle(b).display,
                         maxH:getComputedStyle(b).maxHeight} : null;
          // 真实可读高度: 面板内第一个 .card 的高度(0 = 内容被裁没了)
          const card = b && b.querySelector('.card');
          out.realH = card ? card.offsetHeight : (b ? b.scrollHeight : 0);
          tab.querySelectorAll('.analysis-sub').forEach(s=>{
            out.subs.push({id:s.id, disp:s.style.display, h:s.offsetHeight,
                           vis:getComputedStyle(s).display});
          });
          return out;
        }""", i)
        show(f"\n选中 [{i}] {res['label']}")
        show(f"   block: {res['blk']}")
        show(f"   内容真实高: {res['realH']}px  {'★OK' if res['realH']>50 else '!!被裁'}")
        for s in res['subs']:
            flag = "★" if s['vis'] != 'none' and s['h'] > 0 else " "
            show(f"   {flag} {s['id']:26s} inline={s['disp']:6s} vis={s['vis']:6s} h={s['h']}px")

    # 多选两个子面板
    pg.evaluate("t => App._resetSecFilter(t)", "sentiment")
    pg.wait_for_timeout(300)
    pg.evaluate("""(ids) => {
      const rows = App._secRows['sentiment']||[];
      ids.forEach(i => {
        rows[i].btn.dispatchEvent(new MouseEvent('click',{ctrlKey:true,bubbles:true}));
      });
    }""", sub_idx[:2])
    pg.wait_for_timeout(400)
    multi = pg.evaluate("""() => {
      const tab = document.getElementById('tab-sentiment');
      const out = [];
      tab.querySelectorAll('.analysis-sub').forEach(s=>{
        out.push({id:s.id, vis:getComputedStyle(s).display, h:s.offsetHeight});
      });
      return out;
    }""")
    show(f"\n── 多选 {sub_idx[:2]} ──")
    for s in multi:
        flag = "★可见" if s['vis'] != 'none' and s['h'] > 0 else "  隐藏"
        show(f"   {flag} {s['id']:26s} h={s['h']}px")

    # 清空后还原检查
    pg.evaluate("t => App._resetSecFilter(t)", "sentiment")
    pg.wait_for_timeout(500)
    back = pg.evaluate("""() => {
      const tab = document.getElementById('tab-sentiment');
      const out = {subs:[], hid:0, nav:null};
      tab.querySelectorAll('.analysis-sub').forEach(s=>{
        out.subs.push({id:s.id, vis:getComputedStyle(s).display, h:s.offsetHeight});
      });
      out.hid = tab.querySelectorAll('.sec-hidden').length;
      const nav = tab.querySelector('#analysis-subnav');
      out.nav = nav ? getComputedStyle(nav).display : 'N/A';
      return out;
    }""")
    show("\n── 清空筛选后还原 ──")
    show(f"   残留 sec-hidden: {back['hid']} (应为 0)")
    show(f"   子导航显示: {back['nav']} (不应为 none)")
    for s in back['subs']:
        flag = "★可见" if s['vis'] != 'none' else "  隐藏"
        show(f"   {flag} {s['id']:26s} h={s['h']}px")

    show(f"\n控制台错误: {len(errs)}")
    for e in errs[:5]: show("  " + e)
    b.close()
