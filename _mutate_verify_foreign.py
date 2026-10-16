# -*- coding: utf-8 -*-
"""变异验证：摘掉 _msg_text 的 reasoning_content 兜底分支后，回归断言必须变红。

【为什么不直接改真文件】避免"变异脚本污染源文件却自查已恢复"的老坑：
本脚本在**内存里**对源码做变异（exec 成独立模块），真文件全程只读。
"""
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "foreign_report.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

src = SRC.read_text(encoding="utf-8")

# 变异：删掉 _msg_text 里 "if reas: return ..." 这两行（兜底分支）
mutated, n = re.subn(
    r'\n    reas = \(getattr\(r, "additional_kwargs", None\) or \{\}\)\.get\("reasoning_content"\)\n'
    r'    if reas:\n        return re\.sub\(r"</\?think>", "", str\(reas\)\)\.strip\(\)\n',
    "\n",
    src,
)
assert n == 1, f"变异未命中（n={n}）——源码结构可能已变，请更新变异模式"
assert "reasoning_content" in src and mutated != src, "变异未生效"

mod = types.ModuleType("foreign_report_mutated")
mod.__file__ = str(SRC)
exec(compile(mutated, str(SRC) + "<mutated>", "exec"), mod.__dict__)


class _Msg:
    def __init__(self, content="", reasoning=None):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}


class _LLM:
    def invoke(self, prompt, config=None, **kw):
        return _Msg(content="", reasoning='{"a": 1}')


print("=== 变异体（已摘掉兜底）===")
print("  _msg_text(reasoning-only) =", repr(mod._msg_text(_Msg(reasoning="内容"))) )
try:
    out = mod._ask_json(_LLM(), "p", "封面与核心结论", retries=0)
    print("  _ask_json 返回:", out, "  ❌ 变异体竟然成功了 —— 说明断言没咬住修复")
    sys.exit(1)
except RuntimeError as e:
    print("  _ask_json 抛错:", str(e)[:70])
    print("  ✅ 变异体如期失败 ⇒ 回归断言确实依赖该兜底分支（变异验证通过）")
