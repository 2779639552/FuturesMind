# -*- coding: utf-8 -*-
"""全品种截面表「表头吸顶」（2026-10-19）。

两处静默失效点，都用护栏钉住：
  ① 容器没带 `.table-scroll` ⇒ 被全站 wrapTable() 再包一层 ⇒ sticky 绑到**不纵向滚动**的
     内层容器上，页面滚动时表头照样上移（实测 226 → 112px）。
  ② 吸顶 CSS 若写进 `@media (max-width:768px)` 块里 ⇒ 桌面端完全不生效，
     而且页面不报错、只是"看起来没做"。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web_template.html").read_bytes().decode("utf-8").replace("\r\n", "\n")


def _style_depth_at(html: str, needle: str) -> int:
    """返回 needle 所在行在 <style> 内的花括号深度（0=全局；>0=在 @media 等块内）。"""
    depth = 0
    in_style = False
    for ln in html.split("\n"):
        if "<style>" in ln:
            in_style = True
        if in_style:
            depth += ln.count("{") - ln.count("}")
            if needle in ln:
                return depth
        if "</style>" in ln:
            in_style = False
    return -1


@pytest.mark.unit
def test_both_scroll_containers_carry_table_scroll_marker():
    """★ 容器必须带 class="table-scroll"，否则被 wrapTable() 二次包装 ⇒ 表头不吸顶。"""
    h = _html()
    for max_h in ("300px", "340px"):
        pat = 'class="table-scroll" style="overflow:auto;max-height:calc(100vh - %s)"' % max_h
        assert pat in h, "缺少 %s 那张表的滚动容器标记（会被二次包装）" % max_h
    # 内联 max-height 必须还在（去掉就没有纵向滚动，sticky 无从触发）
    assert h.count("max-height:calc(100vh - 300px)") >= 1
    assert h.count("max-height:calc(100vh - 340px)") >= 1


@pytest.mark.unit
def test_wrap_table_guard_actually_skips_these_containers():
    """前提校验：wrapTable 的跳过判据确实是「父级带 .table-scroll」。"""
    h = _html()
    i = h.index("function wrapTable(t)")
    seg = h[i:i + 700]
    assert "contains('table-scroll')" in seg, "跳过判据变了 ⇒ 上面的标记可能失效"
    assert "overflow-x" in seg, "另一条跳过判据（内联 overflow-x）不见了"


@pytest.mark.unit
def test_sticky_rule_is_global_not_inside_media():
    """★★ 吸顶规则必须在全局作用域（深度 0）。

    【为什么钉深度】第一版就写进了 `@media (max-width:768px)` 块里 ——
      桌面端 `th` 的计算样式仍是 `position: static`，但源码看起来"明明写了"。
    """
    h = _html()
    assert "#q-ov-table thead th" in h, "技术面表头没有吸顶选择器"
    assert "#f-rk-table thead th" in h, "基本面表头没有吸顶选择器"
    d = _style_depth_at(h, "#q-ov-table thead th")
    assert d == 0, "吸顶规则在花括号深度 %d 处（>0 表示在 @media/其它块内，桌面端不生效）" % d


@pytest.mark.unit
def test_sticky_on_th_with_opaque_background():
    """sticky 必须在 th 上，且有实色背景 + 层级；否则数据会从表头底下透出来。"""
    h = _html()
    i = h.index("#q-ov-table thead th,")
    rule = h[i:h.index("}", i) + 1]
    assert "position: sticky" in rule, "没有 position: sticky"
    assert "top: 0" in rule, "没有 top:0（不会贴顶）"
    assert "background: var(--bg-card)" in rule, "★ 缺实色背景 ⇒ 滚动数据会透上来"
    assert "z-index:" in rule, "缺 z-index ⇒ 可能被表体盖住"


@pytest.mark.unit
def test_table_rows_are_not_sticky_anymore():
    """行级 sticky 已移除（border-collapse:collapse 下背景/边框绘制不可靠）。"""
    h = _html()
    assert '<tr style="position:sticky' not in h, "仍有 <tr> 级 sticky（会与 th 级规则打架）"
