// 行为验证: 把 web_template.html 的内联脚本整体载入 Node(桩 DOM/echarts/fetch),
// 直接调用 App._refreshResearch 渲染研报列表, 断言"界面显示的是本地时间而非 UTC 原值"。
//
// 【要防的回归】全库时间戳统一 UTC(SQLite datetime('now')), 而模板原先直接 slice 显示
// ⇒ 界面比本地早 8 小时。用户把"采集 01:32"(实为本地 09:32)读成"凌晨入库的东西卡到早上"
// (2026-09-22 实测)。本护栏锁死两件事:
//   1) _utcToLocal 的转换语义(含空值/脏值/跨天/已带时区标记的串);
//   2) **发布日期不被转换**——publish_date 是本地语义的日期串, 一起转会整体偏移一天。
//
// 用法:  TZ=Asia/Shanghai node tz_display_harness.mjs [模板路径]
//        (必须显式指定 TZ, 否则断言无意义 → 退出码 2)
//        模板路径省略时, 默认取同仓库的 AgentSense/web_template.html
//        退出码 0=全部通过, 1=有断言失败, 2=环境/沙箱桩不足
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_TEMPLATE = path.resolve(HERE, '..', '..', 'web_template.html');
const TEMPLATE = process.argv[2] || DEFAULT_TEMPLATE;

// ─────────── 前置: 时区必须是 +08:00 ───────────
// 本护栏的期望值是"UTC+8 下应有的显示值", 别的时区下断言无意义。
const OFFSET = new Date().getTimezoneOffset();
if (OFFSET !== -480) {
  console.error(
    `要求本地时区为 UTC+8(getTimezoneOffset=-480), 实际 ${OFFSET}。\n` +
      `请用:  TZ=Asia/Shanghai node tz_display_harness.mjs`
  );
  process.exit(2);
}
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
  process.exit(2);
}
const code = blocks[0][2];

// ─────────── DOM 桩 ───────────
// 【关键】必须模拟浏览器里 textContent → innerHTML 的转义语义: 模板的 _escHtml 就是
// `div.textContent = s; return div.innerHTML`。若桩里两者不联动, esc() 会恒返回空串,
// 断言"渲染出的 HTML 含某文本"就会**假阴性**(2026-09-22 实测踩过)。
const escapeTextToHtml = (s) =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\u00a0/g, '&nbsp;');
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
  set innerHTML(v) { this._html = v; this._options = []; this._fromText = false; }
  // textContent 赋值后, innerHTML 应返回**转义后**的文本(浏览器语义, 见上方说明)
  get innerHTML() { return this._fromText ? escapeTextToHtml(this._text) : this._html; }
  set textContent(v) { this._text = String(v); this._fromText = true; }
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

// ─────────── echarts / marked 桩 ───────────
const echartsStub = {
  init() {
    return {
      setOption() {}, dispose() {}, resize() {}, clear() {},
      on() {}, off() {}, getOption() { return {}; },
    };
  },
  graphic: { LinearGradient: class { constructor(a, b, c, d, stops) { this.stops = stops; } } },
};
const markedStub = Object.assign((s) => String(s), {
  parse: (s) => String(s),
  setOptions() {},
  use() {},
});

// ─────────── fetch 桩 ───────────
let fetchCalls = [];
let nextResponses = {};  // url 子串 → body
let defaultBody = { reports: [] };
const fetchStub = async (url) => {
  const u = String(url);
  fetchCalls.push(u);
  let body = defaultBody;
  for (const k of Object.keys(nextResponses)) {
    if (u.includes(k)) { body = nextResponses[k]; break; }
  }
  // headers.get 是必须的: 模板开头把 window.fetch 包了一层, HTTP>=400 时会读
  // resp.headers.get('content-type') 判断是不是 HTML 错误页, 缺了它会 TypeError
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body };
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
  Date,
  Math,
  JSON,
  Intl,
  URLSearchParams,
  AbortController: class { constructor() { this.signal = {}; } abort() {} },
  TextDecoder,
  TextEncoder,
  Promise,
  Object,
  Array,
  String,
  Number,
  Boolean,
  Error,
  RegExp,
  Map,
  Set,
  isNaN,
  parseInt,
  parseFloat,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

