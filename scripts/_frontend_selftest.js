// 前端修改后的必跑自测:①静态引用完整性 ②node DOM-stub 运行时。
// 用法: node scripts/_frontend_selftest.js
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("D:/AgentSense/AgentSense/web_template.html", "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("未找到主 JS"); process.exit(1); }
const js = m[1];
let fail = 0;

// ── ① 静态引用完整性 ──
const defined = new Set();
for (const mm of js.matchAll(/^\s{2}(async\s+)?([_a-zA-Z][_a-zA-Z0-9]*)\s*\(/gm)) defined.add(mm[2]);
for (const mm of js.matchAll(/^\s{2}([_a-zA-Z][_a-zA-Z0-9]*):\s*\[/gm)) defined.add(mm[1]);
const refs = new Set();
for (const mm of html.matchAll(/App\.([_a-zA-Z][_a-zA-Z0-9]*)\s*\(/g)) refs.add(mm[1]);
let missing = [...refs].filter((r) => !defined.has(r));
console.log(`[静态] App 方法 ${defined.size} 个 / HTML 引用 ${refs.size} 个 / 缺失 ${missing.length}`);
if (missing.length) { missing.forEach((x) => console.error("  缺失:", x)); fail++; }

// ── ② DOM-stub 运行时(init + 全 tab 切换) ──
function mkEl(id) {
  return {
    id, innerHTML: "", textContent: "", value: "", style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    insertAdjacentHTML() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    setAttribute() {}, getAttribute() { return null; },
    dispose() {}, setOption() {}, resize() {}, options: [], children: [],
  };
}
const els = {};
const doc = {
  documentElement: mkEl("html"),
  getElementById(id) { if (!els[id]) els[id] = mkEl(id); return els[id]; },
  querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return mkEl("_d"); }, body: mkEl("body"), addEventListener() {},
};
const sandbox = {
  document: doc,
  window: { addEventListener() {}, innerWidth: 1920, innerHeight: 1080 },
  console, setTimeout() { return 0; }, clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
  fetch: async (url) => {
    const u = String(url); let data = {};
    if (u.includes("/api/varieties")) data = [{ code: "RB", name: "螺纹钢" }];
    else if (u.includes("/api/research/views")) data = { variety: "RB", name: "", date: "", dates: [], rows: [] };
    else if (u.includes("/api/research/daily")) data = { dates: [] };
    else if (u.includes("/api/research")) data = { reports: [] };
    return { ok: true, json: async () => data, text: async () => JSON.stringify(data) };
  },
  echarts: { init() { return mkEl("c"); }, version: "5" },
  marked: { parse: (s) => String(s || ""), setOptions() {} },
  AbortController: class { abort() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  localStorage: { getItem() { return null; }, setItem() {} },
  URLSearchParams: class { set() {} toString() { return ""; } },
  location: { reload() {} }, alert() {}, confirm() { return false; }, open() { return null; },
};
sandbox.window.document = doc;
vm.createContext(sandbox);
try {
  vm.runInContext(js + "\n;globalThis.__APP__ = (typeof App !== 'undefined' ? App : undefined);", sandbox, { filename: "main.js" });
  console.log("[运行时] 主 JS 加载: OK");
} catch (e) { console.error("[运行时] 主 JS 加载失败:", e.message); process.exit(1); }

const App = sandbox.__APP__;
(async () => {
  try { await App.init(); console.log("[运行时] App.init(): OK"); }
  catch (e) { console.error("[运行时] init 失败:", e.message); process.exit(1); }
  for (const t of ["run", "research", "sentiment", "dashboard", "warehouse"]) {
    try { await App.switchTab(t); console.log(`[运行时] switchTab('${t}'): OK`); }
    catch (e) { console.error(`[运行时] switchTab('${t}') 失败:`, e.message); fail++; }
  }
  console.log(fail === 0 ? "=== 前端自测全部 PASS ===" : `=== ${fail} 项失败 ===`);
  process.exit(fail === 0 ? 0 : 1);
})();
