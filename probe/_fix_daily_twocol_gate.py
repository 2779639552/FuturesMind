# -*- coding: utf-8 -*-
"""把幂等判据从 root.dataset 改成"结构里已有容器"，标记改挂在容器自身上。

病根: loadDailySummary 每次切日期/类型都是 `el.innerHTML = ...` 重建**子节点**，
      `#res-daily` 自身的 data-* 属性会留下来。若用它当闸门，第二次渲染直接 return
      ⇒ 两列只在首次生效，切一次日期就变回上下排列，且页面不报错（静默失效）。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

PAIRS = [
    # ① 闸门
    (
        "if (!root || (root.dataset && root.dataset.rvTwoCol === '1')) return;\r\n",
        "if (!root) return;\r\n"
        "// ★ 幂等判据必须看**结构**(已有两列容器就跳过)，不能用 root 上的 dataset：\r\n"
        "//   loadDailySummary 每次切日期都是 el.innerHTML = ... 重建子节点，root 属性会留下来\r\n"
        "//   ⇒ 拿 dataset 当闸门会让两列只在首次生效，之后静默失效(不报错，只是又变回上下排)。\r\n"
        "if (root.querySelector && root.querySelector('.rv-twocol')) return;\r\n",
    ),
    # ② 标记跟着容器走
    (
        "if (root.dataset) root.dataset.rvTwoCol = '1';\r\n"
        "const wrap = document.createElement('div');\r\n"
        "wrap.className = 'rv-twocol';\r\n",
        "const wrap = document.createElement('div');\r\n"
        "wrap.className = 'rv-twocol';\r\n"
        "wrap.dataset.rvTwoCol = '1';   // 标记挂在容器上，随下次重渲染一起清掉\r\n",
    ),
    # ③ 顶部注释里的幂等描述
    (
        "// 【幂等】打 root.dataset.rvTwoCol；切日期/类型会整段重渲染，天然重置。\r\n",
        "// 【幂等】看结构(已有 .rv-twocol 就跳过)；切日期/类型会整段重渲染，容器随之消失，\r\n"
        "//   所以标记挂在容器上而不是 root 上(见下方说明)。\r\n",
    ),
]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    assert out.count(old) == 1, "锚点命中 %d 次: %r" % (out.count(old), old[:60])
    out = out.replace(old, new, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
assert out.count("root.dataset.rvTwoCol") == 0, "root 上不该再有幂等标记"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
