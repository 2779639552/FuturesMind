// 行为验证: 把 web_template.html 的内联脚本整体载入 Node(桩 DOM/echarts/fetch),
// 直接调用 App 的品种下拉填充与叠加图加载, 断言"图表用的是自己那个下拉的品种"。
// —— 比"源码里有没有某字符串"的文本断言强得多: 文本断言挡不住"分支永远不执行"。
//
// 用法:  node overlay_variety_picker_harness.mjs [模板路径]
//        模板路径省略时, 默认取同仓库的 AgentSense/web_template.html
//        退出码 0=全部通过, 1=有断言失败, 2=沙箱桩不足/脚本结构变了
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_TEMPLATE = path.resolve(HERE, '..', '..', 'web_template.html');
const TEMPLATE = process.argv[2] || DEFAULT_TEMPLATE;
if (!fs.existsSync(TEMPLATE)) {
  console.error(`模板不存在: ${TEMPLATE}`);
  process.exit(2);
}
const html = fs.readFileSync(TEMPLATE, 'utf8');
const blocks = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].filter(
  (m) => !/src=/.test(m[1])
);
if (blocks.length !== 1) {
  console.error(`期望恰好 1 个内联脚本块, 实际 ${blocks.length}`);
  process.exit(1);
}
const code = blocks[0][2];

// ─────────── DOM 桩 ───────────
function mkClassList() {
  const s = new Set();
  return {
    add: (c) => s.add(c),
    remove: (c) => s.delete(c),
    contains: (c) => s.has(c),
    toggle: (c, f) => {
      if (f === undefined) s.has(c) ? s.delete(c) : s.add(c);
      else f ? s.add(c) : s.delete(c);
    },
  };
}
class El {
  constructor(id = '') {
    this.id = id;
    this._options = [];
    this.style = {};
    this.classList = mkClassList();
    this.dataset = {};
    this.clientHeight = 420;
    this.clientWidth = 800;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.checked = false;
    this.disabled = false;
    this.children = [];
    this._value = '';
    this._html = '';
    this._text = '';
  }
  get options() { return this._options; }
  set innerHTML(v) { this._html = v; this._options = []; }
  get innerHTML() { return this._html; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set value(v) { this._value = String(v); }
  get value() { return this._value; }
  appendChild(o) { this._options.push(o); this.children.push(o); return o; }
  removeChild() {}
  remove() {}
  addEventListener() {}
  removeEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getAttribute() { return null; }
  setAttribute() {}
  insertAdjacentHTML() {}
  scrollIntoView() {}
  focus() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 800, height: 420 }; }
  closest() { return null; }
}
const els = new Map();
const getEl = (id) => {
  if (!els.has(id)) els.set(id, new El(id));
  return els.get(id);
};

const documentStub = {
  readyState: 'complete',
  body: new El('body'),
  head: new El('head'),
  getElementById: getEl,
  createElement: () => new El(''),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  removeEventListener: () => {},
};
documentStub.documentElement = new El('html');
documentStub.documentElement.getAttribute = (n) => (n === 'data-theme' ? 'dark' : null);

// ─────────── echarts 桩 ───────────
const chartInstances = [];
const echartsStub = {
  init(el) {
    const inst = {
      el,
      opts: [],
      setOption(o) { this.opts.push(o); this.last = o; },
      dispose() {},
      resize() {},
      clear() {},
      on() {},
      off() {},
      getOption() { return {}; },
    };
    chartInstances.push(inst);
    return inst;
  },
  graphic: {
    LinearGradient: class { constructor(a, b, c, d, stops) { this.stops = stops; } },
  },
};

// ─────────── marked 桩(页面 CDN 依赖: /static/marked.min.js) ───────────
const markedStub = Object.assign((s) => String(s), {
  parse: (s) => String(s),
  setOptions() {},
  use() {},
});

// ─────────── fetch 桩 ───────────
let fetchCalls = [];
let nextResponse = { ok: true, status: 200, body: { overlay: [], _meta: {} }, headers: { get: () => 'application/json' } };
let throwOnFetch = null;
const fetchStub = async (url) => {
  fetchCalls.push(String(url));
  if (throwOnFetch) throw new Error(throwOnFetch);
  const c = nextResponse;
  // headers.get 是必须的: 模板开头把 window.fetch 包了一层, HTTP>=400 时会读
  // resp.headers.get('content-type') 判断是不是 HTML 错误页, 缺了它会 TypeError
  return { ok: c.ok, status: c.status, headers: c.headers, json: async () => c.body };
};

