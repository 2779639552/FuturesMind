import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

// 第二节「观点对比与冲突」E2E：列宽重排 + 每家机构独占一行
// 【为什么用真实页面而不是构造】这段数据来自后端落盘的 md，`<br>` 的转义形态是
//   marked 在 GFM 表格单元格里的真实行为 —— 构造内容无法复现，必须走真链路。
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

let pass = 0, fail = 0;
const ck = (ok, name, extra) => { if (ok) { pass++; console.log('  ✓ ' + name + (extra ? '  [' + extra + ']' : '')); } else { fail++; console.log('  ✗ ' + name + (extra ? '  [' + extra + ']' : '')); } };

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);
await page.evaluate(async () => {
  const t = document.getElementById('res-daily-type');
  if (t) { t.value = '周报'; t.dispatchEvent(new Event('change')); }
  await new Promise(r => setTimeout(r, 2600));
  const d = document.getElementById('res-daily-date');
  if (d) { const o = [...d.options].find(x => x.value === '2026-10-11'); if (o) { d.value = '2026-10-11'; d.dispatchEvent(new Event('change')); } }
  await new Promise(r => setTimeout(r, 2600));
});
await sleep(2500);

const m = await page.evaluate(() => {
  const tbl = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('分歧状态') && ths.includes('各家观点与逻辑');
  });
  if (!tbl) return null;
  const ths = [...tbl.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
  const w = [...tbl.querySelectorAll('thead th')].map(h => Math.round(h.getBoundingClientRect().width));
  const rows = [...tbl.querySelectorAll('tbody tr')];
  const iLogic = ths.indexOf('各家观点与逻辑');
  const iState = ths.indexOf('分歧状态');

  // 逐行：cf-row 数量、是否还残留字面 <br>、每个 cf-row 的 top（是否真换行）
  const per = rows.map(tr => {
    const td = tr.children[iLogic];
    const tdState = tr.children[iState];
    const rowsDiv = [...td.querySelectorAll('.cf-row')];
    const tops = rowsDiv.map(d => Math.round(d.getBoundingClientRect().top));
    const uniqTops = [...new Set(tops)];
    return {
      v: tr.children[0].textContent.trim(),
      state: tdState.textContent.trim(),
      stateW: Math.round(tdState.getBoundingClientRect().width),
      // ⚠ 不能用 td 高度/行高算行数：同一行里「观点与逻辑」有很多段 ⇒ 整行很高，
      //   td 高度会被撑大（实测报出"6 行/8 行"这种假象）。改为量**文本自身宽度**。
      stateTextW: (() => {
        const r = document.createRange();
        r.selectNodeContents(tdState);
        return Math.round(r.getBoundingClientRect().width);
      })(),
      stateAvail: Math.round(tdState.clientWidth - parseFloat(getComputedStyle(tdState).paddingLeft) - parseFloat(getComputedStyle(tdState).paddingRight)),
      nRows: rowsDiv.length,
      // 关键：每个 cf-row 的 top 必须互不相同（真的各占一行，不是挤在同一行）
      distinctTops: uniqTops.length,
      // 是否还有被转义残留的字面 <br>
      literalBr: (td.textContent.match(/<br\s*\/?>/gi) || []).length,
      // 机构名出现次数（判断是否每家一段）
      fams: (td.textContent.match(/(紫金天风|东证|国泰君安|华泰|永安|中信|混沌天成|物产中大)/g) || []).length,
    };
  });

  const rect = tbl.getBoundingClientRect();
  return {
    ths, w, tblW: Math.round(rect.width), rows: rows.length, per,
    clip: { x: Math.max(0, rect.x - 10), y: Math.max(0, Math.min(rect.y - 50, window.innerHeight - 620)), width: Math.min(rect.width + 20, window.innerWidth - 8), height: Math.min(rect.height + 20, 640) },
  };
});
if (!m) { console.log('  ★ 未找到第二节表格'); await B.close(); process.exit(1); }

