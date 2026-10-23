import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 构造式 E2E：验证「发行方 / 方向 / 置信度」三列**逐机构纵向对齐**。
// 【为什么构造而不只用真实数据】真实落盘的 md 是旧 prompt 产物（观点短、可能只有 1~2 家），
//   覆盖不到「3 家机构」「数量不齐」这些**会静默错行**的边界；构造数据能把这些钉死。
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const ck = (ok, name, extra) => { if (ok) { pass++; console.log('  ✓ ' + name + (extra ? '  [' + extra + ']' : '')); } else { fail++; console.log('  ✗ ' + name + (extra ? '  [' + extra + ']' : '')); } };

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);

const built = await page.evaluate(() => {
  const LONG = '低硫燃料油供应端受配额与检修影响边际收紧，船燃需求随航运走强而回补，两家机构均认为下方支撑稳固；分歧在于外盘低硫基差走弱是否只是短期扰动，永安更看重供给弹性受限，国泰君安更看重配额增供节奏，因此前者建议逢回调多配，后者建议区间操作等待确认。';
  const rows = [
    // 3 家：正常多机构
    ['LU', '低硫燃料油', '永安期货 / 国泰君安 / 华泰', '看多 / 看空 / 中性', '58% / 63% / 82%', LONG, '永安建议轻仓多配；国泰建议观望', '6', '✓'],
    // 1 家
    ['FU', '燃料油', '华泰', '看空', '72%', '高硫裂解价差走弱、发电需求进入淡季，短期承压；关注中东发电采购是否超预期。', '逢高短空', '2', '✓'],
    // 2 家
    ['SC', '原油', '紫金天风 / 混沌天成', '中性 / 看多', '45% / 51%', '地缘扰动与库存去化并存，方向不明；分歧在供给中断的持续性。', '区间操作', '3', '✓'],
    // ★ 数量不齐：发行方 3 家，方向只有 2 个（脏数据）⇒ 必须补占位而不是错行
    ['TA', 'PTA', '华泰 / 国泰君安 / 永安期货', '看多 / 看多', '66% / 70%', '聚酯负荷回升带动需求，加工费压缩后检修增加；两家一致看多，分歧仅在节奏。', '多配', '4', '✓'],
    // ★ 超长观点（~500 字）：必须被 clamp + 给「展开全文」（120 字在 4 行内放得下，测不出边界）
    ['SR', '超长样本', '华泰 / 永安期货', '看多 / 中性', '66% / 55%',
      Array.from({ length: 14 }).fill('供应端受配额与检修影响边际收紧，船燃需求随航运走强而回补，两家均认为下方支撑稳固').join('，') + '。',
      '多配', '2', '✓'],
    // 短观点：不应包 clamp
    ['M', '豆粕', '中信期货', '中性', '55%', '到港增加。', '观望', '1', '✓'],
    // 无研报
    ['CJ', '红枣', '—', '—', '—', '—', '—', '0', '—'],
  ];
  const head = ['品种', '名称', '发行方', '方向', '置信度', '一句话核心观点', '头寸建议', '当日研报数', '当日有新研报'];
  const tbl = document.createElement('table');
  tbl.innerHTML = '<thead><tr>' + head.map(h => '<th>' + h + '</th>').join('') + '</tr></thead>'
    + '<tbody>' + rows.map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</tbody>';
  const host = document.createElement('div');
  host.className = 'report-content';
  host.id = 'vt-host';
  host.style.cssText = 'position:fixed;left:0;top:0;width:1500px;z-index:99999;background:var(--bg,#fff);';
  host.appendChild(tbl);
  document.body.appendChild(host);
  App._decorateViewTable(host);
  // 幂等复跑
  App._decorateViewTable(host);
  return { longLen: LONG.length, hostW: host.clientWidth };
});
console.log('  构造表宽 ' + built.hostW + 'px，长观点 ' + built.longLen + ' 字');

