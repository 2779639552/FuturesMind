// 【护栏】点击模块名回总览（2026-10-21 用户要求）
//
// 为什么需要: 这是"两视图互斥"的状态切换，纯文本断言（grep 有没有 _navBackToOverview）
//   抓不到真正的失效：
//     ① 判据写成 `dv.style.display === ''`（浏览器默认空串），而实际"隐藏"是内联 'none'
//        ⇒ 永远不进回退分支，点了模块名只会把明细重新拉一遍（用户看到的是"点了没反应"）；
//     ② 把 ov.style.display 设成 'block' —— 表面能用，但视图原本靠 CSS 默认布局，
//        一旦外层改 display 语义就错；必须是空串（交还给样式表）；
//     ③ 总览态误触发 ⇒ 每次点模块名都被强制滚到顶部、并丢掉用户正在看的筛选位置。
//
// 做法: 按花括号配平抠出 _navBackToOverview 真实源码，在桩 DOM 上执行。
//
// 用法: node tests/js/nav_back_overview_harness.mjs [模板路径]
//        退出码 0 = 全通过 / 1 = 有断言失败 / 2 = 取源码失败
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = process.argv[2] || path.join(ROOT, 'web_template.html');
if (!fs.existsSync(TEMPLATE)) { console.error(`模板不存在: ${TEMPLATE}`); process.exit(2); }

const html = fs.readFileSync(TEMPLATE, 'utf8');
const LINES = html.split('\n').map(l => l.replace(/\r$/, ''));

function extractMethod(name) {
  const s = LINES.findIndex(l => new RegExp('^\\s*' + name + '\\s*\\([^)]*\\)\\s*\\{$').test(l));
  if (s < 0) { console.error('找不到方法定义: ' + name); process.exit(2); }
  let depth = 0, started = false;
  for (let i = s; i < LINES.length; i++) {
    for (const ch of LINES[i]) {
      if (ch === '{') { depth++; started = true; }
      else if (ch === '}') depth--;
    }
    if (started && depth === 0) return LINES.slice(s, i + 1).join('\n');
  }
  console.error('方法花括号未闭合: ' + name); process.exit(2);
}

const SRC = extractMethod('_navBackToOverview').replace(/,\s*$/, '');
const App = eval('({' + SRC + '})');

let pass = 0, fail = 0;
const check = (name, ok, extra = '') => { ok ? pass++ : fail++; console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}${ok ? '' : '  ' + extra}`); };

// ── 桩：只有 getElementById + style.display ──
function ctx(spec) {
  const nodes = {};
  Object.keys(spec).forEach(id => { nodes[id] = spec[id] === null ? null : { id, style: { display: spec[id] } }; });
  const scrolls = [];
  globalThis.document = { getElementById: id => (id in nodes ? nodes[id] : null) };
  globalThis.window = { scrollTo: o => scrolls.push(o) };
  return { nodes, scrolls };
}

// ── 1. 技术面明细 → 回总览 ──
{
  const { nodes, scrolls } = ctx({ 'q-detail-view': '', 'q-overview-view': 'none' });
  const r = App._navBackToOverview('quant');
  check('技术面: 返回 true', r === true, String(r));
  check('技术面: 明细被隐藏', nodes['q-detail-view'].style.display === 'none', nodes['q-detail-view'].style.display);
  check('技术面: 总览恢复为空串(交还样式表)', nodes['q-overview-view'].style.display === '', JSON.stringify(nodes['q-overview-view'].style.display));
  check('技术面: 回到顶部', scrolls.length === 1 && scrolls[0].top === 0, JSON.stringify(scrolls));
}

// ── 2. 基本面明细 → 回总览 ──
{
  const { nodes, scrolls } = ctx({ 'f-detail-view': '', 'f-overview-view': 'none' });
  const r = App._navBackToOverview('fund');
  check('基本面: 返回 true', r === true, String(r));
  check('基本面: 明细被隐藏', nodes['f-detail-view'].style.display === 'none');
  check('基本面: 总览恢复为空串', nodes['f-overview-view'].style.display === '');
  check('基本面: 回到顶部', scrolls.length === 1);
}

// ── 3. 已在总览: 不动作、不滚动（避免每次点模块名都被弹回顶部）──
{
  const { nodes, scrolls } = ctx({ 'q-detail-view': 'none', 'q-overview-view': '' });
  const r = App._navBackToOverview('quant');
  check('总览态: 返回 false', r === false, String(r));
  check('总览态: 视图未变', nodes['q-detail-view'].style.display === 'none' && nodes['q-overview-view'].style.display === '');
  check('总览态: 不滚动', scrolls.length === 0, JSON.stringify(scrolls));
}

// ── 4. 其它 tab: 不动作 ──
{
  const { scrolls } = ctx({});
  ['run', 'research', 'sentiment', 'warehouse', undefined, ''].forEach(t => {
    const r = App._navBackToOverview(t);
    check(`非量化模块(${String(t)}): 返回 false`, r === false, String(r));
  });
  check('非量化模块: 不滚动', scrolls.length === 0);
}

// ── 5. 元素缺失: 不抛异常、返回 false ──
{
  ctx({ 'q-detail-view': null, 'q-overview-view': null });
  let err = '', r;
  try { r = App._navBackToOverview('quant'); } catch (e) { err = String(e); }
  check('元素缺失: 不抛异常', err === '', err);
  check('元素缺失: 返回 false', r === false, String(r));
}

// ── 6. 只认内联 'none' 这一种隐藏写法（回归：曾经误用 === ''）──
{
  const { nodes } = ctx({ 'f-detail-view': 'none', 'f-overview-view': '' });
  const r = App._navBackToOverview('fund');
  check('隐藏判据认内联 none', r === false, String(r));
  check('隐藏判据: 不误改总览', nodes['f-overview-view'].style.display === '');
}

// ── 7. 没有 window.scrollTo 的环境也不崩（老浏览器/测试桩）──
{
  globalThis.document = { getElementById: id => ({ style: { display: id.endsWith('detail-view') ? '' : 'none' } }) };
  globalThis.window = {};
  let err = '', r;
  try { r = App._navBackToOverview('quant'); } catch (e) { err = String(e); }
  check('无 scrollTo: 不抛异常', err === '', err);
  check('无 scrollTo: 仍返回 true', r === true, String(r));
}

console.log(`\n===== ${pass} passed / ${fail} failed =====`);
process.exit(fail ? 1 : 0);
