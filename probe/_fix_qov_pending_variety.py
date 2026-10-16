# -*- coding: utf-8 -*-
"""修正「点行进入详情」的边缘风险：点中的品种若不在下拉里，**不许静默换成别的**。

================================ 触发场景 ================================
`_loadQuantMeta` 首次填充 `#q-variety` 时会按"待选品种"选中。若该品种**不在
`/api/quant/meta` 返回的池内**（理论上总览只返回池内品种，但在**池变更**、
**接口降级**、或**手工传 `?varieties=` 指定池外品种**时会出现），原来的实现是
`sel.value = (want && valid(want)) ? want : <默认品种>` —— 于是**静默换成了别的品种**：
用户点的是 A，看到的是 B 的 K 线/信号，**页面上没有任何提示**。
这比"该品种各卡报错"糟糕得多（错标的而不自知）。

================================ 修法 ================================
**一律尊重用户点中的品种**：不在下拉里就补一个 option 再选它。
随后该品种的各接口若确实不支持，会各自 `ok:false` 并显示空态 —— 那是**可见**的失败，
而静默换标的不是。

（E2E 里就是用池外的 `CU` 测出了这个分支：原实现回退成 `BR`。）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "web_template.html"

OLD = ''' const want = this._qovPendingSym;
 const valid = code => (d.varieties || []).some(x => x.code === code);
 this._qovPendingSym = null;
 sel.value = (want && valid(want)) ? want
 : ((pref && valid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));'''

NEW = ''' const valid = code => (d.varieties || []).some(x => x.code === code);
 const want = this._qovPendingSym;
 this._qovPendingSym = null;
 if (want && !valid(want)) {
 // 点中的品种不在池内（总览正常只返回池内品种 ⇒ 只在**池变更/接口降级/手工指定
 // 池外品种**时走到）。这里**仍然补一个 option 并选它** —— 静默换成别的品种会让
 // 用户看错标的且毫无提示，比"该品种各卡各自报错"危险得多。
 const o = document.createElement('option');
 o.value = want;
 o.textContent = want;
 sel.appendChild(o);
 }
 sel.value = want || ((pref && valid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));'''


def main() -> int:
    raw = TARGET.read_bytes()
    if raw.count(b"\r\n") == 0:
        print("✘ web_template.html 应为 CRLF，中止")
        return 2
    src = raw.decode("utf-8").replace("\r\n", "\n")

    print("=" * 74)
    print("修正：点中的品种不在下拉里时，不许静默换成别的")
    print("=" * 74)

    n = src.count(OLD)
    print(f"\n[1] 锚点预检：命中 {n} 次（期望 1）")
    if n != 1:
        print("✘ 锚点异常 —— 已中止，文件未改动")
        return 3

    src = src.replace(OLD, NEW, 1)
    print("    ✔ 已改为「一律尊重点中的品种」")

    print("\n[2] 自检")
    checks = [
        ("新逻辑在位", "if (want && !valid(want)) {" in src),
        ("旧的回退写法已去除", "(want && valid(want)) ? want" not in src),
        ("仍保留默认选中回退", "sel.value = want || ((pref && valid(pref.code))" in src),
    ]
    bad = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad:
        print(f"\n✘ 自检未过：{bad} —— 已中止，文件未改动")
        return 4

    TARGET.write_bytes(src.replace("\n", "\r\n").encode("utf-8"))
    chk = TARGET.read_bytes()
    l2 = chk.count(b"\n") - chk.count(b"\r\n")
    print(f"\n[3] 已写入 {len(raw)} → {len(chk)} bytes ({len(chk) - len(raw):+d})")
    print(f"    行尾: bareLF={l2} => {'✔ 纯 CRLF' if l2 == 0 else '✘'}")
    print("\n✔ 修正完成")
    return 0 if l2 == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
