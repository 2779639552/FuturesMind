// E2E 验收: 页级控制条常驻吸顶 + 小模块可独立完成
// 用法: node probe/_e2e_page_tools.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const TABS = ['run', 'research', 'sentiment', 'quant', 'fund', 'warrant', 'gtja', 'variety'];

let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// ── 1) 每个页签: 控制条存在 / 在首位 / 可见 / 不参与折叠 ─────────
console.log('\n[1] 控制条落位与可见性');
for (const t of TABS) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(1600);
  const r = await page.evaluate(tab => {
    const el = document.getElementById('tab-' + tab);
    if (!el) return { err: 'no tab' };
    const pt = el.querySelector(':scope > .page-tools');
    if (!pt) return { err: 'no .page-tools (不在页签首位层级)' };
    const idx = el.querySelector('.sec-index');
    const kids = Array.from(el.children);
    const h = pt.getBoundingClientRect().height;
    return {
      h: Math.round(h),
      visible: pt.offsetParent !== null,
      inBlock: !!pt.closest('.sec-block'),
      inIdx: !!el.querySelector('.sec-index .page-tools'),
      order: kids.map(k => k.className.split(' ')[0] || k.tagName).slice(0, 3),
      idxBefore: idx ? kids.indexOf(idx) < kids.indexOf(pt) : null,
      collapsedSelf: pt.classList.contains('is-collapsed'),
    };
  }, t);
  if (r.err) { log(false, `${t}: ${r.err}`); continue; }
  log(!r.inBlock && !r.inIdx, `${t}: 控制条在首位层级(非折叠块内) inBlock=${r.inBlock} inIdx=${r.inIdx} order=${r.order.join('>')}`);
  log(r.visible && r.h > 30, `${t}: 控制条可见且高>30px (h=${r.h}, visible=${r.visible})`);
  log(r.idxBefore === true && !r.collapsedSelf, `${t}: 位于小模块索引之上(idxBefore=${r.idxBefore}) 且自身未被折叠`);
}

// ── 2) 吸附: 滚动后仍停在视口顶部 ─────────────────────────────
// ⚠️ 必须先展开全部模块把页面撑高 —— 否则折叠态下文档比视口还短,
//    scrollTo(0,900) 会被夹到 0, 吸顶根本无从验证(会误报 FAIL)。
console.log('\n[2] 吸顶(滚动 900px 后仍在视口顶部)');
for (const t of ['sentiment', 'quant', 'variety']) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(1600);
  const scrolled = await page.evaluate(async (tab) => {
    const el = document.getElementById('tab-' + tab);
    el.querySelectorAll('.sec-block.is-collapsed').forEach(b => {
      b.classList.remove('is-collapsed'); b.classList.add('sec-open'); b.style.maxHeight = 'none';
    });
    await new Promise(r => setTimeout(r, 300));
    window.scrollTo(0, 900);
    await new Promise(r => setTimeout(r, 400));
    const pt = el.querySelector(':scope > .page-tools');
    return {
      scrollY: Math.round(window.scrollY),
      maxScroll: Math.round(document.documentElement.scrollHeight - window.innerHeight),
      top: Math.round(pt.getBoundingClientRect().top),
    };
  }, t);
  // 只有真的滚动起来了才判定吸顶; 否则说明页面仍不够高(跳过并如实标注)
  if (scrolled.scrollY < 200) {
    log(false, `${t}: 页面可滚高度不足(maxScroll=${scrolled.maxScroll}), 未能验证吸顶`);
  } else {
    log(scrolled.top >= 0 && scrolled.top <= 90,
        `${t}: 滚动到 ${scrolled.scrollY}px 时控制条 top=${scrolled.top}px (期望 0~90, 即吸在导航栏下方)`);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

// ── 3) 小模块筛选: 只勾一个模块, 控制条必须还在 ──────────────
console.log('\n[3] 筛选到单模块后控制条仍可见');
for (const t of ['sentiment', 'quant', 'warrant']) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(1800);
  const r = await page.evaluate(async (tab) => {
    const el = document.getElementById('tab-' + tab);
    const rows = (App._secRows || {})[tab] || [];
    if (!rows.length) return { err: 'no rows' };
    const chip = rows[0].btn;
    chip.click();                                  // 勾选第一个模块
    await new Promise(r => setTimeout(r, 700));
    const pt = el.querySelector(':scope > .page-tools');
    const pb = pt.getBoundingClientRect();
    const hiddenSiblings = rows.filter(x => x.btn.classList.contains('is-on')).length;
    return {
      ptHidden: pt.classList.contains('sec-hidden'),
      ptVisible: pt.offsetParent !== null,
      ptH: Math.round(pb.height),
      selCount: hiddenSiblings,
      ptTop: Math.round(pb.top),
    };
  }, t);
  if (r.err) { log(false, `${t}: ${r.err}`); continue; }
  log(!r.ptHidden && r.ptVisible && r.ptH > 30,
      `${t}: 勾选 ${r.selCount} 个模块后 控制条 sec-hidden=${r.ptHidden} visible=${r.ptVisible} h=${r.ptH}`);
  // 复位
  await page.evaluate(tab => App._resetSecFilter(tab), t);
  await page.waitForTimeout(500);
}

