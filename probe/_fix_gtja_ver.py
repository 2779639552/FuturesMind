# -*- coding: utf-8 -*-
"""① _SEC_IDX_VER +1（结构变了必须重建索引）；② 修掉 tab-gtja-tools 内过时注释。"""
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


# ① 版本号 +1 —— 覆盖矩阵从 .page-tools 内移到 tab 顶层, 模块数变了, 旧索引必须作废重建
rep("_SEC_IDX_VER: '15',", "_SEC_IDX_VER: '16',", 1, '_SEC_IDX_VER 15 → 16')

# ② 控制条内的过时注释（覆盖矩阵已不在这里）
rep(
    "  <div id=\"tab-gtja-tools\" class=\"page-tools\">\n"
    "  <!-- ① 查询控制 + 覆盖矩阵 -->",
    "  <div id=\"tab-gtja-tools\" class=\"page-tools\">\n"
    "  <!-- ① 查询控制（⚠️ 本控制条**只许放这一张矮卡**：覆盖矩阵原在此处，因吸顶\n"
    "       遮挡 + 进不了小模块，已于 2026-09-30 移到 .page-tools 外面。详见下方注释。） -->",
    1, '修正 tab-gtja-tools 内过时注释')

assert raw != orig
assert raw.count(b'\n') - raw.count(b'\r\n') == 0
io.open(P, 'wb').write(raw)
print('OK  delta %+d' % (len(raw) - len(orig)))
print('CRLF', raw.count(b'\r\n'), 'bareLF 0')
