// 【护栏】社会库存 · 季节性数据整形（2026-10-21）
//
// 为什么需要: 本次改造把「社会库存」从阶梯线换成季节性图，直接复用了数据仓库那套
//   `_seasonalFromSeries` + `_doyOf`。这两个函数是**纯数据整形**，一旦算错：
//     ① 闰年不归位 ⇒ 2024-03-01 落到 2/29 的格子上，2024 整条曲线 3 月后错一格；
//     ② 同日重复不覆盖 ⇒ 同一天出现两个点，折线来回跳；
//     ③ latest 取错点 ⇒ 图上"最新值"标注指向旧数据（用户看到的数字与 badge 不一致）。
//   这些都是**不报错**的错误，必须用真数据断言。
//
// 做法: 按花括号配平抠出两个方法的真实源码，在桩 this 上执行。
//
// 用法: node tests/js/inv_seasonal_harness.mjs [模板路径]
//        退出码 0 = 全通过 / 1 = 有断言失败 / 2 = 取源码失败
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = process.argv[2] || path.join(ROOT, 'web_template.html');
if (!fs.existsSync(TEMPLATE)) { console.error(`模板不存在: ${TEMPLATE}`); process.exit(2); }

const LINES = fs.readFileSync(TEMPLATE, 'utf8').split('\n').map(l => l.replace(/\r$/, ''));

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

const SRC = extractMethod('_seasonalFromSeries').replace(/,\s*$/, '');
const SRC_DOY = extractMethod('_doyOf').replace(/,\s*$/, '');
// 两个方法都是对象字面量的简写形式 ⇒ 直接拼成一个对象（_seasonalFromSeries 内部
// 通过 this._doyOf 做闰年对齐，必须同一个 this）
const App = eval('({' + SRC + ',' + SRC_DOY + '})');

let pass = 0, fail = 0;
const check = (name, ok, extra = '') => {
  ok ? pass++ : fail++;
  console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}${ok ? '' : '  ' + extra}`);
};

// ── 0. _doyOf 自检（后面所有断言都建立在它之上）──
if (typeof App._doyOf !== 'function') {
  // 兜底：直接按方法体里的表达式重新实现一份会失去意义 ⇒ 取源码失败就退出
  console.error('_doyOf 没挂上，无法验证');
  process.exit(2);
}

// ── 1. 三年周频序列 → 三个年份桶，按 doy 升序 ──
{
  const series = [];
  // 2024-01-05 起每周一期，跨到 2026-10-16（模拟真实社会库存：周频、3 年）
  let t = Date.UTC(2024, 0, 5), i = 0;
  const end = Date.UTC(2026, 9, 16);
  while (t <= end) {
    const d = new Date(t);
    const iso = d.toISOString().slice(0, 10);
    series.push([iso, 80 + (i % 17)]);
    t += 7 * 86400000; i++;
  }
  const r = App._seasonalFromSeries(series);
  const ys = Object.keys(r.years);
  check('三年序列 → 3 个年份桶', ys.length === 3, ys.join(','));
  check('年份键升序', ys.join(',') === ys.slice().sort().join(','), ys.join(','));
  const y24 = r.years['2024'];
  check('每年点数 ≈52（周频）', Math.abs(y24.length - 52) <= 2, 'n=' + y24.length);
  const doys = y24.map(p => p[0]);
  check('doy 升序', doys.every((v, k) => k === 0 || v > doys[k - 1]), 'first10=' + doys.slice(0, 10));
}

// ── 2. 闰年对齐：2024-03-01 必须落到 doy 59（非闰年 3/1 的位置），不是 60 ──
{
  const r = App._seasonalFromSeries([['2024-02-29', 1], ['2024-03-01', 2]]);
  const pts = r.years['2024'];
  check('2024-02-29 → doy 59', pts[0][0] === 59, 'doy=' + pts[0][0]);
  check('2024-03-01 与 2/29 同格(取后者)', pts.length === 1 && pts[0][1] === 2,
    JSON.stringify(pts));
  const r2 = App._seasonalFromSeries([['2025-03-01', 5]]);
  check('平年 2025-03-01 → doy 59', r2.years['2025'][0][0] === 59, 'doy=' + r2.years['2025'][0][0]);
}

// ── 3. 最新点 & 环比差 ──
{
  const r = App._seasonalFromSeries([['2026-10-02', 90], ['2026-10-09', 95.5], ['2026-10-16', 97.9]]);
  check('latest 取最后一点', r.latest && r.latest.val === 97.9, JSON.stringify(r.latest));
  check('latest.diff = 与前一值之差', r.latest && Math.abs(r.latest.diff - 2.4) < 1e-6,
    String(r.latest && r.latest.diff));
  check('latest.doy 对应 10-16', r.latest && r.latest.doy === App._doyOf(2026, 10, 16),
    String(r.latest && r.latest.doy));
}

// ── 4. 脏数据：非法日期、非数值一律丢弃（不能变成 0 或 NaN 点）──
{
  const r = App._seasonalFromSeries([
    ['2026-10-16', 97.9], ['not-a-date', 50], ['2026-10-17', null], ['2026-10-18', 'x'],
    ['', 12], [null, 33],
  ]);
  const n = Object.values(r.years).reduce((s, a) => s + a.length, 0);
  check('只保留 1 个合法点', n === 1, 'n=' + n);
  check('非法值不产生 NaN', r.years['2026'].every(p => isFinite(p[1])), JSON.stringify(r.years));
}

// ── 5. 同日重复：后值覆盖（与后端 to_seasonal_years 同口径）──
{
  const r = App._seasonalFromSeries([['2026-05-01', 10], ['2026-05-01', 12]]);
  check('同日重复取后值', r.years['2026'].length === 1 && r.years['2026'][0][1] === 12,
    JSON.stringify(r.years['2026']));
}

// ── 6. 不足 2 年时年份数=1（调用方据此决定是否画季节性）──
{
  const r = App._seasonalFromSeries([['2026-06-01', 1], ['2026-07-01', 2]]);
  check('单年序列年份数=1（调用方应回退走势图）', Object.keys(r.years).length === 1,
    Object.keys(r.years).join(','));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