// ── 4) 单独勾选一个"内容模块" → 能出数(非加载中/非空) ──────────
console.log('\n[4] 单独勾选一个内容模块后可独立出数');
for (const t of ['quant', 'warrant', 'sentiment']) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(2000);
  const r = await page.evaluate(async (tab) => {
    const rows = (App._secRows || {})[tab] || [];
    // 找"看起来是内容模块"的行(非按钮条): 取其中一个勾选
    let target = null;
    for (const x of rows) {
      const h = Math.max(0, ...(x.blocks || []).map(b => b.scrollHeight));
      if (h >= 200) { target = x; break; }
    }
    if (!target) return { err: '无内容模块' };
    App._resetSecFilter(tab);
    await new Promise(r => setTimeout(r, 300));
    target.btn.click();
    await new Promise(r => setTimeout(r, 6000));
    const b = (target.blocks || [])[0];
    const txt = (b.textContent || '').replace(/\s+/g, ' ').trim();
    const charts = Array.from(b.querySelectorAll('.chart-container'))
      .filter(c => c.offsetParent !== null)
      .map(c => ({ id: c.id, w: c.clientWidth, h: c.clientHeight }));
    return {
      label: target.label.slice(0, 22), len: txt.length,
      loading: /加载中|加载失败/.test(txt),
      chartOk: charts.length === 0 ? null : charts.every(c => c.w > 0 && c.h > 0),
      nCharts: charts.length,
    };
  }, t);
  if (r.err) { log(false, `${t}: ${r.err}`); continue; }
  // ⚠️ 判据不能只看文本长度：像「K 线图」这种纯图表卡，文本天生只有标题+日期
  //    （实测 52 字），但 canvas 1510×520 已正常渲染。故改为「有实质内容」的双条件：
  //    文本够长 或 存在已渲染且尺寸正常的图表。
  const hasText = r.len > 60;
  const hasChart = r.nCharts > 0 && r.chartOk === true;
  log(!r.loading && (hasText || hasChart),
      `${t}: 单独勾选「${r.label}」→ 文本 ${r.len} 字, 加载中=${r.loading}, `
      + `图表 ${r.nCharts} 个 sizing=${r.chartOk}（判据: 文本>60 或 图表已渲染）`);
  await page.evaluate(tab => App._resetSecFilter(tab), t);   // ⚠️ 循环变量是 t，不是 tab
}

