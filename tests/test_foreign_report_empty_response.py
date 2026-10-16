# -*- coding: utf-8 -*-
"""回归：外文研报"空响应"缺陷 —— content 为空但 reasoning_content 有内容。

【缺陷背景】2026-09-29 任务 71694a2ef38a 在「封面与核心结论」路失败：
`ValueError: 响应为空`，3 次重试全空。真因是思维模型把内容放进 reasoning_content，
而 `_ask_json` 只读 `.content`，把已捕获的 reasoning_content 白扔掉。

本测试的判据（必须能穿透"只是改了措辞"这类假修复）：
1. 只给 reasoning_content（content 空）时，_ask_json 必须返回解析成功的 dict；
2. 两者都空时，仍必须抛 RuntimeError（不能因为加了兜底就把真正的空响应当成功）；
3. 变异验证：把 _msg_text 的 reasoning_content 兜底分支去掉，测试 1 必须变红。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import foreign_report as fr


class _Msg:
    """最小 AIMessage 替身：只有 content 与 additional_kwargs。"""

    def __init__(self, content="", reasoning=None):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}


class _LLM:
    def __init__(self, resp):
        self._resp = resp
        self.calls = 0

    def invoke(self, prompt, config=None, **kw):
        self.calls += 1
        return self._resp


GOOD_JSON = '{"cover": {"title_zh": "LPG 机密"}, "summary": {"cards": []}}'


def test_reasoning_content_fallback_yields_json():
    """content 空、reasoning_content 有 JSON → 必须成功解析（缺陷 1 的修复）。"""
    llm = _LLM(_Msg(content="", reasoning=GOOD_JSON))
    out = fr._ask_json(llm, "prompt", "封面与核心结论")
    assert isinstance(out, dict)
    assert out["cover"]["title_zh"] == "LPG 机密"
    assert llm.calls == 1, "有兜底时应一次成功，不该白重试"


def test_reasoning_content_with_think_tags():
    """思维链混 <think> 标记时，剥标记后仍要能解析。"""
    llm = _LLM(_Msg(content="   ", reasoning="<think>想想</think>" + GOOD_JSON))
    out = fr._ask_json(llm, "prompt", "封面与核心结论")
    assert out["cover"]["title_zh"] == "LPG 机密"


def test_both_empty_still_fails():
    """两者都空 → 仍必须失败（兜底不能把真空响应当成功）。"""
    llm = _LLM(_Msg(content="", reasoning=""))
    try:
        fr._ask_json(llm, "prompt", "封面与核心结论", retries=0)
    except RuntimeError as e:
        assert "响应为空" in str(e), str(e)
    else:
        raise AssertionError("空响应必须抛 RuntimeError")


def test_content_list_joined():
    """content 为分块 list 时应拼接。"""
    llm = _LLM(_Msg(content=['{"a": ', "1}"]))
    assert fr._ask_json(llm, "prompt", "x") == {"a": 1}


def test_ask_text_also_falls_back():
    """翻译路径（_ask_text）同样要吃 reasoning_content 兜底。"""
    llm = _LLM(_Msg(content="", reasoning="这是译文正文。"))
    assert fr._ask_text(llm, "prompt", "翻译") == "这是译文正文。"


def test_msg_text_prefers_content():
    """两者都有时应优先用 content（别把正式输出换成思维链）。"""
    r = _Msg(content="正式内容", reasoning="思维链")
    assert fr._msg_text(r) == "正式内容"
