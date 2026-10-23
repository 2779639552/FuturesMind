# -*- coding: utf-8 -*-
"""把「研报页二级页签」应用到**原项目**(D:\\AgentSense\\AgentSense)。

只搬研报页那一处：
  · 页面头（RESEARCH / 研报 / 一句话）
  · 4 个二级页签（问答 / 每日总结 / 单品种 / 观点总览）
  · 问答卡所在容器 #tab-research-tools(.page-tools) 跟随「问答」联动显隐

**不搬**：表面反转等全站样式（那会改变所有页）、实验副本的只读开关。
所有改动都是追加；删掉两个 id 块即可回退。备份由调用方先做。

⚠ CRLF：本文件不用反斜杠转义（chr(13)+chr(10) 拼行尾）。
"""
import io

CRLF = chr(13) + chr(10)
P = "web_template.html"
s = io.open(P, encoding="utf-8", newline="").read()
assert CRLF in s and chr(10) not in s.replace(CRLF, ""), "不是纯 CRLF"

if 'id="rsh-subtabs"' in s:
    raise SystemExit("已应用过, 中止")

CSS = """
<!-- ══════════════════════════════════════════════════════════════════
     【2026-10-19】研报页二级页签（问答 / 每日总结 / 单品种 / 观点总览）
     来源：实验副本 D:\\AgentSenseLab 的 LAB v3，仅搬"研报页"这一处。
     只切换顶层 .card 的 display，**不改 DOM 归属、不重新请求数据**。
     研报的问答卡装在 .page-tools(#tab-research-tools) 里，随「问答」联动显隐。
     回退：删掉 <style id="rsh-subtabs"> 与 <script id="rsh-subtabs"> 两块即可。
     ══════════════════════════════════════════════════════════════════ -->
<style id="rsh-subtabs">
.rsh-head { display: flex; align-items: flex-end; gap: 18px; flex-wrap: wrap; padding: 2px 2px 14px; }
.rsh-tt { display: flex; flex-direction: column; gap: 3px; }
.rsh-kicker { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--brand); font-weight: 600; }
.rsh-title { margin: 0; font-size: 27px; line-height: 1.2; letter-spacing: -0.02em;
  font-weight: 650; color: var(--text); }
.rsh-head p { margin: 0 0 2px; color: var(--text-muted); font-size: 13px; line-height: 1.6; max-width: 720px; }
.rsh-subnav { display: flex; gap: 6px; padding: 0 2px 14px; flex-wrap: wrap; }
.rsh-subnav button { border: 1px solid #e6e6eb; background: #fff; color: var(--text-secondary);
  font-size: 13px; padding: 6px 14px; border-radius: 999px; cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease; }
.rsh-subnav button:hover { border-color: #c9c9cf; color: var(--text); }
.rsh-subnav button.on { background: var(--text); color: var(--bg); border-color: var(--text); font-weight: 500; }
[data-theme="dark"] .rsh-subnav button { background: #1d1d1f; border-color: #2c2c2e; }
[data-theme="dark"] .rsh-subnav button.on { background: #f5f5f7; color: #1d1d1f; border-color: #f5f5f7; }
@media (max-width: 900px) { .rsh-title { font-size: 22px; } .rsh-head p { font-size: 12.5px; } }
</style>
""".replace("\n", CRLF)