// ── 5) 品种状态: 控制条换品种 → 依赖它的模块跟着变 ──────────────
console.log('\n[5] 控制条换品种后依赖模块同步');
await page.evaluate(() => App.switchTab('run'));
await page.waitForTimeout(2500);
const r5 = await page.evaluate(async () => {
  const sel = document.getElementById('sym');
  const codes = Array.from(sel.options).map(o => o.value);
  const before = sel.value;
  const other = codes.find(c => c !== before);
  sel.value = other;
  await App.updateSentimentStats();
  App.loadRunInputDash();
  await new Promise(r => setTimeout(r, 12000));
  const body = document.getElementById('run-input-body').textContent.replace(/\s+/g, ' ').trim();
  return {
    before, after: other, n: codes.length,
    bodyLen: body.length, loading: /加载中/.test(body),
    stateLayer: App._curVariety('sym'),
    raw: document.getElementById('sym').value,
    bodyHead: body.slice(0, 70),
  };
});
log(r5.n === 21, `控制条下拉含全部 ${r5.n} 个品种(期望 21)`);
log(r5.stateLayer === r5.after && r5.raw === r5.after,
    `状态层与控件一致: _curVariety('sym')=${r5.stateLayer} / #sym=${r5.raw} (切到 ${r5.after})`);
log(!r5.loading && r5.bodyLen > 200,
    `换品种后「分析输入数据」已刷新(${r5.bodyLen} 字) head="${r5.bodyHead}"`);

// ── 6) 元素被移除时状态层兜底(独立模块场景) ────────────────────
console.log('\n[6] 品种控件不可用时状态层兜底(保证模块仍能取到品种)');
const r6 = await page.evaluate(() => {
  const sel = document.getElementById('sym');
  const keep = sel.value;
  const holder = sel.parentNode;
  const next = sel.nextSibling;
  holder.removeChild(sel);                        // 临时摘掉控件(模拟"该小模块没渲染")
  const vMissing = App._curVariety('sym');
  holder.insertBefore(sel, next);                 // 还原
  return { keep, vMissing, after: App._curVariety('sym') };
});
log(!!r6.vMissing, `控件被摘除后 _curVariety('sym') 仍返回 '${r6.vMissing}'(不落空, 模块不会取错品种)`);
log(r6.after === r6.keep, `控件还原后回到原值 '${r6.after}'`);

// ── 7) 无幽灵引用 ────────────────────────────────────────────
console.log('\n[7] 幽灵引用清理');
const r7 = await page.evaluate(() => ({
  dom: document.querySelectorAll('#trade-variety').length,
  anyPageToolsInBlocks: document.querySelectorAll('.sec-block .page-tools').length,
  subnavSticky: getComputedStyle(document.getElementById('analysis-subnav')).position,
}));
log(r7.dom === 0, `DOM 中 #trade-variety 数量 = ${r7.dom} (期望 0)`);
log(r7.anyPageToolsInBlocks === 0, `被折叠外壳包住的控制条 = ${r7.anyPageToolsInBlocks} (期望 0)`);
log(r7.subnavSticky === 'sticky', `#analysis-subnav position=${r7.subnavSticky} (期望 sticky)`);

