/**
 * 【护栏】卡片内浮层不被折叠外壳裁剪 (2026-09-29)
 *
 * 背景: 折叠引擎给每个小模块外壳 `.sec-block` 加了 `overflow:hidden`(收起态必需),
 *   但它会**裁掉卡片内所有绝对定位的浮层**(下落包含块在卡片内部)。
 *   实测数据仓库页 `#res-local-import-panel` 高 251px 只露出 31px(被裁 220px) ——
 *   用户报的"研报本地导入的下拉栏被遮挡"。
 *   修法: 展开态给 .sec-block 加 `.sec-open{overflow:visible}` + 点击浮层时
 *   `_ensurePopoverVisible()` 兜底放开整条祖先链。
 *
 * ⚠️ 本护栏必须走**真实交互**(点索引项展开 → 调 App.toggleXxxPanel()),
 *   不能直接改 style.display —— 那样会绕过 .sec-open 与 _ensurePopoverVisible,
 *   测出来的是"修法没生效"的假红(本轮踩过)。
 *
 * 判据(比 getBoundingClientRect 更硬):
 *   ① 浮层矩形不得超出任一裁剪祖先的矩形(overflow 判定)
 *   ② elementFromPoint 取浮层底部中心点, 必须命中浮层**自身或其后代**
 *      —— 被裁/被盖时命中的是别的元素
 *
 * 用法:
 *   NO_PROXY=127.0.0.1,localhost node tests/js/popover_clip_harness.mjs
 *   SHOT_DIR=<dir>  # 可选, 顺带截图取证
 */
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const SHOT_DIR = process.env.SHOT_DIR || '';

// 全站绝对定位浮层清单: [tab, 触发展开的索引标题关键字, 浮层 id, 打开函数]
// 未登录时 tab 内容仍会渲染(折叠是纯 DOM); 401 只影响数据填充, 不影响几何测量。
//
// ⚠️【为什么必须列全】折叠引擎对**每个 tab** 都生效(`switchTab` 无条件调
//   `_buildSectionIndex`), 所以任何一个落在卡片内的 `position:absolute` 浮层
//   都有被 `.sec-block{overflow:hidden}` 裁掉的风险。漏登记 = 漏测。
//   取证口径: `grep -nE 'position:absolute' web_template.html` → 全站真浮层仅 3 个
//   (另 2 个 `position:fixed` 是登录遮罩, 不受祖先裁剪影响)。
//
// ⚠️【unloggedEmpty】未登录时面板是空的(内容靠 loadXxx 填), 高度 0 ⇒ 几何断言会
//   "平凡通过", 给出**假绿**。凡内容驱动的浮层必须 `fill` 造出真实高度再测
//   —— 真实用户看到裁剪问题, 恰恰是在面板有内容的时候。
const CASES = [
  { tab: 'warehouse', blockLabel: '上传研报', panelId: 'res-local-import-panel', opener: 'toggleLocalImportPanel' },
  { tab: 'warehouse', blockLabel: '上传研报', panelId: 'res-collect-variety-panel', opener: 'toggleCollectVarietyPanel' },
  {
    tab: 'research', blockLabel: '单品种总结', panelId: 'res-vsum-vpanel',
    opener: 'toggleVsumVarietyPanel', unloggedEmpty: true,
    // 模拟"已填充"的品种多选面板: 20 行 ≈ 真实高度
    fill: () => {
      const p = document.getElementById('res-vsum-vpanel');
      if (!p) return;
      p.innerHTML = Array.from({ length: 20 }, (_, i) =>
        `<label style="display:block;padding:3px 0;white-space:nowrap">`
        + `<input type="checkbox" checked> 品种${i} <span style="color:var(--text-muted)">—</span></label>`
      ).join('');
    },
  },
];

