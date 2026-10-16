// 【护栏】多策略对比(runMultiCompare)代码资产的契约测试
//
// 为什么需要(2026-09-29): 交易回测页于 2026-09-11 下线, 但 runMultiCompare 的实现
//   被要求"补回为代码资产、暂不接 UI"。脱离 UI 的代码最容易被后续会话改坏且无人发现,
//   且它承载着一个真实历史 bug 的修复(买入持有基准曾写死 days=365 → 改区间数值不变)。
//
// 断言(每条对应一类真实缺陷):
//   1) 未接 UI 时元素全缺 → 守卫必须优雅退出 + 单次可读提示(不得白屏崩)
//   2) 缺单个容器 → 提示需精确列出缺失项(便于将来接回 UI 时定位)
//   3) 开关读取必须可选链(旧写法 .checked 在元素缺失时 TypeError)
//   4) 【历史 bug 回归】取数天数动态计算, 且锚点=区间内首条价格(不得退回 ?days=365)
//   5) fmtStrengthPct 空值安全 + 国内涨跌色(买入红/卖出绿)
//   6) 【不接 UI 的硬约束】页面内不得出现任何真实调用入口
//
// 用法: node tests/js/multi_compare_asset_harness.mjs   退出码 0 = 全通过。
//
// 【为什么按行切分而不是花括号配平】runMultiCompare 内部含**嵌套模板字符串**
//   (形如 `${cond ? `…` : `…`}`), 朴素 backtick 状态机会提前闭合, 切出 564 行(实际 290 行)。

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'web_template.html'), 'utf8');
// 【必剥 \r】web_template.html 是 CRLF 行尾(Windows 编辑器保存的), 若直接 split('\n')
//   每行末尾会残留 '\r', 而下面所有锚点都以 `$` 结尾 → 永远匹配不上, 护栏恒崩在
//   "start not found"。这不是被测代码坏了, 是护栏自己的取行方式对行尾不设防。
const LINES = html.split('\n').map(l => l.replace(/\r$/, ''));

/** 取 App 对象里某个方法体: 从 `  name(...) {` 行到其后的 `  },` 行(含边界行)。 */
function bodyLines(startRe, stopRe) {
  const s = LINES.findIndex(l => startRe.test(l));
  if (s < 0) throw new Error('start not found: ' + startRe);
  for (let i = s + 1; i < LINES.length; i++) {
    if (stopRe.test(LINES[i])) return LINES.slice(s, i + 1);
  }
  throw new Error('stop not found after line ' + s);
}

const mcLines = bodyLines(/^  async runMultiCompare\(\) \{$/, /^  \},$/);
const fmtStart = LINES.findIndex(l => /^  fmtStrengthPct\(ts\) \{$/.test(l));
if (fmtStart < 0) throw new Error('fmtStrengthPct not found');
let fmtEnd = fmtStart;
for (let i = fmtStart + 1; i < LINES.length; i++) { if (/^  \},$/.test(LINES[i])) { fmtEnd = i; break; } }
const fmtLines = LINES.slice(fmtStart, fmtEnd + 1);

const mcSrc = mcLines.join('\n');
const src = '({' + [mcSrc, fmtLines.join('\n')].map(s => s.trim().replace(/,\s*$/, '')).join(',\n') + '})';

let pass = 0, fail = 0;
const check = (name, ok) => { ok ? pass++ : fail++; console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}`); };

// ── 场景1: 未接 UI(元素全缺) → 守卫需优雅退出并给出可读提示 ──
{
  const calls = [];
  global.document = { getElementById: () => null, querySelectorAll: () => [] };
  const App = eval(src);
  App.toast = (m, t) => calls.push([m, t]);
  App._defaultCode = () => 'SC';
  App._chartInstances = {};
  const r = await App.runMultiCompare();
  check('未接UI_无异常返回', r === undefined);
  check('未接UI_单次提示', calls.length === 1);
  check('未接UI_提示可读', /未接入页面/.test(calls[0]?.[0] || '') && /trade-variety/.test(calls[0]?.[0] || ''));
  check('未接UI_提示为error级', calls[0]?.[1] === 'error');
}

// ── 场景2: 仅缺单个容器 → 提示应精确列出缺失项(便于接回 UI 时定位) ──
{
  const have = new Set(['trade-variety', 'trade-start', 'trade-end', 'trading-summary', 'trading-stats']);
  const calls = [];
  global.document = { getElementById: id => (have.has(id) ? { value: '', checked: false } : null), querySelectorAll: () => [] };
  const App = eval(src);
  App.toast = (m, t) => calls.push([m, t]);
  App._defaultCode = () => 'SC';
  App._chartInstances = {};
  await App.runMultiCompare();
  check('缺单项_提示列出trading-detail', /trading-detail/.test(calls[0]?.[0] || ''));
}

// ── 场景3: 口径加固 — 开关缺失不应抛错(旧写法会 TypeError) ──
check('加固_风控开关可选链', mcSrc.includes("getElementById('risk-enabled')?.checked"));
check('加固_成本开关可选链', mcSrc.includes("getElementById('cost-enabled')?.checked"));

// ── 场景4: 历史 bug 回归(2026-08-25 修复) ──
check('口径_取数天数动态', mcSrc.includes('fetchDays') && !mcSrc.includes('?days=365'));
check('口径_区间首价锚点', mcSrc.includes('commonDates.length === 0) base = p.close'));

// ── 场景5: fmtStrengthPct 空值安全 + 国内涨跌色 ──
{
  global.document = { getElementById: () => null, querySelectorAll: () => [] };
  const App = eval(src);
  check('fmt_空值返回空串', App.fmtStrengthPct(null) === '' && App.fmtStrengthPct({ action: 'buy' }) === '');
  check('fmt_买入用红(国内语义)', App.fmtStrengthPct({ action: 'buy', strength_pct: 0.5 }).includes('var(--red)'));
  check('fmt_卖出用绿(国内语义)', App.fmtStrengthPct({ action: 'sell', strength_pct: 0.5 }).includes('var(--green)'));
}

// ── 场景6: 不接 UI 的硬约束 — 页面里不得出现任何调用入口 ──
// 只查**真实入口**(onclick / button / 勾选面板), 不查注释与函数内提示文案 ——
// 函数头说明与守卫提示本就会提到"多策略对比", 用纯文本匹配会误报。
{
  check('未接UI_无onclick入口', !/onclick="App\.runMultiCompare\(\)"/.test(html));
  check('未接UI_无按钮入口', !/<button[^>]*onclick="[^"]*runMultiCompare[^"]*"/.test(html));
  check('未接UI_无勾选面板', !/class="multi-cb"/.test(html));
}

console.log(fail ? `\nRESULT: FAIL(${fail}/${pass + fail})` : `\nRESULT: ALL_PASS(${pass})`);
process.exit(fail ? 1 : 0);