// ── 1. 结构 + 对齐 ────────────────────────────────────────────────────────────
const geo = await page.evaluate(() => {
  const host = document.getElementById('vt-host');
  const trs = [...host.querySelectorAll('tbody tr')];
  const out = [];
  trs.forEach((tr, ri) => {
    const td = [...tr.children];
    const src = [...td[2].querySelectorAll('.src-item')];
    const chips = [...td[3].querySelectorAll('.dir-chip')];
    const bars = [...td[4].querySelectorAll('.conf-bar')];
    const tops = (arr) => arr.map(e => Math.round(e.getBoundingClientRect().top * 10) / 10);
    const heights = (arr) => arr.map(e => Math.round(e.getBoundingClientRect().height * 10) / 10);
    const ghosts = (arr) => arr.map(e => getComputedStyle(e).visibility);
    out.push({
      ri, v: td[0].textContent.trim(),
      n: { src: src.length, chip: chips.length, bar: bars.length },
      tops: { src: tops(src), chip: tops(chips), bar: tops(bars) },
      heights: { src: heights(src), chip: heights(chips), bar: heights(bars) },
      ghost: { chip: ghosts(chips), bar: ghosts(bars) },
      tdVA: getComputedStyle(td[2]).verticalAlign + '/' + getComputedStyle(td[3]).verticalAlign + '/' + getComputedStyle(td[4]).verticalAlign,
      colW: { src: Math.round(td[2].getBoundingClientRect().width), dir: Math.round(td[3].getBoundingClientRect().width), conf: Math.round(td[4].getBoundingClientRect().width), op: Math.round(td[5].getBoundingClientRect().width) },
    });
  });
  return out;
});

ck(geo[0].n.src === 3 && geo[0].n.chip === 3 && geo[0].n.bar === 3, '3 家机构：三列项数都 = 3', JSON.stringify(geo[0].n));
ck(geo[1].n.src === 1 && geo[1].n.chip === 1 && geo[1].n.bar === 1, '1 家机构：三列项数都 = 1', JSON.stringify(geo[1].n));
ck(geo[3].n.src === 3 && geo[3].n.chip === 3 && geo[3].n.bar === 3, '★ 数据不齐（方向只有 2 个）：按发行方 3 家补齐到 3', JSON.stringify(geo[3].n));
ck(geo[3].ghost.chip[2] === 'hidden' && geo[3].ghost.bar[2] === 'hidden', '★ 补齐项是**不可见占位**（不是显示一个假方向）', geo[3].ghost.chip[2] + '/' + geo[3].ghost.bar[2]);

// 逐项对齐（三列第 k 项 top 必须一致；容差 1.5px 吸收亚像素）
for (const g of geo) {
  if (g.n.src === 0) continue;
  const bad = [];
  for (let k = 0; k < g.n.src; k++) {
    const a = g.tops.src[k], b = g.tops.chip[k], c = g.tops.bar[k];
    if (a === undefined || b === undefined || c === undefined || Math.max(Math.abs(a - b), Math.abs(a - c), Math.abs(b - c)) > 1.5) bad.push('k=' + k + ':' + a + '/' + b + '/' + c);
  }
  ck(bad.length === 0, g.v + '：三列第 k 项严格同行（' + g.n.src + ' 家）', bad.length ? bad.join(' ') : g.tops.src.join('|'));
}

// 每项高度必须一致（20px）—— 高度不齐会随家数累积错位
const hbad = [];
for (const g of geo) {
  for (const k of ['src', 'chip', 'bar']) for (const h of g.heights[k]) if (Math.abs(h - 20) > 1.5) hbad.push(g.v + '/' + k + '=' + h);
}
ck(hbad.length === 0, '每项固定高 20px（不随内容/家数变化）', hbad.slice(0, 4).join(' '));

ck(geo[0].tdVA === 'top/top/top', '三列 td 顶部对齐（middle 会让内容不等的格中心对齐 ⇒ 首项错开）', geo[0].tdVA);

// ── 2. 配色/进度条仍然正确 ────────────────────────────────────────────────────
const viz = await page.evaluate(() => {
  const host = document.getElementById('vt-host');
  const td = host.querySelectorAll('tbody tr')[0].children;
  const chips = [...td[3].querySelectorAll('.dir-chip')];
  const bars = [...td[4].querySelectorAll('.conf-bar')];
  const col = (e) => getComputedStyle(e).color.replace(/\s/g, '');
  const fills = bars.map(b => {
    const f = b.querySelector('.fill'); const tr = b.querySelector('.track');
    return { w: f ? Math.round(f.getBoundingClientRect().width) : null, tw: tr ? Math.round(tr.getBoundingClientRect().width) : null, bg: f ? getComputedStyle(f).backgroundColor.replace(/\s/g, '') : null };
  });
  return { chipCols: chips.map(col), fills, pct: bars.map(b => b.textContent.trim()) };
});
ck(viz.chipCols[0].includes('255,69,58'), '看多 = 红', viz.chipCols[0]);
ck(viz.chipCols[1].includes('48,209,88'), '看空 = 绿', viz.chipCols[1]);
ck(viz.chipCols[2].includes('255,159,10'), '中性 = 琥珀', viz.chipCols[2]);
const r72 = viz.fills[0] ? viz.fills[0].w / viz.fills[0].tw : 0;
ck(Math.abs(r72 - 0.58) < 0.08, '进度条长度=数值（58% → ' + Math.round(r72 * 100) + '%）', viz.pct.join(' '));

