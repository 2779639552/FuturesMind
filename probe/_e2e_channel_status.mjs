// 【用途】「采集通道状态提示」的浏览器 E2E 验收（2026-10-16 新增）。
// 【为什么必须真浏览器】静态断言只能证"代码里写了"，证不了：
//   ① 接口在真实服务里通不通（改了后端必须重启，pytest 测不到"服务跑的是哪份代码"）；
//   ② 提示条会不会真的渲染出来（接线漏了、容器被折叠引擎藏了、JS 报错都会静默不显示）；
//   ③ 用户能不能**看见修复动作**（这是本次改动的唯一目的）。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 } })).newPage();

let pass = 0, fail = 0;
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
function ck(ok, msg, extra) {
  if (ok) { pass++; console.log('  ✔ ' + msg + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★ FAIL ' + msg + (extra ? '  [' + extra + ']' : '')); }
}
const sleep = ms => page.waitForTimeout(ms);

// ① 先加载首页（拿到同源上下文），再直测接口 ——
//    ⚠ 不能在 about:blank 上 fetch 相对路径：没有 base URL 会直接抛
//      "Failed to parse URL from /api/..."（第一版就这么写的）。
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
const apiResp = await page.evaluate(async () => {
  const r = await fetch('/api/wechat/channels');
  return { status: r.status, body: await r.json().catch(() => null) };
});
console.log('\n=== A. 接口直测（真实服务进程）===');
ck(apiResp.status === 200, 'GET /api/wechat/channels 返回 200', 'HTTP ' + apiResp.status);
const api = apiResp.body || {};
ck(api.ok === true, '响应 ok=true');
ck(Array.isArray(api.channels) && api.channels.length === 2, '两条通道', String((api.channels || []).length));
for (const c of (api.channels || [])) {
  console.log('     [%s] %s state=%s accounts=%s %s', c.key, c.name, c.state, c.accounts, c.unit_name);
}

// ② 进「数据仓库」页，看提示条真渲染（首页已在上一步加载过）
await page.waitForSelector('nav button[data-tab="warehouse"]', { timeout: 30000 });
await page.click('nav button[data-tab="warehouse"]');

let seen = null;
for (let i = 0; i < 60; i++) {
  seen = await page.evaluate(() => {
    const el = document.getElementById('res-ingest-status');
    const list = document.getElementById('res-list');
    // 滚到研报库卡片，确保它真在可见区（防止"渲染了但被藏起来"被误判为通过）
    if (el && el.offsetParent === null) el.scrollIntoView({ block: 'center' });
    return {
      exists: !!el,
      display: el ? el.style.display : null,
      visible: !!(el && el.offsetParent !== null),
      text: el ? (el.innerText || '').trim().replace(/\s+/g, ' ') : '',
      html: el ? el.innerHTML : '',
      listLoaded: !!(list && (list.innerText || '').trim() && !(list.innerText || '').includes('加载中')),
    };
  });
  if (seen && seen.text) break;
  await sleep(500);
}

console.log('\n=== B. 提示条真的渲染出来了 ===');
ck(seen && seen.exists, '#res-ingest-status 容器存在');
ck(seen && seen.display !== 'none', '容器未被隐藏（display != none）', seen ? seen.display : '-');
ck(seen && seen.text.length > 0, '提示条有内容', seen ? seen.text.slice(0, 120) : '-');

console.log('\n=== C. ★ 按接口真实状态自适应（本次改动的唯一目的）===');
const txt = seen ? seen.text : '';
// 【★ 为什么要自适应】本段原来的前提是"有通道处于中断"—— 那是 2026-10-16 上午的实况。
//   链路修好后状态自然变 ok/empty ⇒ 写死"必须出现「中断」与修复命令"就会**每次都假红**
//   （产品是对的，判据过期了。本项目已多次踩"前提过期"型假红）。
//   正解：按接口真实状态分两条路，两条路都验"界面与状态一致"。
const apiCh = (api && api.channels) || [];
const failedCh = apiCh.filter(c => c.state === 'failed');
console.log('   接口状态: ' + apiCh.map(c => c.key + '=' + c.state).join(' / ')
  + '   failed=' + failedCh.length);
ck(apiCh.length >= 2, '接口返回两条通道', String(apiCh.length));
ck(txt.includes('合集'), '合集通道也在提示范围内（只说一半会让人以为另一条也坏了）');

if (failedCh.length) {
  // ── 有中断：必须点名 + 说原因 + 给可执行修复命令
  const nm = failedCh[0].name || '';
  ck(txt.includes('微信读书') || (nm && txt.includes(nm.replace('公众号 · ', ''))),
    '点名了中断的通道', nm);
  ck(txt.includes('中断'), '明确写出「中断」（不是含糊的"没数据"）');
  ck(!!failedCh[0].error, '接口给出了失败原因', String(failedCh[0].error).slice(0, 60));
  ck(txt.includes('_weread_login.mjs'), '★ 给出可执行的**修复命令**（用户知道怎么办）');
} else {
  // ── 无中断：界面**必须不**报中断、**不**给修复命令（否则让人去修没坏的东西）
  ck(!txt.includes('中断'), '★ 无通道中断时，界面不应出现「中断」字样',
    txt.includes('中断') ? txt.slice(0, 90) : 'ok ✓');
  ck(!txt.includes('_weread_login.mjs'), '★ 无中断时不应显示修复命令（否则误导用户去重登）');
  ck(apiCh.every(c => c.state === 'ok' || c.state === 'empty'),
    '两条通道状态均为 ok / empty', apiCh.map(c => c.key + '=' + c.state).join('/'));
  // ★ 本轮修复的核心：陈旧失败告警必须被更晚的成功记录推翻
  const wr = apiCh.find(c => c.key === 'weread');
  if (wr && wr.new > 0) {
    ck(wr.state === 'ok',
      '★ 本轮有新文(new=' + wr.new + ') ⇒ 状态应为 ok 而非 failed（陈旧告警已被推翻）',
      'state=' + wr.state);
  }
}

console.log('\n=== D. 口径解释：品种池边界 ≠ 通道故障 ===');
ck(txt.includes('品种池') || txt.includes('未入库'),
  '解释了「采到却未入库」是口径边界', txt.match(/\d+ 篇公众号文章[^。]{0,40}/) ? txt.match(/\d+ 篇公众号文章[^。]{0,40}/)[0] : '-');

console.log('\n=== E. 不误伤研报库本体 ===');
ck(seen && seen.listLoaded, '研报库列表照常加载（巡检没影响主体）');

console.log('\n=== F. 无 JS 错误 ===');
const realErrs = errs.filter(e => !/favicon|ERR_CONNECTION|net::/i.test(e));
ck(realErrs.length === 0, '无 JS 运行时错误', realErrs.slice(0, 2).join(' | ') || '0 条');

console.log('\n' + '='.repeat(58));
console.log('  PASS ' + pass + ' / FAIL ' + fail);
console.log('='.repeat(58));

// 拍一张：滚到研报库卡片（提示条在图里）
await page.evaluate(() => {
  const el = document.getElementById('res-ingest-status');
  if (el) el.scrollIntoView({ block: 'start' });
});
await sleep(800);
const clip = await page.evaluate(() => {
  const el = document.getElementById('res-ingest-status');
  const card = el ? el.closest('.card') : null;
  const b = (card || el).getBoundingClientRect();
  return { x: Math.max(0, b.x - 8), y: Math.max(0, b.y - 8), width: Math.min(b.width + 16, 1700), height: Math.min(b.height + 16, 900) };
});
await page.screenshot({ path: 'probe/_shot_channel_status.png', clip });
await B.close();
process.exit(fail ? 1 : 0);