// ── 8) 国君页「覆盖矩阵」: 必须进入小模块 + 不吸顶遮挡 ──────────
// 【为什么单列一段】覆盖矩阵曾被放在 .page-tools 内部 ⇒ ①_secScan 整块跳过它,
//   索引里没有它; ②.page-tools 吸顶 + 实色底, 21 行大表格整块钉在顶部遮住下方模块。
console.log('\n[8] 国君页覆盖矩阵(进小模块 + 不吸顶)');
{
  await page.evaluate(() => App.switchTab('gtja'));
  await page.waitForTimeout(2200);
  const r = await page.evaluate(() => {
    const el = document.getElementById('tab-gtja');
    const mat = document.getElementById('gt-matrix');
    const card = mat ? mat.closest('.card') : null;
    const pt = el.querySelector(':scope > .page-tools');
    const kids = Array.from(el.children);
    // 索引里的标签文字
    const labels = Array.from(el.querySelectorAll('.sec-index-list .sec-index-item'))
      .map(b => (b.textContent || '').replace('✓', '').trim());
    // 覆盖矩阵卡是否被 .page-tools 包着
    const inPageTools = !!(card && card.closest('.page-tools'));
    // 是否被折叠外壳包住(应为是 —— 它现在是小模块)
    const inBlock = !!(card && card.closest('.sec-block'));
    // 吸顶容器高度: 只应包含"① 查询控制"一张矮卡
    const ptH = pt ? Math.round(pt.getBoundingClientRect().height) : -1;
    // 控制条内卡片数(应=1)
    const ptCards = pt ? pt.querySelectorAll(':scope > .card').length : -1;
    // 覆盖矩阵卡自身 position(不应 sticky)
    const matCardPos = card ? getComputedStyle(card).position : 'n/a';
    // badge 位置(应挂在覆盖矩阵标题下)
    const badge = document.getElementById('gt-matrix-badge');
    const badgeInMatrixH3 = !!(badge && badge.closest('h3') &&
      /覆盖矩阵/.test(badge.closest('h3').textContent || ''));
    return {
      hasMatrix: !!mat, inPageTools, inBlock, ptH, ptCards, matCardPos,
      labels, badgeInMatrixH3,
      inIdx: !!el.querySelector('.sec-index-list'),
    };
  });
  log(r.hasMatrix, '#gt-matrix 存在');
  log(r.inPageTools === false, `覆盖矩阵已移出 .page-tools (inPageTools=${r.inPageTools})`);
  log(r.inBlock === true, `覆盖矩阵已进小模块折叠块 (inBlock=${r.inBlock})`);
  log(r.inIdx && r.labels.some(s => /覆盖矩阵/.test(s)),
      `索引中出现「覆盖矩阵」: [${r.labels.join(' | ')}]`);
  log(r.ptCards === 1, `控制条内只余 1 张矮卡 (ptCards=${r.ptCards})`);
  log(r.ptH > 0 && r.ptH < 200, `控制条高度合理、不会遮屏 (h=${r.ptH}px)`);
  log(r.matCardPos !== 'sticky', `覆盖矩阵卡 position=${r.matCardPos} (期望非 sticky)`);
  log(r.badgeInMatrixH3, `badge #gt-matrix-badge 挂在「覆盖矩阵」标题下 (${r.badgeInMatrixH3})`);

  // 数据集: 展开覆盖矩阵后，滚动页面，验证它**不**跟随吸顶
  const stick = await page.evaluate(async () => {
    const el = document.getElementById('tab-gtja');
    // 先确保矩阵可见
    const card = document.getElementById('gt-matrix').closest('.card');
    const blk = card.closest('.sec-block');
    if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
    await new Promise(r => requestAnimationFrame(r));
    const before = card.getBoundingClientRect().top;
    window.scrollTo(0, 900);
    await new Promise(r => setTimeout(r, 260));
    const after = card.getBoundingClientRect().top;
    const pt = el.querySelector(':scope > .page-tools');
    const ptAfter = pt ? pt.getBoundingClientRect().top : null;
    window.scrollTo(0, 0);
    return { before: Math.round(before), after: Math.round(after), ptAfter: ptAfter === null ? null : Math.round(ptAfter) };
  });
  // 覆盖矩阵应随页面滚走(top 明显上移 ⇒ 负值)，而不是被钉住
  log(stick.after < stick.before - 100 || stick.after < 0,
      `覆盖矩阵随页面滚动上移、未吸顶 (before=${stick.before}px → after=${stick.after}px)`);
  log(stick.ptAfter !== null && stick.ptAfter >= 40 && stick.ptAfter <= 80,
      `控制条仍正常吸顶 (滚动后 top=${stick.ptAfter}px, 期望≈56px)`);
}