const ctx = vm.createContext(sandbox);
try {
  vm.runInContext(code + '\n;globalThis.__App = (typeof App !== "undefined") ? App : null;', ctx, {
    filename: 'web_template_inline.js',
  });
} catch (e) {
  console.error('内联脚本在沙箱中执行失败(桩不足或模板结构变了):');
  console.error(e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n') : e);
  process.exit(2);
}

const App = sandbox.__App;
if (!App || typeof App._utcToLocal !== 'function') {
  console.error('App._utcToLocal 不存在 —— 时区转换工具未接入或被改名');
  process.exit(1);
}

// ─────────── 断言 ───────────
let pass = 0;
const failures = [];
function eq(actual, expected, label) {
  if (actual === expected) {
    pass++;
  } else {
    failures.push(`${label}\n      期望: ${JSON.stringify(expected)}\n      实际: ${JSON.stringify(actual)}`);
  }
}
function ok(cond, label) {
  if (cond) pass++;
  else failures.push(label);
}

// 1) 转换语义
eq(App._utcToLocal('2026-09-22 01:32:41'), '2026-09-22 09:32', 'UTC 时刻应转成本地(到分钟)');
eq(App._utcToLocal('2026-09-22 01:32:41', true), '2026-09-22 09:32:41', 'withSec=true 应保留秒');
eq(App._utcToLocal('2026-09-21 16:30:00'), '2026-09-22 00:30', '跨天: UTC 16:30 应是本地次日 00:30');
eq(App._utcToLocal(''), '', '空串应返回空串');
eq(App._utcToLocal(null), '', 'null 应返回空串');
eq(App._utcToLocal(undefined), '', 'undefined 应返回空串');
eq(App._utcToLocal('garbage'), 'garbage', '无法解析的值应原样返回(不抛)');
eq(App._utcToLocal('2026-09-22T01:32:41Z'), '2026-09-22 09:32', '已带 Z 的 ISO 串不该重复追加 Z');

// 2) _researchTimeLabel: 发布日不转、采集时刻转
eq(
  App._researchTimeLabel({ publish_date: '2026-09-21', uploaded_at: '2026-09-22 01:32:41' }),
  '发布 2026-09-21 · 采集 2026-09-22 09:32',
  '发布日保持本地语义, 采集时刻转本地'
);
eq(
  App._researchTimeLabel({ publish_date: '2026-09-20', uploaded_at: '' }),
  '发布 2026-09-20',
  '只有发布日时不出现"采集"段'
);
eq(
  App._researchTimeLabel({ uploaded_at: '2026-09-22 01:32:41' }),
  '采集 2026-09-22 09:32',
  '只有采集时刻(无发布日)时也要转本地'
);

// 3) 端到端: _refreshResearch 渲染出的列表行应是本地时间
nextResponses = {
  '/api/research?': {
    reports: [
      {
        id: 914, status: 'processing', title: '繁微长图系列:主力合约涨跌全景(2026-09-21)',
        variety: 'LC', source: '东证期货-繁微MCP', direction: '中性', confidence: 0.5,
        publish_date: '2026-09-21', uploaded_at: '2026-09-22 01:32:41', varieties: 'LC',
      },
      {
        id: 913, status: 'skipped', title: '鸡蛋重点数据周度跟踪(第38周)',
        variety: 'JD', source: '东证期货-繁微MCP', direction: '中性', confidence: 0.5,
        publish_date: '', uploaded_at: '2026-09-22 01:31:33', varieties: 'JD',
      },
    ],
  },
  '/api/research/collect': { collecting: false },
  '/api/research/rag/index': { indexing: false },
};
try {
  await App._refreshResearch(true);
} catch (e) {
  console.error('_refreshResearch 调用失败(桩不足):');
  console.error(e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n') : e);
  process.exit(2);
}
const listHtml = getEl('res-list').innerHTML || '';

ok(listHtml.includes('采集 2026-09-22 09:32'), '列表行应显示本地采集时间 09:32');
ok(!listHtml.includes('01:32'), '列表行不应再出现 UTC 原值 01:32');
ok(listHtml.includes('发布 2026-09-21'), '发布日期应原样显示');
ok(listHtml.includes('已跳过'), 'skipped 状态应渲染为「已跳过」徽标');
ok(!/>失败</.test(listHtml), 'skipped 不该被渲染成失败');

const countText = getEl('res-count').textContent || '';
ok(/已跳过/.test(countText), `计数应含「已跳过」(实际: ${countText})`);
ok(!/失败/.test(countText), `skipped 不该计入失败数(实际: ${countText})`);

// 4) 源码级护栏: 不得再出现"把 publish_date 与 uploaded_at 混着 slice"的老写法
const legacyPatterns = [
  /String\(rp\.publish_date \|\| rp\.uploaded_at\)/,
  /String\(d\.publish_date \|\| d\.uploaded_at/,
];
for (const re of legacyPatterns) {
  ok(!re.test(html), `模板残留未区分发布日/采集时刻的老写法: ${re}`);
}

// 5) 【反向护栏】generated_at / collected_at / imported_at 是**本地时间**,绝不可转
//    来源核验(2026-09-22): generated_at ← web_app.py datetime.now().strftime();
//    collected_at ← warrant/site.py dt.datetime.now().isoformat(); imported_at ← variety_convert._now_iso()=datetime.now()。
//    库内 uploaded_at/created_at 才是 SQLite UTC。两套语义混用 ⇒ 一转就错 8 小时(符号还相反)。
const mustNotConvert = [
  /_utcToLocal\(\s*[^)]*\.generated_at\b/,
  /_utcToLocal\(\s*[^)]*\.collected_at\b/,
  /_utcToLocal\(\s*[^)]*\.imported_at\b/,
];
for (const re of mustNotConvert) {
  ok(!re.test(html), `本地时间字段不该被 _utcToLocal 转换(会偏 8 小时): ${re}`);
}

// ─────────── 汇总 ───────────
console.log('='.repeat(72));
console.log(`模板: ${TEMPLATE}`);
console.log(`fetch 调用: ${fetchCalls.length} 次`);
if (failures.length) {
  console.log(`\n❌ 失败 ${failures.length} / 通过 ${pass}`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log(`\n✅ 全部通过: ${pass} 项断言`);
process.exit(0);