console.log('  表宽 ' + m.tblW + 'px / ' + m.rows + ' 行');
console.log('  列宽: ' + m.ths.map((h, k) => h + '=' + m.w[k]).join(' | '));
console.log();

// ── 1. 列宽：分歧状态收窄、观点与逻辑扩容 ─────────────────────────────────────
const iS = m.ths.indexOf('分歧状态'), iL = m.ths.indexOf('各家观点与逻辑');
ck(m.w[iS] < 200, '分歧状态列已收窄（原 439px）', m.w[iS] + 'px');
// ★★ 关键判据：实测宽度必须**接近声明的 76px**。
//   只断言 "< 200" 是漏的 —— 上一轮设 76px 时被等比放大成 141px，141 < 200 照样通过，
//   用户看到的"留白过大"就漏出去了。**CSS 写了多少 ≠ 页面渲染多少**。
ck(Math.abs(m.w[iS] - 76) <= 8, '★★ 分歧状态实测宽度 ≈ 声明的 76px（未被等比放大）',
  m.w[iS] + 'px vs 声明 76px');
ck(m.w[iL] > 600, '观点与逻辑列已扩容（原 402px）', m.w[iL] + 'px');
ck(m.w[iL] > m.w[iS] * 3, '★ 观点列明显宽于分歧状态列（份额已倒过来）',
  (m.w[iL] / m.w[iS]).toFixed(1) + '×');
// 分歧状态的值都是 4~5 字，必须一行放得下（不能被挤成两行）
const badWrap = m.per.filter(r => r.stateTextW > r.stateAvail + 1);
ck(badWrap.length === 0, '分歧状态没被收窄到折行（文本宽度仍放得下）',
  badWrap.slice(0, 3).map(r => r.v + ':' + r.state + '(' + r.stateTextW + '>' + r.stateAvail + ')').join(' ')
  || ('全部单行, 最宽 ' + Math.max(...m.per.map(r => r.stateTextW)) + 'px < 可用 ' + m.per[0].stateAvail + 'px'));

// ── 2. 每家机构独占一行 ─────────────────────────────────────────────────────
// ★ 单家行（分歧状态「单一观点」）本来就**不该拆** —— 拆成一层无意义包裹反而是 bug。
const multi = m.per.filter(r => r.fams >= 2);
const single = m.per.filter(r => r.fams <= 1);
const noRows = multi.filter(r => r.nRows < 2);
ck(noRows.length === 0, '多机构行都拆成了多个 cf-row（每家一段）',
  noRows.length ? noRows.map(r => r.v + ':' + r.nRows).join(' ') : (multi.length + ' 个多机构行, 最少 ' + Math.min(...multi.map(r => r.nRows)) + ' 段'));
ck(single.every(r => r.nRows === 0), '单机构行不拆（不产生无意义的单层包裹）',
  single.map(r => r.v + ':' + r.state + '→' + r.nRows + ' 段').join(' ') || '无单家行');

