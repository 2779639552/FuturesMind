# -*- coding: utf-8 -*-
"""
真正把覆盖矩阵移出 .page-tools（上次只改了注释文字，结构没动 —— E2E 抓出来了）。

结构现状（div 深度实测）：
  L3297 <div id="tab-gtja-tools" class="page-tools">   depth 0→1
  L3300 <div class="card">  ① 查询控制                depth 1→2
  L3327 </div>                                        depth 2→1  (card ① 闭合)
  L3339 <div class="card">  覆盖矩阵                  depth 1→2
  L3344 </div>                                        depth 2→1  (矩阵卡闭合)
  L3346 </div>                                        depth 1→0  ← 这才是 .page-tools 的闭合
  L3347 <div class="expander" ...>                    depth 0→1

修法：把 L3346 那个 </div> 上移到 card ① 之后 ⇒ 覆盖矩阵成为 tab 顶层兄弟。
"""
import io

P = 'web_template.html'
raw = io.open(P, 'rb').read()
orig = raw


def rep(old, new, cnt=1, desc=''):
    global raw
    o = old.replace('\n', '\r\n').encode('utf-8')
    n = new.replace('\n', '\r\n').encode('utf-8')
    c = raw.count(o)
    assert c == cnt, '[FAIL] %s: hits=%d want %d' % (desc, c, cnt)
    raw = raw.replace(o, n)
    print('  ok:', desc)


# ① 在 card ① 闭合之后，补上 .page-tools 的闭合
rep(
    '    <div id="gt-note" style="font-size:0.84rem;color:var(--text-secondary);line-height:1.7"></div>\n'
    '  </div>\n'
    '\n'
    '  <!-- 覆盖矩阵(2026-09-30)',
    '    <div id="gt-note" style="font-size:0.84rem;color:var(--text-secondary);line-height:1.7"></div>\n'
    '  </div>\n'
    '  </div><!-- /#tab-gtja-tools : 控制条到此处为止, 只含「① 查询控制」一张矮卡 -->\n'
    '\n'
    '  <!-- 覆盖矩阵(2026-09-30)',
    1, '在 card ① 之后闭合 .page-tools')

# ② 删掉原位置那个多余的闭合（它会多关一层 tab-gtja）
rep(
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\n'
    '    </div>\n'
    '  </div>\n'
    '\n'
    '  </div>\n'
    '  <div class="expander" style="margin-bottom:14px">',
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\n'
    '    </div>\n'
    '  </div>\n'
    '\n'
    '  <div class="expander" style="margin-bottom:14px">',
    1, '删除原 .page-tools 闭合')

assert raw != orig
assert raw.count(b'\n') - raw.count(b'\r\n') == 0
io.open(P, 'wb').write(raw)
print('OK  delta %+d' % (len(raw) - len(orig)))
print('CRLF', raw.count(b'\r\n'), 'bareLF 0')
