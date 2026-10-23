# -*- coding: utf-8 -*-
"""研报「每日总结」：三、可信程度分析 与 四、综合结论与关注要点 横向排列（左三右四）。

纪律：
  * 模板是 CRLF，读写必须 newline="" 保字节；
  * 每个锚点 assert count==1，命中 0 直接抛错（不静默不写）。
"""
import io
import sys
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

CALL_ANCHOR = " this._decorateConflictTable(el);\r\n"
CALL_NEW = (
    " this._decorateConflictTable(el);\r\n"
    " // 【2026-10-21】三/四节横向排列（左「三、可信程度分析」右「四、综合结论与关注要点」）。\r\n"
    " // 放在最后：前面几处装饰只挪表格内部节点，不改 h2 层级；这里重排的是节级结构。\r\n"
    " this._layoutDailyTwoCol(el);\r\n"
)

DEF_ANCHOR = (
    "\r\n"
    "// 【功能】第二节「观点对比与冲突」：①列宽重排（分歧状态收窄、观点与逻辑扩容）；\r\n"
)

METHOD = r'''
// 【功能】2026-10-21 每日总结把「三、可信程度分析」与「四、综合结论与关注要点」并排
//   （左三右四），其余节（总表 / 观点对比）保持原来的上下顺序。
// 【为什么在渲染后改 DOM】两节在 md 里本就是顺序的两个 h2，没有"并排"语义；
//   若改成两列表格会动 LLM 输出格式(且两节主体是有序列表)，所以只在 DOM 层重组。
// 【定位】按**标题文本**找节，不按下标 / 字号（与总表同一纪律）⇒ 将来多一节也不会错位。
// 【幂等】打 root.dataset.rvTwoCol；切日期/类型会整段重渲染，天然重置。
// 【降级】只找到一节、或两节不在同一父级、或四节不在三节之后 ⇒ **原样返回**，
//   绝不把半截内容搬进两列（宁可维持上下排列，也不出现"三在左、四没了"）。
_layoutDailyTwoCol(root) {
if (!root || (root.dataset && root.dataset.rvTwoCol === '1')) return;
const norm = h => (h.textContent || '').replace(/\s+/g, '');
const hs = Array.from(root.querySelectorAll('h2'));
const i3 = hs.findIndex(h => /^[三3]、/.test(norm(h)) && norm(h).indexOf('可信程度') >= 0);
const i4 = hs.findIndex(h => /^[四4]、/.test(norm(h)) && norm(h).indexOf('综合结论') >= 0);
if (i3 < 0 || i4 < 0 || i4 < i3 + 1) return;
const h3 = hs[i3], h4 = hs[i4];
const host = h3.parentNode;
if (!host || host !== h4.parentNode) return;
// ① 收第三节：h3 起、到 h4 前（必须真的走到 h4，中途断链说明结构异常）
const nodes3 = [];
let n = h3;
while (n && n !== h4) { nodes3.push(n); n = n.nextElementSibling; }
if (!nodes3.length || n !== h4) return;
// ② 收第四节：h4 起、到下一个同级 h2 前（四节后面若还有五节，留在外面）
const nodes4 = [];
n = h4;
while (n && n.tagName !== 'H2') { nodes4.push(n); n = n.nextElementSibling; }
if (!nodes4.length) return;
if (root.dataset) root.dataset.rvTwoCol = '1';
const wrap = document.createElement('div');
wrap.className = 'rv-twocol';
// 列宽用 auto-fit：宽屏两列均分，容器不足 700px 自动堆叠成单列（不写死断点，避免侧栏/窄窗挤成两半）。
wrap.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));'
+ 'gap:0 24px;align-items:start;margin:4px 0';
// ★ 先插 wrap 再搬节点：若先 appendChild 搬走 h3，h3 已不在 host 中，insertBefore 会抛 NotFoundError。
host.insertBefore(wrap, h3);
const mkCol = (nodes, cls) => {
const c = document.createElement('div');
c.className = cls;
c.style.cssText = 'min-width:0';   // grid 子项默认 min-width:auto，长表格会撑破单列
nodes.forEach(x => c.appendChild(x));  // appendChild 自带"从原位置移除"，无需先删
return c;
};
wrap.appendChild(mkCol(nodes3, 'rv-twocol-col rv-twocol-left'));
wrap.appendChild(mkCol(nodes4, 'rv-twocol-col rv-twocol-right'));
},
'''

def main():
    raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
    if raw.count("\r\n") == 0:
        sys.exit("模板不是 CRLF，终止")

    c = raw.count(CALL_ANCHOR)
    d = raw.count(DEF_ANCHOR)
    print("anchor call=%d def=%d" % (c, d))
    assert c == 1, "调用锚点命中 %d 次" % c
    assert d == 1, "定义锚点命中 %d 次" % d

    out = raw.replace(CALL_ANCHOR, CALL_NEW, 1)
    out = out.replace(DEF_ANCHOR, "\r\n" + METHOD.strip("\n").replace("\n", "\r\n") + "\r\n\r\n"
                      + "// 【功能】第二节「观点对比与冲突」：①列宽重排（分歧状态收窄、观点与逻辑扩容）；\r\n", 1)

    # 自检：CRLF 纯净、方法定义与调用各 1 处、顺序正确
    assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
    assert out.count("_layoutDailyTwoCol(root) {") == 1
    assert out.count("this._layoutDailyTwoCol(el);") == 1
    assert out.index("this._decorateConflictTable(el);") < out.index("this._layoutDailyTwoCol(el);")

    io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
    print("OK written, bytes=%d, bare_lf=0" % len(out.encode("utf-8")))


if __name__ == "__main__":
    main()