// ─────────── 组装沙箱 ───────────
const sandbox = {
  console,
  document: documentStub,
  fetch: fetchStub,
  echarts: echartsStub,
  marked: markedStub,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  navigator: { userAgent: 'node', clipboard: { writeText: async () => {} } },
  location: { href: 'http://localhost:5000/', hostname: 'localhost', reload() {} },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  EventSource: class { constructor() { this.close = () => {}; } addEventListener() {} },
  MutationObserver: class { constructor() {} observe() {} disconnect() {} takeRecords() { return []; } },
  IntersectionObserver: class { constructor() {} observe() {} unobserve() {} disconnect() {} },
  ResizeObserver: class { constructor() {} observe() {} unobserve() {} disconnect() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  innerWidth: 1440,
  innerHeight: 900,
  addEventListener: () => {},
  removeEventListener: () => {},
  alert: () => {},
  confirm: () => true,
  prompt: () => null,
  Blob: class {},
  FileReader: class {},
  XMLHttpRequest: class {},
  URL: { createObjectURL: () => 'blob:', revokeObjectURL() {} },
  performance: { now: () => Date.now() },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
vm.runInContext(
  code + '\n;globalThis.__App = (typeof App !== "undefined") ? App : null;',
  ctx,
  { filename: 'inline-script.js' }
);
const App = sandbox.__App;

// ─────────── 断言 ───────────
let pass = 0;
const fails = [];
function ok(cond, msg) {
  if (cond) { pass++; console.log(`  ✓ ${msg}`); }
  else { fails.push(msg); console.log(`  ✗ ${msg}`); }
}
const eq = (a, b, msg) => ok(a === b, `${msg}  (实际=${JSON.stringify(a)}, 期望=${JSON.stringify(b)})`);

if (!App) {
  console.error('内联脚本未导出 App —— 沙箱桩不足或脚本结构变了');
  process.exit(2);
}

const ovSel = getEl('overlay-sym');
const symSel = getEl('sym');
getEl('lookback-days').value = '180';

App._chartInstances = {};
App._varieties = [
  { code: 'BR', name: '丁二烯橡胶', sentiment_posts: 0 },
  { code: 'SC', name: '原油', sentiment_posts: 1022 },
  { code: 'M', name: '豆粕', sentiment_posts: 272 },
];

console.log('\n[1] 下拉填充: 选项完整性 / 默认值 / 保留用户选择');
App._populateOverlaySymSelect();
eq(ovSel.options.length, 3, '选项数 = 品种数');
eq(ovSel.options[1].value, 'SC', '选项值 = 品种代码');
ok(/SC - 原油 \(1022帖\)/.test(ovSel.options[1].textContent), '选项文案含 代码-名称(帖数)');
eq(ovSel.value, 'SC', '首次填充默认选「第一个有情绪帖的品种」(跳过 0 帖的 BR)');

ovSel.value = 'M';
App._populateOverlaySymSelect();
eq(ovSel.value, 'M', '重填后保留用户已选品种(数据更新回调会再次触发填充)');

// 品种池变化(所选品种被移除)时回落到有帖品种, 而不是留空
App._varieties = [{ code: 'SC', name: '原油', sentiment_posts: 1022 }];
App._populateOverlaySymSelect();
eq(ovSel.value, 'SC', '所选品种已不在池内时回落到有帖的第一个品种');

console.log('\n[2] 图表数据源: 跟随自己的下拉, 不跟随运行分析品种');
App._varieties = [
  { code: 'BR', name: '丁二烯橡胶', sentiment_posts: 53 },
  { code: 'SC', name: '原油', sentiment_posts: 1022 },
  { code: 'M', name: '豆粕', sentiment_posts: 272 },
];
App._populateOverlaySymSelect();
symSel.value = 'SC';       // 运行分析页选的是原油
ovSel.value = 'M';         // 情绪数据页这张图选的是豆粕
fetchCalls = [];
nextResponse = {
  ok: true, status: 200,
  body: {
    overlay: [
      { date: '2026-09-01', close: 3000, avg_score: 0.2, simple_avg: 0.1 },
      { date: '2026-09-02', close: 3010, avg_score: -0.1, simple_avg: 0.0 },
    ],
    _meta: { sentiment_start: '2026-09-01', sentiment_end: '2026-09-02', price_start: '2026-09-01', price_end: '2026-09-02' },
  },
};
await App.loadOverlayChart();
eq(fetchCalls.length, 1, '只发一次请求');
eq(fetchCalls[0], '/api/overlay/M?days=180', '请求的是 #overlay-sym 的品种(M), 不是 #sym 的 SC');
eq(symSel.value, 'SC', '运行分析的 #sym 未被改动(两者解耦)');
eq(getEl('overlay-badge').textContent.startsWith('2 个交易日'), true, '徽标按返回数据更新交易日数');

console.log('\n[3] 切换下拉 → 只重画本图, 不动 #sym 也不重载帖子');
ovSel.value = 'SC';
fetchCalls = [];
App.onOverlaySymChange();
await new Promise((r) => setTimeout(r, 0));   // 等内部 await 链走完
ok(fetchCalls.length >= 1, 'onOverlaySymChange 触发了重新加载');
eq(fetchCalls[0], '/api/overlay/SC?days=180', '重新请求新品种(SC)');
eq(symSel.value, 'SC', '#sym 与图表品种此刻同为 SC, 但来源不同(下面验证改 #sym 不影响图)');
symSel.value = 'BR';                          // 运行分析改品种
eq(ovSel.value, 'SC', '改 #sym 后 #overlay-sym 保持不动');

console.log('\n[4] 失败/空数据: 必须清掉旧图, 不能让上个品种的曲线留在屏上');
const beforeCount = chartInstances.length;
nextResponse = { ok: false, status: 404, body: {}, headers: { get: () => 'application/json' } };
await App.loadOverlayChart();
ok(chartInstances.length > beforeCount, '非 200(JSON 响应)时重建了图表实例(旧曲线被 dispose)');
const failOpt = chartInstances[chartInstances.length - 1].last;
ok(JSON.stringify(failOpt).includes('数据加载失败'), '失败时图内给出提示文案');
eq(getEl('overlay-badge').textContent, '⚠ 数据加载失败', '失败时徽标明确标注');

// 【关键】模板的 window.fetch 包装层在「HTTP>=400 且响应为 HTML 错误页」时会直接
// throw, 根本不回到 !r.ok 分支 —— 这条路径必须由 catch 兜住, 否则服务端 500 时
// 屏上会留着上一个品种的曲线。
const beforeCount2 = chartInstances.length;
throwOnFetch = '服务返回了 HTML 错误页(HTTP 500)';
await App.loadOverlayChart();
throwOnFetch = null;
ok(chartInstances.length > beforeCount2, 'fetch 抛异常(包装层 throw)时同样重建图表实例');
eq(getEl('overlay-badge').textContent, '⚠ 数据加载失败', '抛异常时徽标同样标注失败(不会留旧值)');
eq(fetchCalls[fetchCalls.length - 1], '/api/overlay/SC?days=180', '抛异常路径请求的仍是当前下拉品种');

nextResponse = {
  ok: true, status: 200,
  body: { overlay: [], _meta: { sentiment_start: null, sentiment_end: null, price_start: null, price_end: null } },
  headers: { get: () => 'application/json' },
};
await App.loadOverlayChart();
const emptyOpt = chartInstances[chartInstances.length - 1].last;
ok(JSON.stringify(emptyOpt.graphic || '').includes('暂无数据'), '完全无数据时提示「暂无数据」');
ok(!JSON.stringify(emptyOpt.graphic || '').includes('当前仅展示价格走势'), '完全无数据时不再误报「仅展示价格走势」');

nextResponse = {
  ok: true, status: 200,
  body: { overlay: [{ date: '2026-09-03', close: 3000, avg_score: null, simple_avg: null }], _meta: {} },
  headers: { get: () => 'application/json' },
};
await App.loadOverlayChart();
const priceOnlyOpt = chartInstances[chartInstances.length - 1].last;
ok(JSON.stringify(priceOnlyOpt.graphic || '').includes('当前仅展示价格走势'), '有价格无情绪时保留原提示(两档区分)');

console.log(`\n结果: ${pass} 通过, ${fails.length} 失败`);
if (fails.length) { fails.forEach((f) => console.log('  ✗ ' + f)); process.exit(1); }
console.log('全部通过');
