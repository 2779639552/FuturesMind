"""状态文件写入的「原子 + 可回滚」护栏（2026-10-23）。

【为什么不扫全仓】
试过用 AST 扫"函数内写 JSON 但没有 os.replace"，结果 34 处疑似里大量误判 ——
同一个函数里既写追加日志（不需要原子）又写状态文件（需要原子）时，函数级判据分不开。
护栏一旦靠猜，就会变成噪音；噪音护栏等于没有护栏。

【所以只守已知的跨进程状态写入点】
这些文件的共同特征（缺一不可）：
  · 跨进程/跨轮次读写（调度器写、采集器读，或反过来）
  · 程序启动时要读它决定行为
  · 损坏的后果是**静默**的（读到旧值或空值，然后另一个进程崩）

2026-10-22/23 公众号读书通道那两轮失败，就是 `mp_collect_result.json` 与
`cookies.json` 撞上了"写一半被读到"。

【本文件守两条】
  1. 这些写入点必须原子（走 `atomic_io` 或 tmp+os.replace）；
  2. 采集器的读取必须带 `.bak` 回滚路径 —— 光原子写救不了已经坏掉的文件。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# (模块文件, 写入函数名, 说明)
STATEFUL_WRITERS = [
    ("wechat_weread_collector.py", "_save_json", "采集结果/cookie/水位线"),
    ("oilchem_news_collect.py", "write_result", "隆众采集结果"),
    ("oilchem_news_collect.py", "save_seen", "隆众水位线"),
    ("warrant_collect.py", "write_output", "仓单采集结果"),
]


def _func_src(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


@pytest.mark.parametrize("filename,func,what", STATEFUL_WRITERS)
def test_state_writer_is_atomic(filename, func, what):
    seg = _func_src(ROOT / filename, func)
    assert seg, f"{filename}:{func} 不存在（改名了？护栏要跟着改）"
    assert ("atomic_io" in seg) or ("os.replace" in seg), (
        f"{filename}:{func}（{what}）不是原子写 —— 并发读会拿到 0 字节文件，"
        "对方 json.loads 抛 JSONDecodeError，然后**那个进程**崩（写方不报错）"
    )


def test_weread_reader_can_rollback_from_bak():
    """原子写只防未来；历史遗留的半截文件要靠 .bak 回滚救回来。"""
    seg = _func_src(ROOT / "wechat_weread_collector.py", "_read_json_tolerant")
    assert "atomic_io" in seg and "load_json" in seg, \
        "读取没有 .bak 回滚路径 —— 文件已损坏时只能人工删，不能自愈"


def test_atomic_io_keeps_previous_version():
    """`.bak` 必须是上一版（先备份再写新），否则回滚等于回到坏版本。"""
    from tradingagents import atomic_io as A

    src = inspect.getsource(A.save_json)
    assert src.index("bak" ) < src.index("_atomic_write(p, data)"), \
        "备份发生在新写入之后 —— .bak 会存成新内容，回滚失去意义"


def test_atomic_io_is_the_shared_implementation():
    """采集器不该再自己实现一份原子写（分叉后会各自长歪）。"""
    seg = _func_src(ROOT / "wechat_weread_collector.py", "_save_json")
    assert "atomic_io" in seg, "采集器又自己写了一套，应该复用 tradingagents.atomic_io"
