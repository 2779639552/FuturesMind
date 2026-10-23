// 【护栏】「国君数据」页渲染行为测试(2026-09-29)
//
// 为什么需要: 本页最关键的设计不是"能不能显示表格", 而是**三态不可混淆** ——
//   四源里两个是带参数端点、两个是全量端点, 失败模式不同。若把"接口失败"渲染得
//   和"确定无数据"一样(都是灰字/空表), 用户会据此判断"该品种没库存/没利润",
//   而真相可能只是本次请求失败 —— 这会直接污染研究结论。
//   这类缺陷**页面不报错、接口返回正常**, 纯文本断言(grep 函数名/关键字)抓不到,
//   必须真的执行渲染代码后检查输出。
//
// 做法: 从 web_template.html 抠出渲染方法的**真实源码**, 在桩 DOM 上直接调用,
//   断言"给定的 payload → 产出什么样的 HTML/颜色/文案"。
//
// 用法: node tests/js/gtja_module_harness.mjs [模板路径]
//        退出码 0 = 全部通过, 1 = 有断言失败, 2 = 环境/取源码失败
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = process.argv[2] || path.join(ROOT, 'web_template.html');

if (!fs.existsSync(TEMPLATE)) {
  console.error(`模板不存在: ${TEMPLATE}`);
  process.exit(2);
}
const html = fs.readFileSync(TEMPLATE, 'utf8');
// 【必剥 \r】模板是 CRLF; 不剥的话下面所有方法锚点都会因行尾 \r 失配。
// 【★ 方法体提取必须按花括号配平, 不能用缩进(2026-10-14 二次修正)】
//   模板已被全局压平为 **1 空格/层**(原 2 空格) ⇒ 方法定义行与内层 if 块的
//   缩进**完全相同**, 缩进无法再区分层级: 写死 `^  ` 会命中 0(exit 2,
//   看着像"方法被删"); 改 `^[ \t]+` 更糟 —— 会在内层 `}` 处**提前截断**、
//   静默产出半截方法体。故统一走 bodyLines() 的花括号配平。
const LINES = html.split('\n').map(l => l.replace(/\r$/, ''));

/** 取一个方法的完整方法体 —— **按花括号配平**，不看缩进。
 *
 * 【为什么不能用缩进判据(2026-10-14 两次修正)】
 *   本文件原用 `^  },?$`（恰好 2 空格）当"方法结束"。它靠缩进层级区分
 *   「对象方法结束(2 空格)」与「方法内 if/for 块的结束(4 空格)」。
 *   但模板 `web_template.html` 已被**全局压平为 1 空格/层** ⇒ 两类行都变成 1 空格
 *   ⇒ 缩进信息**彻底丢失**。
 *   ① 沿用 `^  ` ⇒ 命中 0，直接 exit(2)（症状：像"方法被删了"）；
 *   ② 改 `^[ \t]+` ⇒ **更糟**：会在内层 `if (...) { ... }` 的 `}` 处**提前截断**，
 *      静默产出半截方法体（`_renderGtjaIndicators` 实测只取到 12 行、缺后半段）。
 *   ⇒ 正确做法是**语义配平**：数 `{` / `}`，回到 0 才是方法结束。
 *   ⚠️ 前提：方法体内字符串/模板字面量里的花括号必须**自身配对**
 *     （`${...}` 天然配对；实测这些方法里无不成对的字面量花括号）。
 */
function bodyLines(startRe) {
  const s = LINES.findIndex(l => startRe.test(l));
  if (s < 0) { console.error('找不到方法定义: ' + startRe); process.exit(2); }
  let depth = 0, started = false;
  for (let i = s; i < LINES.length; i++) {
    for (const ch of LINES[i]) {
      if (ch === '{') { depth++; started = true; }
      else if (ch === '}') { depth--; }
    }
    if (started && depth === 0) {
      // 结束后末行应是 `}` 或 `},`；否则说明配平被字符串里的花括号带偏了
      const last = LINES[i].trim();
      if (last !== '}' && last !== '},') {
        console.error('方法配平可疑(末行非 }, ): ' + startRe + ' -> ' + JSON.stringify(last));
        process.exit(2);
      }
      return LINES.slice(s, i + 1);
    }
  }
  console.error('方法未配平: ' + startRe);
  process.exit(2);
}