const browser = await chromium.launch({
  executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
const logs = [];
page.on('console', m => { if (m.type() === 'error') logs.push(m.text()); });
page.on('pageerror', e => logs.push('pageerror: ' + e.message));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 隐藏未登录遮罩: 用 <style> !important 而不是改内联 style ——
// 遮罩的内联样式会被 App 的逻辑重写回来, 导致 elementFromPoint 永远命中遮罩(本轮踩过)。
await page.addStyleTag({ content: '#login-overlay{display:none !important;}#login-card{display:none !important;}' });
await page.evaluate(() => {
  document.querySelectorAll('#login-overlay,#login-card').forEach(e => e.remove());
});

let pass = 0, fail = 0;
const bad = [];
const chk = (n, c, extra = '') => {
  if (c) { pass++; console.log(`  ✓ ${n}`); }
  else { fail++; bad.push(n); console.log(`  ✗ ${n} ${extra}`); }
};

for (const c of CASES) {
  console.log(`\n=== ${c.tab} / ${c.blockLabel} → #${c.panelId} ===`);
  await page.evaluate(t => App.switchTab(t), c.tab);
  await page.waitForTimeout(1400);

  // ⓪ 内容驱动的浮层: 未登录时为空(高度 0) ⇒ 先造出真实内容, 否则几何断言平凡通过(假绿)
  if (c.fill) {
    await page.evaluate(c.fill);
    await page.waitForTimeout(150);
  }

  const step1 = await page.evaluate(({ label, panelId }) => {
    const tab = document.querySelector('.tab.active') || document.getElementById('tab-warehouse');
    const items = Array.from(tab.querySelectorAll('.sec-index-item'));
    const it = items.find(x => (x.textContent || '').includes(label));
    if (!it) return { err: '找不到索引项: ' + label };
    if (!it.classList.contains('is-open')) it.click();   // 真实 click → _toggleSection
    return { clicked: true, panelExists: !!document.getElementById(panelId) };
  }, { label: c.blockLabel, panelId: c.panelId });
  if (step1.err) { chk('找到索引项', false, step1.err); continue; }
  chk('找到索引项并展开', true);
  // ⚠️ 必须等展开过渡跑完(320ms)—— .sec-open 是在过渡**结束后**才加上的
  //   (展开动画期间保持 overflow:hidden 才能不让内容瞬间溢出), 提前测量会假红。
  await page.waitForTimeout(600);
  const step1b = await page.evaluate(({ label }) => {
    const tab = document.querySelector('.tab.active');
    const it = Array.from(tab.querySelectorAll('.sec-index-item')).find(x => (x.textContent || '').includes(label));
    let blk = it ? it.closest('.sec-index') : null;
    // 从索引项反查它控制的块: 用 _secRows 拿 blocks 更准
    const tabId = tab.id.replace(/^tab-/, '');
    const rows = (App._secRows || {})[tabId] || [];
    const row = rows.find(r => (r.label || '').includes(label));
    const b = row && row.blocks && row.blocks[0];
    return { secOpen: !!(b && b.classList.contains('sec-open')),
             collapsed: !!(b && b.classList.contains('is-collapsed')),
             overflow: b ? getComputedStyle(b).overflow : null };
  }, { label: c.blockLabel });
  chk('展开过渡结束后外壳放开裁剪 (overflow=visible)', step1b.overflow === 'visible', `得 ${step1b.overflow}`);
  chk('展开后不带 is-collapsed', !step1b.collapsed);

  // ② 走真实路径打开浮层
  const step2 = await page.evaluate(({ opener, panelId }) => {
    if (typeof App[opener] !== 'function') return { err: '缺少方法 ' + opener };
    App[opener]();      // 真实调用(内部会 _ensurePopoverVisible)
    const p = document.getElementById(panelId);
    return { display: getComputedStyle(p).display };
  }, { opener: c.opener, panelId: c.panelId });
  if (step2.err) { chk('调用打开函数', false, step2.err); continue; }
  chk('浮层已打开', step2.display === 'block' || step2.display === '', `display=${step2.display}`);
  await page.waitForTimeout(300);

  // ③ 测量: 是否被任何裁剪祖先截断
  const m = await page.evaluate((panelId) => {
    const p = document.getElementById(panelId);
    const r = p.getBoundingClientRect();
    // 收集整条链上的裁剪祖先
    const clippers = [];
    let n = p.parentElement;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflow !== 'visible' || cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        clippers.push({
          el: n, tag: n.tagName, cls: String(n.className || ''),
          ov: cs.overflow, rect: n.getBoundingClientRect(),
        });
      }
      n = n.parentElement;
    }
    // 只算"真会裁"的祖先(body/html 已排除)
    const clippedBy = clippers.filter(c => {
      if (c.tag === 'BODY' || c.tag === 'HTML') return false;
      const cr = c.rect;
      return r.bottom > cr.bottom + 1 || r.right > cr.right + 1 || r.top < cr.top - 1 || r.left < cr.left - 1;
    }).map(c => ({ cls: c.cls.split(' ')[0] || c.tag, ov: c.ov, over: Math.round(r.bottom - c.rect.bottom) }));

    // 可见性: 取浮层内部几个点, 命中自己才算真可见
    const pts = [
      ['top+10', r.left + r.width / 2, r.top + 10],
      ['mid', r.left + r.width / 2, r.top + r.height / 2],
      ['bottom-6', r.left + r.width / 2, r.bottom - 6],
    ];
    const hits = pts.map(([name, x, y]) => {
      const hit = document.elementFromPoint(x, y);
      return { name, ok: !!(hit && (hit === p || p.contains(hit))),
               hit: hit ? (hit.id || String(hit.className).split(' ')[0] || hit.tagName) : 'null' };
    });
    return {
      rect: { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) },
      clippedBy, hits,
      blocker: clippers.length,
    };
  }, c.panelId);

  console.log(`    浮层 ${m.rect.top}..${m.rect.bottom} (h=${m.rect.h}); 链上裁剪祖先 ${m.blocker} 个`);
  m.hits.forEach(h => console.log(`      命中点 ${h.name}: ${h.ok ? '✓自身' : '✗ ' + h.hit}`));
  chk('无裁剪祖先截断浮层', m.clippedBy.length === 0,
      `被 ${JSON.stringify(m.clippedBy)}`);
  chk('底部 6px 处命中浮层自身', m.hits[2].ok, `命中 ${m.hits[2].hit}`);
  chk('中部命中浮层自身', m.hits[1].ok, `命中 ${m.hits[1].hit}`);

  if (SHOT_DIR) {
    await page.screenshot({ path: `${SHOT_DIR}/popover_${c.panelId}.png` });
  }

  // ④ 反向: 收起后浮层必须跟着不可见(证明 overflow 收回正常)
  const closed = await page.evaluate(({ opener, panelId, label }) => {
    const tab = document.querySelector('.tab.active');
    const it = Array.from(tab.querySelectorAll('.sec-index-item')).find(x => (x.textContent || '').includes(label));
    if (it && it.classList.contains('is-open')) it.click();   // 收起该模块
    const p = document.getElementById(panelId);
    const cs = getComputedStyle(p);
    // 找到它所在的 sec-block
    let blk = p.closest('.sec-block');
    return {
      blockCollapsed: !!(blk && blk.classList.contains('is-collapsed')),
      blockOverflow: blk ? getComputedStyle(blk).overflow : null,
      hasSecOpen: !!(blk && blk.classList.contains('sec-open')),
      panelDisplay: cs.display,
    };
  }, { opener: c.opener, panelId: c.panelId, label: c.blockLabel });
  chk('收起后外壳恢复 overflow:hidden', closed.blockOverflow === 'hidden', `得 ${closed.blockOverflow}`);
  chk('收起后移除 .sec-open', !closed.hasSecOpen);
}

console.log(`\n${'='.repeat(52)}\n${fail === 0 ? 'ALL_PASS' : 'FAILED'}(${pass})  fail=${fail}`);
if (bad.length) console.log('未通过: ' + bad.join(' / '));
const real = logs.filter(l => !/401|Unauthorized|Failed to load resource/i.test(l));
console.log(real.length ? '⚠️ JS 报错: ' + real.join(' | ') : '无 JS 报错(401 已过滤)');

await browser.close();
process.exit(fail === 0 ? 0 : 1);