// ── 9) 国君页三卡合并: 总览/走势图/明细 同卡 + 点「查看」就地出数 ────
// 【为什么单列一段】④⑤ 原本各自成卡且只由 ③ 的「查看」驱动 ⇒ 点一次要靠
//   _gtEnsureVisible 展开下面两块, 三块还各占一条索引项。合并后应满足:
//   三容器同卡 / 卡内只有一个 h3 / 点「查看」后图表非 0×0 且明细出数。
console.log('\n[9] 国君页三卡合并(同卡 + 就地出数)');
{
  await page.evaluate(() => App.switchTab('gtja'));
  await page.waitForTimeout(1600);
  // 等指标总览出数
  for (let i = 0; i < 40; i++) {
    const ok = await page.evaluate(() => {
      const el = document.getElementById('gt-indicators');
      return !!el && el.querySelectorAll('tbody tr').length > 0;
    });
    if (ok) break;
    await page.waitForTimeout(2000);
  }
  const st = await page.evaluate(() => {
    const ind = document.getElementById('gt-indicators');
    const chart = document.getElementById('gt-chart');
    const tbl = document.getElementById('gt-table');
    const card = ind ? ind.closest('.card') : null;
    const blk = card ? card.closest('.sec-block') : null;
    if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
    const labels = Array.from(document.querySelectorAll('#tab-gtja .sec-index-list .sec-index-item'))
      .map(b => (b.textContent || '').replace('✓', '').trim());
    return {
      sameCard: !!card && !!chart && !!tbl && chart.closest('.card') === card && tbl.closest('.card') === card,
      h3n: card ? card.querySelectorAll('h3').length : -1,
      blocks: document.querySelectorAll('#tab-gtja > .sec-block').length,
      labels,
      rows: ind ? ind.querySelectorAll('tbody tr').length : 0,
    };
  });
  log(st.sameCard, '总览表/走势图/明细 同在①张卡内 (不再跨卡)');
  log(st.h3n === 1, `合并卡内 h3 数 = ${st.h3n} (期望 1, 防索引串名)`);
  log(st.blocks === 3, `国君页小模块数 = ${st.blocks} (期望 3, 原为 5)`);
  // ⚠️ 判据必须看「独立项」: 合并卡的标签「③ 指标总览 / 走势图 / 明细」本身含这两个词,
  //   用 /走势图|明细/ 去测整串会命中自己(自伤断言, 实测假红一次)。
  //   正确判据: ① 没有以 ④/⑤ 开头的独立项; ② 索引项数恰为 3。
  const noStandalone = !st.labels.some(s => /^[④⑤]\s*(走势图|明细数据)/.test(s));
  log(noStandalone && st.labels.length === 3,
      `无「④走势图」「⑤明细数据」独立项且共 3 项: [${st.labels.join(' | ')}]`);

  // 点第一行「查看」→ 就地出图出表
  await page.evaluate(() => {
    const b = document.querySelector('#gt-indicators tbody tr button');
    if (b) b.click();
  });
  await page.waitForTimeout(900);
  const a = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    const cv = c ? c.querySelector('canvas') : null;
    const tbl = document.getElementById('gt-table');
    const cb = c ? c.getBoundingClientRect() : null;
    const tb = tbl ? tbl.getBoundingClientRect() : null;
    const ib = document.getElementById('gt-indicators').getBoundingClientRect();
    return {
      cw: cv ? Math.round(cv.getBoundingClientRect().width) : 0,
      ch: cv ? Math.round(cv.getBoundingClientRect().height) : 0,
      badge: (document.getElementById('gt-chart-badge') || {}).textContent || '',
      tRows: tbl ? tbl.querySelectorAll('tbody tr').length : 0,
      tBadge: (document.getElementById('gt-tbl-badge') || {}).textContent || '',
      chartBelowTable: cb && cb.top > ib.top,
      gap: (cb && tb) ? Math.round(tb.top - cb.bottom) : -1,
    };
  });
  log(a.cw > 100 && a.ch > 100, `★走势图就地渲染且非 0×0 (${a.cw}×${a.ch}px)`);
  log(!!a.badge, `图表徽标带指标名: "${a.badge}"`);
  log(a.tRows > 0 && a.tBadge === `${a.tRows} 行`, `明细表就地出 ${a.tRows} 行 (徽标="${a.tBadge}")`);
  log(a.chartBelowTable, '走势图紧接总览表之下(视线连贯, 无需跳卡)');
  log(Math.abs(a.gap) < 200, `图表与明细相邻 (间隔 ${a.gap}px)`);
  log(!errs.length, '本段无 JS 报错');
}

console.log(`\nJS 报错: ${errs.length ? errs.slice(0, 3).join(' | ') : '(无)'}`);
console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
await browser.close();
process.exit(fail ? 1 : 0);