// ── 2b. ★★ 构造样本：单家机构**内部用 <br> 分段**（用户反馈的正是这种）──────────
// 【为什么必须构造】真实数据里当前没有这种行，但 LLM 随时会写成
//   「国泰君安 中性(55%)—理由A<br>理由B<br>风险C」。这种行若被当"多家"拆开，
//   每段之间还会画虚线 ⇒ 视觉上**把一家伪装成三家**（用户：「把国君的名称拆成了三行」）。
const built = await page.evaluate(() => {
  const mk = (cellHtml, id) => {
    const host = document.createElement('div');
    host.className = 'report-content';
    host.id = id;
    host.style.cssText = 'position:fixed;left:0;top:0;width:1200px;z-index:99999;background:var(--bg,#fff)';
    host.innerHTML = '<table><thead><tr><th>品种</th><th>分歧状态</th><th>各家观点与逻辑</th><th>分歧焦点</th><th>分歧根源</th></tr></thead>'
      + '<tbody><tr><td>PX</td><td>单一观点</td><td>' + cellHtml + '</td><td>—</td><td>—</td></tr></tbody></table>';
    document.body.appendChild(host);
    App._decorateConflictTable(host);
    const td = host.querySelector('tbody td:nth-child(3)');
    const rows = [...td.querySelectorAll('.cf-row')];
    const res = {
      nRows: rows.length,
      hasCellClass: td.classList.contains('cf-cell'),
      // 段间分隔线：相邻 .cf-row 必须有 border-top；单家场景应当**没有**
      sepCount: rows.filter(d => parseFloat(getComputedStyle(d).borderTopWidth) > 0).length,
      text: (td.textContent || '').trim(),
      // 真实换行数（还原后应为 2，且不含字面 <br>）
      brEls: td.querySelectorAll('br').length,
      literalBr: (td.textContent.match(/<br\s*\/?>/gi) || []).length,
    };
    host.remove();
    return res;
  };
  // 单家 + 内部 2 个 <br>（3 段）
  const single = mk('国泰君安 中性(55%)—装置停车与月差走强支撑近月<br>但聚酯负荷下调压制上方空间<br>风险：新装置提前投产', 'cf-test-single');
  // 对照：真多家（2 家）
  const multi = mk('华泰 看多(72%)—供应收缩<br>国泰君安 中性(55%)—库存高位', 'cf-test-multi');
  // 对照：带序号前缀的真多家（LLM 有时写「① 华泰 看多」）—— 前缀容错必须仍能识别
  const numbered = mk('① 华泰 看多(72%)—供应收缩<br>② 国泰君安 中性(55%)—库存高位', 'cf-test-numbered');
  return { single, multi, numbered };
});
ck(built.single.nRows === 0, '★★ 单家内部多段：不拆成 cf-row（不把一家伪装成三家）',
  '段=' + built.single.nRows + ' / 类=' + built.single.hasCellClass);
ck(built.single.sepCount === 0, '★★ 单家内部多段：段间**没有**虚线分隔（否则像是三家）', '分隔线 ' + built.single.sepCount + ' 条');
ck(built.single.brEls === 2, '单家内部多段：仍还原成 2 个真实换行（保留作者分段）', built.single.brEls + ' 个 <br>');
ck(built.single.literalBr === 0, '单家内部多段：没有残留字面 <br>', built.single.literalBr + ' 处');
ck(built.multi.nRows === 2, '对照：真多家仍正常拆成 2 段', built.multi.nRows + ' 段');
ck(built.multi.sepCount === 1, '对照：真多家第 2 段有 1 条分隔线', built.multi.sepCount + ' 条');
ck(built.numbered.nRows === 2, '对照：带序号前缀（① ②）的真多家仍能识别并拆开', built.numbered.nRows + ' 段');