// ── 3. clamp 判据：长观点有按钮、短观点不加壳 ─────────────────────────────────
const clamp = await page.evaluate(() => {
  const host = document.getElementById('vt-host');
  const trs = [...host.querySelectorAll('tbody tr')];
  const info = (ri) => {
    const td = trs[ri].children[5];
    const box = td.querySelector('.clamp3');
    return {
      long: td.classList.contains('rv-long'),
      btn: !!td.querySelector('.clamp-toggle'),
      boxH: box ? Math.round(box.getBoundingClientRect().height) : null,
      scrollH: box ? box.scrollHeight : null,
      len: td.textContent.trim().length,
    };
  };
  return { lu: info(0), lst: info(4), m: info(5), cj: info(6) };
});
ck(clamp.lst.long && clamp.lst.btn, '超长观点：包 clamp 且有「展开全文」', 'len=' + clamp.lst.len);
ck(clamp.lst.boxH !== null && clamp.lst.scrollH > clamp.lst.boxH + 2, '★ 超长观点确实被裁（几何判据，不依赖 display 取值）', clamp.lst.boxH + '<' + clamp.lst.scrollH);
// ★ 120 字（目标口径上限）在 4 行内放得下 ⇒ **不应**加按钮（否则每格都长按钮）
ck(!clamp.lu.long && !clamp.lu.btn, '120 字观点在 4 行内放得下 ⇒ 不加壳（判据是"真被裁"而非字符数）', 'len=' + clamp.lu.len);
ck(!clamp.m.long && !clamp.m.btn, '短观点（4 字）：不加壳、不出按钮', 'len=' + clamp.m.len);
ck(!clamp.cj.long, '空值（—）：完全不处理', 'len=' + clamp.cj.len);

// 展开后全文可达
await page.evaluate(() => document.querySelector('#vt-host tbody tr:nth-child(5) .clamp-toggle').click());
await sleep(300);
const expanded = await page.evaluate(() => {
  const td = document.querySelector('#vt-host tbody tr:nth-child(5)').children[5];
  const box = td.querySelector('.clamp3');
  return { open: td.classList.contains('open'), boxH: Math.round(box.getBoundingClientRect().height), scrollH: box.scrollHeight, txt: td.textContent.trim().length };
});
ck(expanded.open && Math.abs(expanded.boxH - expanded.scrollH) <= 2, '展开后全文完整显示', expanded.boxH + '/' + expanded.scrollH);

// ── 4. 列宽（给"观点能放多少字"提供依据）────────────────────────────────────
console.log('  列宽: 发行方 ' + geo[0].colW.src + ' | 方向 ' + geo[0].colW.dir + ' | 置信度 ' + geo[0].colW.conf + ' | 观点 ' + geo[0].colW.op + 'px');
const perLine = await page.evaluate(() => {
  const td = document.querySelector('#vt-host tbody tr:nth-child(5)').children[5];
  const box = td.querySelector('.clamp3');
  const cs = getComputedStyle(box);
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font:' + cs.font;
  probe.textContent = '低硫燃料油供应端受配额与检修影响边际收紧';
  document.body.appendChild(probe);
  const w = probe.getBoundingClientRect().width / probe.textContent.length;
  probe.remove();
  return { charW: Math.round(w * 10) / 10, boxW: Math.round(box.clientWidth), perLine: Math.floor(box.clientWidth / w), lineH: Math.round(parseFloat(cs.lineHeight)) };
});
console.log('  观点列：每字 ' + perLine.charW + 'px，可用宽 ' + perLine.boxW + 'px ⇒ 每行约 ' + perLine.perLine + ' 字，4 行约 ' + perLine.perLine * 4 + ' 字');

await page.screenshot({ path: 'probe/_shot_vstack_built.png', clip: { x: 0, y: 0, width: 1500, height: 470 } });
console.log('\n  JS 错误: ' + (errs.length ? errs.join(' | ') : '无'));
console.log('  结果: ' + pass + ' PASS / ' + fail + ' FAIL');
await B.close();
process.exit(fail ? 1 : 0);
