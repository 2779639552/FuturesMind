# -*- coding: utf-8 -*-
"""badge 跟随覆盖矩阵：从「① 查询控制」标题移到「覆盖矩阵」卡标题。

理由：该 badge 渲染的是**覆盖矩阵**数据（`d.total 品种 · d.start~d.end`）。
矩阵已独立成卡, badge 留在控制条上会出现两个问题：
  ① 语义错位（查询控制标题里挂着矩阵的元信息）；
  ② 矩阵折叠后 badge 仍在控制条里可见 ⇒ 用户不知道它在说哪块。
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


# ① 从「① 查询控制」标题移除 badge
rep(
    '    <h3>① 查询控制 <span class="badge badge-info" id="gt-matrix-badge"></span></h3>',
    '    <h3>① 查询控制</h3>',
    1, '从查询控制标题移除 badge')

# ② 挂到「覆盖矩阵」标题
rep(
    '    <h3>覆盖矩阵</h3>',
    '    <h3>覆盖矩阵 <span class="badge badge-info" id="gt-matrix-badge"></span></h3>',
    1, 'badge 挂到覆盖矩阵标题')

assert raw != orig
assert raw.count(b'\n') - raw.count(b'\r\n') == 0
io.open(P, 'wb').write(raw)
print('OK  delta %+d' % (len(raw) - len(orig)))
