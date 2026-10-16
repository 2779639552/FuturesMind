# -*- coding: utf-8 -*-
r"""外文研报 · LLM 输出 JSON 的容错解析 —— 回归护栏（2026-10-14）。

【要防的真实事故】
任务 2c5e12096595（OPR 342K 字符）报：
    第 2/5 块（原文第 [16..28] 页）笔记失败：
    [分块笔记 2/5] 归纳失败：JSONDecodeError: Expecting ',' delimiter:
    line 241 column 101 (char 17154)
⇒ 5 块分块笔记里整整 1 块作废，压缩稿少覆盖 13 页原文。
根因是 LLM 输出的 JSON **漏写了逗号**，而当时 `_json_from` 的三级兜底
（直解 → 截大括号 → 去尾随逗号）都只是"切掉多余部分"，对**结构内的语法错误**无能为力。

【本文件守两件事】
  A. 该修的必须修好：漏逗号（对象/数组/各类值之后）、尾随逗号、字符串内裸换行；
  B. ★ 不该动的绝不能动：正常 JSON 逐字段不变；字符串**内容里**的冒号/逗号/花括号/
     转义引号一律不许被改写 —— 这类"修错"比不修更危险（会把好数据改坏）。
     反向用例（B 组）比正向用例更重要：容错解析器最常见的失败模式就是误伤。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import foreign_report as fr  # noqa: E402


def _parse(src: str) -> dict:
    return fr._json_from(src)


# ════════════════════════════════════════════════════════════
# A. 必须修好
# ════════════════════════════════════════════════════════════
class TestRepairsBrokenJson:
    @pytest.mark.parametrize("name,src,want", [
        ("对象成员之间漏逗号", '{"a": 1\n "b": 2}', {"a": 1, "b": 2}),
        ("数组元素之间漏逗号", '{"xs": [1\n 2\n 3]}', {"xs": [1, 2, 3]}),
        ("字符串值后漏逗号", '{"a": "x"\n "b": "y"}', {"a": "x", "b": "y"}),
        ("对象值后漏逗号", '{"a": {"k": 1}\n "b": 2}', {"a": {"k": 1}, "b": 2}),
        ("数组值后漏逗号", '{"a": [1]\n "b": 2}', {"a": [1], "b": 2}),
        ("true 后漏逗号", '{"a": true\n "b": 1}', {"a": True, "b": 1}),
        ("null 后漏逗号", '{"a": null\n "b": 1}', {"a": None, "b": 1}),
        ("数字后漏逗号（跨行）", '{"n": 12\n "m": 34}', {"n": 12, "m": 34}),
        ("嵌套数组里的对象漏逗号",
         '{"f": [{"t": "x"}\n {"t": "y"}], "n": 1}',
         {"f": [{"t": "x"}, {"t": "y"}], "n": 1}),
        ("外层对象漏逗号 + 内层数组",
         '{"a": [1, 2]\n "b": {"c": [3]}}',
         {"a": [1, 2], "b": {"c": [3]}}),
    ])
    def test_missing_comma_repaired(self, name, src, want):
        assert _parse(src) == want, f"{name} 未被修复"

    def test_trailing_comma_repaired(self):
        assert _parse('{"a": [1, 2,],}') == {"a": [1, 2]}

    def test_raw_newline_in_string_escaped(self):
        """JSON 规范不允许字符串里出现裸换行 —— 必须转义而不是丢弃或截断。"""
        got = _parse('{"a": "第一行\n第二行"}')
        assert got["a"] == "第一行\n第二行"

    def test_raw_tab_in_string_escaped(self):
        got = _parse('{"a": "x\ty"}')
        assert got["a"] == "x\ty"

    def test_code_fence_stripped(self):
        assert _parse('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_around_json_tolerated(self):
        assert _parse('好的，结果如下：\n{"a": 1}\n以上。') == {"a": 1}

    def test_repair_helper_is_idempotent_on_valid_json(self):
        """修复函数对**已经合法**的 JSON 必须零改动（幂等）。"""
        for src in ('{"a": 1, "b": [1, 2]}', '{"s": "x\\ny"}', '{"n": -1.5e3}'):
            assert fr._repair_json_text(src) == src, f"合法 JSON 被改写：{src}"


# ════════════════════════════════════════════════════════════
# B. ★ 不该动的绝不能动（容错解析器最容易翻车的地方）
# ════════════════════════════════════════════════════════════
class TestDoesNotCorruptValidJson:
    @pytest.mark.parametrize("name,src,want", [
        ("普通对象", '{"a": 1, "b": [1, 2]}', {"a": 1, "b": [1, 2]}),
        ("值里的冒号（时间/URL）",
         '{"t": "10:30", "u": "http://x/y"}', {"t": "10:30", "u": "http://x/y"}),
        ("值里的逗号", '{"s": "a, b, c"}', {"s": "a, b, c"}),
        ("值里的花括号字面量", '{"s": "{pseudo: 1}"}', {"s": "{pseudo: 1}"}),
        ("值里的方括号字面量", '{"s": "[not a list]"}', {"s": "[not a list]"}),
        ("负数与科学计数", '{"a": -1.5e3, "b": -0.2}', {"a": -1500.0, "b": -0.2}),
        ("空对象/空数组", '{"a": {}, "b": []}', {"a": {}, "b": []}),
        ("中文键值", '{"结论": "看多", "页码": [3, 4]}', {"结论": "看多", "页码": [3, 4]}),
    ])
    def test_valid_json_unchanged(self, name, src, want):
        assert _parse(src) == want, f"{name} 被误改"

    def test_escaped_quote_inside_string(self):
        """字符串内的转义引号不能被当成定界符（否则后续结构全乱）。"""
        got = _parse(r'{"s": "he said \"hi\"", "n": 1}')
        assert got == {"s": 'he said "hi"', "n": 1}

    def test_colon_inside_string_does_not_trigger_comma(self):
        """★ key 与 value 的分界靠 `:` 辨认；值里的冒号不许诱发补逗号。"""
        got = _parse('{"a": "x:y", "b": 2}')
        assert got == {"a": "x:y", "b": 2}

    def test_nested_valid_structure_intact(self):
        src = '{"meta": {"title": "a", "pages": [1, 2]}, "findings": [{"t": "p"}]}'
        assert _parse(src) == {
            "meta": {"title": "a", "pages": [1, 2]},
            "findings": [{"t": "p"}],
        }


# ════════════════════════════════════════════════════════════
# C. 边界：不可修复时应明确报错，而不是返回半个对象
# ════════════════════════════════════════════════════════════
class TestUnrepairableRaises:
    def test_empty_response(self):
        with pytest.raises(ValueError):
            _parse("")

    def test_no_json_object_at_all(self):
        with pytest.raises(ValueError):
            _parse("这次模型只回了一句话，没有 JSON。")

    def test_truncated_json_still_raises(self):
        """被截断（无闭合括号）的 JSON 不该被"修"成一个残缺对象 —— 宁可报错重试。"""
        with pytest.raises(Exception):
            _parse('{"a": "未闭合的字符串')

    def test_returns_dict_only(self):
        """顶层是数组时必须抛错（调用方按 dict 取字段，数组会引发更深的错）。"""
        with pytest.raises(ValueError):
            _parse("[1, 2, 3]")


# ════════════════════════════════════════════════════════════
# D. 与真实事故对齐：分块笔记这种"长且多字段"的 JSON 漏一个逗号必须能救回来
# ════════════════════════════════════════════════════════════
class TestRealIncidentShape:
    def test_long_note_missing_one_comma_is_recovered(self):
        """仿事故形态：长笔记里夹了**一处**漏逗号，整体仍必须能解析出来。

        做法：先用 `,\\n` 把 39 条拼成合法 JSON，再**只删掉第 20、21 条之间的那个逗号**
        —— 与真实事故同形（长 JSON、只有一处语法错、错误位置在中段）。
        """
        rows = [f'{{"t": "结论{i}", "p": {i}, "v": "x:y,z"}}' for i in range(1, 40)]
        good = '{"findings": [' + ",\n".join(rows) + '], "numbers": [{"k": "A", "v": 1}]}'
        assert len(_parse(good)["findings"]) == 39, "良性样本本身就不对，后面的断言无意义"

        broken = good.replace(
            '{"t": "结论20", "p": 20, "v": "x:y,z"},\n{"t": "结论21"',
            '{"t": "结论20", "p": 20, "v": "x:y,z"}\n{"t": "结论21"',
        )
        assert broken != good, "没成功制造出漏逗号样本"
        import json as _j
        with pytest.raises(Exception):
            _j.loads(broken)      # 标准解析器确实解不了 ⇒ 样本有效

        got = _parse(broken)
        assert len(got["findings"]) == 39, "修复后条数不对 ⇒ 有数据被吞"
        assert got["findings"][19]["t"] == "结论20"
        assert got["findings"][20]["t"] == "结论21"
        assert got["findings"][19]["v"] == "x:y,z", "值里的冒号/逗号被改坏了"
        assert got["numbers"] == [{"k": "A", "v": 1}]