JS = """
<!-- 【2026-10-19】研报页二级页签: 只挂 switchTab, 不碰任何业务逻辑 -->
<script id="rsh-subtabs">
(function () {
  var TAB = 'research';
  var CONF = {
    def: 'daily',
    // 研报的问答卡装在 .page-tools 里, 只有「问答」页签才显示这个容器
    toolsIn: 'qa',
    items: [
      { k: 'qa',     n: '问答',     m: ['研报问答'] },
      { k: 'daily',  n: '每日总结', m: ['每日总结'] },
      { k: 'single', n: '单品种',   m: ['单品种'] },
      { k: 'views',  n: '观点总览', m: ['观点总览'] }
    ]
  };
  var META = ['RESEARCH', '研报', '问答 / 每日总结 / 单品种 / 观点总览：四个入口，一条知识主线'];
  var state = { k: CONF.def };

  function topCards(tab) {
    return [].slice.call(tab.children).filter(function (e) {
      return e.classList && e.classList.contains('card');
    });
  }
  function ensureHead(tab) {
    var h = tab.querySelector(':scope > .rsh-head');
    if (h) return h;
    h = document.createElement('div');
    h.className = 'rsh-head';
    h.innerHTML = '<div class="rsh-tt"><span class="rsh-kicker">' + META[0] + '</span>'
      + '<div class="rsh-title">' + META[1] + '</div></div><p>' + META[2] + '</p>';
    tab.insertBefore(h, tab.firstElementChild);
    return h;
  }
  function ensureBar(tab) {
    var bar = tab.querySelector(':scope > .rsh-subnav');
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'rsh-subnav';
    CONF.items.forEach(function (it) {
      var b = document.createElement('button');
      b.type = 'button';
      b.dataset.k = it.k;
      b.textContent = it.n;
      b.onclick = function () { state.k = it.k; apply(tab); };
      bar.appendChild(b);
    });
    var head = tab.querySelector(':scope > .rsh-head');
    if (head && head.nextSibling) tab.insertBefore(bar, head.nextSibling);
    else tab.insertBefore(bar, tab.firstChild);
    return bar;
  }
  function apply(tab) {
    var k = state.k;
    topCards(tab).forEach(function (c) {
      var h = c.querySelector('h3');
      var t = h ? h.textContent : '';
      var hit = CONF.items.filter(function (it) {
        return it.m.some(function (m) { return t.indexOf(m) >= 0; });
      })[0];
      // 认不出归属的卡片一律保留(宁可多显示, 不要凭空藏掉内容)
      c.style.display = (!hit || hit.k === k) ? '' : 'none';
    });
    var tools = tab.querySelector(':scope > .page-tools');
    if (tools) tools.style.display = (k === CONF.toolsIn) ? '' : 'none';
    var bar = tab.querySelector(':scope > .rsh-subnav');
    if (bar) {
      [].slice.call(bar.children).forEach(function (b) {
        b.classList.toggle('on', b.dataset.k === k);
      });
    }
  }
  function mount(tabId) {
    if (tabId !== TAB) return;
    var tab = document.getElementById('tab-' + TAB);
    if (!tab) return;
    ensureHead(tab);
    ensureBar(tab);
    apply(tab);
  }
  function bind() {
    if (typeof App === 'undefined' || !App.switchTab || App.__rshBound) return !!App;
    var orig = App.switchTab;
    App.switchTab = function (t) {
      var r = orig.apply(this, arguments);
      try { if (t === TAB) setTimeout(function () { mount(t); }, 60); } catch (e) {}
      return r;
    };
    App.__rshBound = true;
    var act = document.querySelector('nav button.active');
    if (act && act.dataset.tab === TAB) mount(TAB);
    return true;
  }
  if (!bind()) {
    var n = 0;
    var t = setInterval(function () { if (bind() || ++n > 40) clearInterval(t); }, 150);
  }
})();
</script>
""".replace("\n", CRLF)

anchor_head = "</style>" + CRLF + "</head>"
assert s.count(anchor_head) == 1, s.count(anchor_head)
s = s.replace(anchor_head, "</style>" + CRLF + CSS + "</head>", 1)

anchor_body = "</body>" + CRLF + "</html>"
assert s.count(anchor_body) == 1, s.count(anchor_body)
s = s.replace(anchor_body, JS + "</body>" + CRLF + "</html>", 1)

io.open(P, "w", encoding="utf-8", newline="").write(s)
print("research subtabs applied; size", len(s))