// ── 2c. ★★ 用户原文：两家之间**漏写 <br>**（第三家粘在第二家尾部）──────────────
// 【实测原文】「…下游采购下滑、己内酰胺累库华泰 看多(68%)—库存未回升…」
//   第 1、2 家之间有 <br>，第 2、3 家粘连 ⇒ 旧兜底只在 heads<2 时跑，永远修不到。
const glued = await page.evaluate(() => {
  const host = document.createElement('div');
  host.className = 'report-content';
  host.style.cssText = 'position:fixed;left:0;top:0;width:1200px;z-index:99999;background:var(--bg,#fff)';
  host.innerHTML = '<table><thead><tr><th>品种</th><th>分歧状态</th><th>各家观点与逻辑</th><th>分歧焦点</th><th>分歧根源</th></tr></thead>'
    + '<tbody><tr><td>XX</td><td>多空对立</td><td>'
    + '东证 看多(58%)—市场预期由震荡转偏多且偏多占优，但缺供需验证<br>'
    + '国泰君安 看空(77%)—进口增量、国产开工恢复与检修推迟使供应转松，下游采购下滑、己内酰胺累库'
    + '华泰 看多(68%)—库存未回升、进口到港低，供给偏紧支撑，下游开工回落拖累，'
    + '</td><td>—</td><td>—</td></tr></tbody></table>';
  document.body.appendChild(host);
  App._decorateConflictTable(host);
  const td = host.querySelector('tbody td:nth-child(3)');
  const rows = [...td.querySelectorAll('.cf-row')].map(d => d.textContent.trim());
  const out = { nRows: rows.length, rows, stray: rows.some(r => /^[泰君安紫金天混沌物产中]{1,3}\s/.test(r)) };
  host.remove();
  return out;
});
ck(glued.nRows === 3, '★★ 粘连（漏写 <br>）：仍切成 3 段，每家单独一行', glued.nRows + ' 段');
ck(glued.rows[2] && glued.rows[2].startsWith('华泰'), '★★ 第三家（华泰）从第二家尾部独立出来',
  (glued.rows[2] || '').slice(0, 24));
ck(!glued.stray, '机构名没有被切碎（段首不是"泰/君/安"等残片）',
  glued.rows.map(r => r.slice(0, 4)).join(' | '));

const sameLine = m.per.filter(r => r.nRows >= 2 && r.distinctTops < r.nRows);
ck(sameLine.length === 0, '★★ cf-row 之间 top 各不相同（真换行，不是同一行内并排）',
  sameLine.length ? sameLine.map(r => r.v + ':' + r.distinctTops + '/' + r.nRows).join(' ') : '全部各占一行');

const literal = m.per.filter(r => r.literalBr > 0);
ck(literal.length === 0, '★ 单元格内已无残留的字面 <br> 文本',
  literal.length ? literal.map(r => r.v + ':' + r.literalBr).join(' ') : '0 处');

// 拆出的段数应与机构名出现次数一致（每家一段，不多不少）
const mism = multi.filter(r => r.nRows !== r.fams);
ck(mism.length === 0, '段数 == 机构家数（没有多拆/漏拆）',
  mism.slice(0, 3).map(r => r.v + ':段' + r.nRows + '≠家' + r.fams).join(' ') || ('如 ' + m.per[0].v + ' 段' + m.per[0].nRows + '=家' + m.per[0].fams));

// ── 3. 幂等：重复调用不叠加 ────────────────────────────────────────────────
const idem = await page.evaluate(() => {
  const tbl = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('分歧状态') && ths.includes('各家观点与逻辑');
  });
  const before = tbl.querySelectorAll('.cf-row').length;
  const host = document.querySelector('.report-content');
  App._decorateConflictTable(host);
  App._decorateConflictTable(host);
  return { before, after: tbl.querySelectorAll('.cf-row').length };
});
ck(idem.before === idem.after, '重复调用不叠加（幂等）', idem.before + ' → ' + idem.after);

// ── 4. 不误伤总表 ─────────────────────────────────────────────────────────
const other = await page.evaluate(() => {
  const all = [...document.querySelectorAll('.report-content table')];
  const views = all.filter(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('方向') && ths.includes('置信度');
  });
  return views.map(t => ({ conflictClass: t.classList.contains('rv-conflict-tbl'), cfRow: t.querySelectorAll('.cf-row').length }));
});
ck(other.length > 0 && other.every(o => !o.conflictClass && o.cfRow === 0),
  '总表未被误伤（没有被打上第二节的 class/拆分）',
  other.length + ' 张总表');

ck(errs.length === 0, '无 JS 错误', errs.join(' | ') || '0');
await page.screenshot({ path: 'probe/_shot_conflict_after.png', clip: m.clip });
console.log('\n  结果: ' + pass + ' PASS / ' + fail + ' FAIL');
await B.close();
process.exit(fail ? 1 : 0);