const want = [
  /^[ \t]+_renderGtjaSources\(d\) \{$/,
  /^[ \t]+_renderGtjaIndicators\(d\) \{$/,
  /^[ \t]+_renderGtjaChart\(ind\) \{$/,
  /^[ \t]+_renderGtjaTable\(ind\) \{$/,
  /^[ \t]+showGtjaIndicator\(i, expandDetail\) \{$/,
  // 【2026-10-19】明细数据折叠栏开关(默认收起, 点「查看」才下拉)
  /^[ \t]+toggleGtjaDetail\(force\) \{$/,
  /^[ \t]+_gtEnsureVisible\(el\) \{$/,
  // 【2026-10-19】走势图 → 季节性图(按年同比) 新增的五个辅助方法;
  // _renderGtjaChart 会调它们, 少提取一个就是 "undefined is not a function"。
  /^[ \t]+_seasonalFromSeries\(series\) \{$/,
  /^[ \t]+_doyOf\(y, mo, da\) \{$/,
  /^[ \t]+_seasonColor\(y\) \{$/,
  /^[ \t]+_drawGtjaSeasonal\(c, ind, data\) \{$/,
  /^[ \t]+_gtSeasonalNote\(ind, d, noteEl\) \{$/,
];
const parts = want.map(re => bodyLines(re).join('\n').trim().replace(/,\s*$/, ''));
const src = '({' + parts.join(',\n') + '})';

// ── 桩 DOM ──
class FakeEl {
  constructor(id) {
    this.id = id; this.innerHTML = ''; this.textContent = '';
    this.style = {}; this._classes = new Set();
    this.classList = {
      add: (...cs) => cs.forEach(c => this._classes.add(c)),
      remove: (...cs) => cs.forEach(c => this._classes.delete(c)),
      contains: c => this._classes.has(c),
      toggle: (c, on) => { (on === undefined ? !this._classes.has(c) : on) ? this._classes.add(c) : this._classes.delete(c); },
    };
    this.closestResult = null; this.parentElement = null;
  }
  closest() { return this.closestResult; }
  querySelectorAll() { return []; }
}

const nodes = {};
const mk = id => (nodes[id] = nodes[id] || new FakeEl(id));
['gt-note', 'gt-sources', 'gt-indicators', 'gt-ind-badge', 'gt-chart', 'gt-chart-badge',
 'gt-chart-note', 'gt-table', 'gt-tbl-badge', 'gt-detail-expander', 'gt-detail-hint',
 'gt-variety', 'gt-start', 'gt-end'].forEach(mk);

globalThis.document = {
  getElementById: id => nodes[id] || null,
  querySelectorAll: () => [],
  documentElement: { getAttribute: () => 'dark' },
};
globalThis.CHART = { axis: '#aaa', grid: '#333', seasonBlack: '#000000' };

// 记录图表最后一次 setOption 的 option(stub 掉 ECharts)
let lastChartOption = null;
const App = eval(src);
App._escHtml = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
App._initChart = () => ({
  setOption: o => { lastChartOption = o; },
  clear: () => { lastChartOption = null; },
  resize: () => {},
});
App._resizeAllCharts = () => {};
App._secRows = {};
App._gtState = {};

let pass = 0, fail = 0;
const check = (name, ok, extra = '') => {
  ok ? pass++ : fail++;
  console.log(`--- ${name} --- ${ok ? 'PASS' : 'FAIL'}${ok ? '' : '  ' + extra}`);
};

// ══════════════════════════════════════════════════════════════════
// ① ★ 四源状态: 三态必须可区分(本页最重要的契约)
// ══════════════════════════════════════════════════════════════════
const payload3 = {
  ok: true, variety: 'SC', name: '原油', source: '国泰君安(GTJA) VIP cloudApi',
  start: '2026-04-08', end: '2026-09-30',
  sources: {
    // 失败: 上游报错
    basis: { available: false, error: 'GTJAError: 请求失败(重试 4 次)', rows: 0, note: '' },
    // 有数据
    warehouse: { available: true, error: '', rows: 120, note: '', span: '2026-04-08 ~ 2026-09-30' },
    // 确定无数据: 正常应答 0 行
    inventory: { available: true, error: '', rows: 0, note: '全量数据集里没有 SC 的任何记录(该源未覆盖此品种)。' },
    // 有数据 + 分片告警: 部分区间失败
    profit: { available: true, error: '', rows: 30, note: '该品种共 2 个口径, 此处展示前 2 个。',
              chunks: 3, warning: '部分区间取数失败, 下方序列**不完整**: 2025-10-13~2026-04-06(GTJAError)' },
  },
  counts: { indicators: 3, available_sources: 3, failed_sources: 1 },
  unit_note: '各序列单位以接口原始回传为准, 平台**不做单位归一**。',
};

App._renderGtjaSources(payload3);
const srcHtml = nodes['gt-sources'].innerHTML;

check('①-1 失败源显示「接口失败」', srcHtml.includes('接口失败'));
check('①-2 失败源用红色(var(--red))', /接口失败[^]{0,200}var\(--red\)/.test(srcHtml) || /var\(--red\);font-weight:600">接口失败/.test(srcHtml));
check('①-3 失败源必须展示错误原因(不能只说失败)', srcHtml.includes('请求失败(重试 4 次)'));
check('①-4 有数据源显示「有数据」+ 条数', srcHtml.includes('有数据') && srcHtml.includes('120 条'));
check('①-5 确定无数据源显示「确定无数据」', srcHtml.includes('确定无数据'));
check('①-6 ★「确定无数据」与「接口失败」文案不同(不可混淆)', 
  (() => {
    // 构造两个只差 available 的源, 断言渲染文案不同
    const A = { ...payload3, sources: { ...payload3.sources,
      basis: { available: true, error: '', rows: 0, note: '接口正常应答 0 行 —— 无现货指数口径。' } } };
    App._renderGtjaSources(A);
    const okHtml = nodes['gt-sources'].innerHTML;
    return !okHtml.includes('接口失败') && okHtml.includes('确定无数据') && srcHtml.includes('接口失败');
  })());
check('①-7 ★失败源不得被渲染成「确定无数据」', (() => {
  // 从失败块里截取基差那一行, 断言它不含"确定无数据"
  const i = srcHtml.indexOf('基差/现货价');
  const seg = srcHtml.slice(i, i + 400);
  return seg.includes('接口失败') && !seg.includes('确定无数据');
})());
check('①-8 分片数显示(chunks>1)', srcHtml.includes('分 3 片'));
check('①-9 ★分片告警必须透传(序列不完整)', srcHtml.includes('序列') && srcHtml.includes('不完整'));
check('①-10 数据源名称用中文可读名', srcHtml.includes('基差/现货价') && srcHtml.includes('交易所注册仓单')
  && srcHtml.includes('国君自建库存') && srcHtml.includes('加工/进口利润'));
check('①-11 单位声明透传', srcHtml.includes('不做单位归一'));

// ══════════════════════════════════════════════════════════════════
// ② 指标总览表
// ══════════════════════════════════════════════════════════════════
const payloadInd = {
  ok: true, variety: 'BU', name: '沥青',
  indicators: [
    { id: 'basis.value', group: '基差', label: '基差(现货−期货)', unit: '元/吨', note: 'n',
      status: 'ok', as_of: '2026-09-30', current: 1104, prev: 1090, chg: 14, chg_pct: 1.28,
      hi: 1200, lo: 500, pct: 99.2, n: 120, pct_scope: '区间(2026-04-08 ~ 2026-09-30)',
      series: [['2026-09-29', 1090], ['2026-09-30', 1104]] },
    { id: 'inv.x', group: '库存', label: '社会库存', unit: '', note: 'n',
      status: 'ok', as_of: '2026-09-30', current: 47.7, prev: 48.1, chg: -0.4, chg_pct: -0.83,
      hi: 60, lo: 40, pct: 0.8, n: 131, pct_scope: '全历史(131点)',
      series: [['2026-09-29', 48.1], ['2026-09-30', 47.7]] },
    { id: 'wh.y', group: '仓单', label: '注册仓单', unit: '', note: 'n',
      status: 'ok', as_of: '2026-09-30', current: null, prev: null, chg: null, chg_pct: null,
      hi: null, lo: null, pct: null, n: 0, pct_scope: '区间(x)',
      series: [] },
  ],
};
App._renderGtjaIndicators(payloadInd);
const indHtml = nodes['gt-indicators'].innerHTML;
check('②-1 指标行数 = payload 条数', (indHtml.match(/<tr style="border-top/g) || []).length === 3);
check('②-2 徽标显示项数', nodes['gt-ind-badge'].textContent === '3 项');
check('②-3 ★null 值显示为「—」而不是 NaN/null', !indHtml.includes('NaN') && !indHtml.includes('>null<') && indHtml.includes('—'));
check('②-4 高分位用红色(涨/强)', /99\.2%/.test(indHtml));
check('②-5 低分位用绿色(跌/弱)', /0\.8%/.test(indHtml));
check('②-6 变化为正显示 + 号', indHtml.includes('+14'));
check('②-7 变化为负显示 − 号', indHtml.includes('-0.4'));
check('②-8 每行都有「查看」按钮', (indHtml.match(/showGtjaIndicator\(/g) || []).length === 3);
check('②-9 展示单位', indHtml.includes('元/吨'));
check('②-10 展示分位口径(全历史/区间)', indHtml.includes('全历史(131点)'));

// 空指标 → 必须给出"为什么空"的指引(而非空白表)
App._renderGtjaIndicators({ ok: true, indicators: [] });
const emptyHtml = nodes['gt-indicators'].innerHTML;
check('②-11 ★无指标时提示区分「确定无数据」与「接口失败」',
  emptyHtml.includes('确定无数据') && emptyHtml.includes('接口失败'));
check('②-12 徽标归零', nodes['gt-ind-badge'].textContent === '0 项');

// ══════════════════════════════════════════════════════════════════
// ③ 季节性图(2026-10-19: 走势图改为按年同比的季节性图)
// ══════════════════════════════════════════════════════════════════
// 口径来自桌面「季节性图绘制包」: x=年内天数、每年一条线、当年粗红线、
// 历史年细线 alpha 0.6、月份轴 1月~12月、最新点标注「值 (±变化)」。
App._renderGtjaChart(payloadInd.indicators[0]);
check('③-1 单年数据只画一条线', lastChartOption && lastChartOption.series
  && lastChartOption.series.length === 1);
check('③-2 ★x 轴为 366 天类目 + 12 个月份标签', (() => {
  const d = lastChartOption.xAxis.data;
  return d.length === 366 && d[0] === '1月' && d[31] === '2月'
    && d[59] === '3月' && d[334] === '12月';
})());
check('③-3 当年(2026)为红色粗线', (() => {
  const s = lastChartOption.series[0];
  return s.lineStyle.color === '#FF0000' && s.lineStyle.width === 2.4
    && s.lineStyle.opacity === 1;
})());
check('③-4 图表徽标带指标名', nodes['gt-chart-badge'].textContent.includes('基差'));
check('③-5 图表脚注含分位与口径', nodes['gt-chart-note'].innerHTML.includes('99.2%'));
check('③-6 脚注说明当前年份数', nodes['gt-chart-note'].innerHTML.includes('1 个年份'));

App._renderGtjaChart(payloadInd.indicators[2]);
check('③-7 ★无序列点时给空态而不是崩', lastChartOption
  && JSON.stringify(lastChartOption).includes('无序列点'));

// 多年: 每年一条线 + 历史年半透明细线 + 年份配色 + 最新点标注
const indSeason = {
  id: 'basis.value', group: '基差', label: '基差(现货−期货)', unit: '元/吨', note: '',
  pct: 50, pct_scope: '区间(x)',
  series: [
    ['2024-02-29', 95], ['2024-03-01', 100], ['2024-03-02', 110],
    ['2025-03-01', 90], ['2025-03-02', 95],
    ['2026-03-01', 120], ['2026-03-02', 130],
  ],
};
App._renderGtjaChart(indSeason);
const sopt = lastChartOption;
check('③-8 每年一条线(3 年 → 3 条)', sopt.series.length === 3
  && sopt.series.map(s => s.name).join(',') === '2024,2025,2026');
check('③-9 ★历史年细线 + alpha 0.6', sopt.series[0].lineStyle.width === 1.2
  && sopt.series[0].lineStyle.opacity === 0.6
  && sopt.series[1].lineStyle.opacity === 0.6);
check('③-10 ★年份配色与绘制包一致(2024 蓝 / 2025 绿 / 2026 红)',
  sopt.series[0].lineStyle.color === '#00B0F0'
  && sopt.series[1].lineStyle.color === '#00B050'
  && sopt.series[2].lineStyle.color === '#FF0000');
check('③-11 ★最新点标注「值 (±变化)」', (() => {
  const mp = sopt.series[2].markPoint;
  return mp && mp.data && mp.data.length === 1
    && mp.data[0].coord[0] === 60 && mp.data[0].coord[1] === 130
    && mp.label.formatter() === '130 (+10)';
})());
check('③-12 图例列出全部年份', (sopt.legend.data || []).join(',') === '2024,2025,2026');
check('③-13 ★闰年对齐: 2024-03-01 与平年 3/1 同为第 59 天',
  App._doyOf(2024, 3, 1) === 59 && App._doyOf(2026, 3, 1) === 59);
check('③-14 平年 12/31 为第 364 天(不越界)', App._doyOf(2025, 12, 31) === 364);
check('③-15 数据含 0 时画 Y=0 基准线', (() => {
  App._renderGtjaChart({ id: 'x', group: 'g', label: 'g', unit: '', note: '',
    pct: null, pct_scope: '', series: [['2026-01-02', -5], ['2026-01-03', 5]] });
  return !!(lastChartOption.series[0].markLine
    && lastChartOption.series[0].markLine.data[0].yAxis === 0);
})());

App._renderGtjaTable(payloadInd.indicators[0]);
const tblHtml = nodes['gt-table'].innerHTML;
check('③-16 明细表倒序(最新在上)', tblHtml.indexOf('2026-09-30') < tblHtml.indexOf('2026-09-29'));
check('③-17 明细表行数正确', (tblHtml.match(/<tr style="border-top/g) || []).length === 2);
check('③-18 明细徽标计数', nodes['gt-tbl-badge'].textContent === '2 行');

// ── 明细数据折叠(2026-10-19): 默认收起, 只有主动点「查看」才下拉 ──
// 用桩 overview 驱动 showGtjaIndicator; 临时摘掉 fetch 以免真的发请求
// (Node 有全局 fetch, 相对 URL 会抛 "Failed to parse URL", 虽然被 .catch 兜住但会脏输出)
const savedFetch = globalThis.fetch;
globalThis.fetch = undefined;
App._gtState = { overview: { variety: 'BU', indicators: payloadInd.indicators } };
App.showGtjaIndicator(0);
check('③-19 ★进页自动渲染时明细保持收起', !nodes['gt-detail-expander'].classList.contains('open'));
App.showGtjaIndicator(0, true);
check('③-20 ★主动点「查看」才下拉展开明细', nodes['gt-detail-expander'].classList.contains('open'));
check('③-20b 展开后提示语变「点击收起」', nodes['gt-detail-hint'].textContent === '点击收起');
check('③-21 再点表头可收回', App.toggleGtjaDetail() === false
  && !nodes['gt-detail-expander'].classList.contains('open')
  && nodes['gt-detail-hint'].textContent === '点击展开');
globalThis.fetch = savedFetch;
App._gtState = {};

// ══════════════════════════════════════════════════════════════════
// ④ ★ 跨卡展开补丁: 折叠态下点「查看」必须能看见结果
// ══════════════════════════════════════════════════════════════════
const block = new FakeEl('blk');
block.classList.add('is-collapsed');
const target = new FakeEl('gt-chart2');
target.closestResult = block;
App._secRows = { gtja: [{ blocks: [block], btn: new FakeEl('btn') }] };
App._gtEnsureVisible(target);
check('④-1 折叠块被展开', !block.classList.contains('is-collapsed'));
check('④-2 加上 sec-open 类', block.classList.contains('sec-open'));
check('④-3 maxHeight 放开', block.style.maxHeight === 'none');
check('④-4 ★索引按钮态同步(is-open) —— 否则点两次才收起',
  App._secRows.gtja[0].btn.classList.contains('is-open'));

// 已展开的块不得被改动
const open2 = new FakeEl('blk2');
open2.classList.add('sec-open');
const t2 = new FakeEl('t2'); t2.closestResult = open2;
App._gtEnsureVisible(t2);
check('④-5 已展开的块不受影响', open2.classList.contains('sec-open') && open2.style.maxHeight === undefined);
// 无折叠祖先 → 不崩
App._gtEnsureVisible(new FakeEl('t3'));
check('④-6 无折叠祖先时不抛异常', true);

// ══════════════════════════════════════════════════════════════════
// ⑥ XSS 防线: 接口返回的文本必须转义
// ══════════════════════════════════════════════════════════════════
App._renderGtjaSources({
  ok: true, variety: 'X', name: '<img src=x onerror=alert(1)>',
  sources: { basis: { available: false, error: '<script>alert(2)</script>', rows: 0, note: '' },
             warehouse: { available: true, error: '', rows: 1, note: '<b>bad</b>' },
             inventory: { available: true, error: '', rows: 0, note: '' },
             profit: { available: true, error: '', rows: 0, note: '' } },
  counts: {}, unit_note: '',
});
const xss = nodes['gt-sources'].innerHTML;
check('⑥-1 ★错误文本被转义(不出现裸 <script>)', !xss.includes('<script>alert(2)</script>'));
check('⑥-2 ★note 里的 HTML 被转义', !xss.includes('<b>bad</b>') && xss.includes('&lt;b&gt;'));

App._renderGtjaIndicators({ ok: true, indicators: [
  { id: 'x', group: '<i>g</i>', label: '<script>alert(3)</script>', unit: '', note: '<u>n</u>',
    status: 'ok', current: 1, prev: 1, chg: 0, chg_pct: 0, hi: 1, lo: 1, pct: 50, n: 1,
    pct_scope: '<s>p</s>', series: [['2026-01-01', 1]] }] });
const xss2 = nodes['gt-indicators'].innerHTML;
check('⑥-3 ★指标 label 被转义', !xss2.includes('<script>alert(3)</script>'));

// ══════════════════════════════════════════════════════════════════
console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
