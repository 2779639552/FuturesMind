"""「采集预算」与「子进程硬超时」的跨模块不变量 (2026-09-18)。

被锁定的真实缺陷
----------------
预算比子进程硬超时还长 ⇒ **必然被强杀**; 而一旦被强杀, 连 collect_run 报告与退出码
都写不出来, 前端/告警只剩一句 timeout —— 表现为"每轮 error、却查不到任何报告"。
这个坑烧过两轮, 两次都是"两个数字住在不同文件里, 改一个忘另一个":

  · 2026-09-17 网页链路: 采集器默认预算 12min > 网页子进程 timeout=600s
    → 修法: 网页链路显式传 `--max-minutes 9`(540s, 留 60s 收尾+落盘)。
  · 2026-09-18 定时链路: 预算 12min → 20min, 子进程超时同步 900s → 1500s。

所以这里用测试把关系锁死: 只改一边就会红。
"""

import re
from pathlib import Path

from tests.test_batch_collect_channel_health import bc

_ROOT = Path(__file__).resolve().parents[1]
_SCHEDULER = _ROOT / "scheduler.py"
_WEB_APP = _ROOT / "web_app.py"


def _collector_timeout_after(text: str, anchor: str, call_name: str = "_capture_run(") -> int:
    """取 anchor 之后**第一个 `<call_name>`** 的 timeout 值。

    【为什么要定位具体调用名而非直接搜 timeout=】函数文档/注释里也会写
    "subprocess timeout=..." 这类说明文字; 直接搜 `timeout=` 会命中注释,
    测到的就不是真实取值了。
    【调用名差异】2026-09-22 起调度器统一走 `_capture_run(...)`(内部才是
    subprocess.run, 调用点不再直接出现 subprocess.run); 而网页「一键更新」
    链路(web_app.py)仍直接用 `subprocess.run(..., timeout=600)`, 两者锚点不同。
    """
    i = text.index(anchor)
    j = text.index(call_name, i)
    m = re.search(r"timeout\s*=\s*(\d+)", text[j:])
    assert m, "在 %s 之后找不到 %s(timeout=...)" % (anchor, call_name)
    return int(m.group(1))


def test_scheduler_timeout_exceeds_collector_budget():
    """定时链路: 子进程超时必须大于采集器全局预算, 且留出收尾余量。"""
    budget_s = bc.MAX_RUN_MINUTES * 60
    timeout_s = _collector_timeout_after(
        _SCHEDULER.read_text(encoding="utf-8"), "def _run_platform_collection"
    )
    assert timeout_s > budget_s, (
        "采集器预算 %ds >= 调度器子进程超时 %ds ⇒ 必然被强杀、报告丢失"
        % (budget_s, timeout_s)
    )
    assert timeout_s - budget_s >= 120, (
        "余量不足: 收尾时那个已在跑的关键词最慢约 150s(抖音), 还要留报告落盘时间; 实际余量 %ds"
        % (timeout_s - budget_s)
    )


def test_web_oneclick_budget_fits_in_its_own_timeout():
    """网页「一键更新」链路: 它自己的预算(显式传参)必须装得进它自己的子进程超时。"""
    text = _WEB_APP.read_text(encoding="utf-8")
    anchor = "def _run_one_platform"
    i = text.index(anchor)
    m = re.search(r'"--max-minutes"\s*,\s*"(\d+)"', text[i:])
    budget_s = int(m.group(1)) * 60
    timeout_s = _collector_timeout_after(text, anchor, call_name="subprocess.run(")
    assert budget_s < timeout_s, (
        "网页链路预算 %ds >= 子进程超时 %ds ⇒ 必然被强杀(它不会走模块默认值, 故必须显式传参)"
        % (budget_s, timeout_s)
    )
    assert timeout_s - budget_s >= 30, "网页链路也要留收尾余量; 实际 %ds" % (timeout_s - budget_s)


def test_douyin_word_cap_fits_the_word_pool():
    """抖音每轮词数上限不得超过词池大小(否则档位在要求不存在的词)。

    2026-09-24 起抖音改**全量模式**(第三元素 None, 每次跑完整词池不轮换)——
    此档位天然满足"≤ 词池"; 若未来回退数字档位, 数字仍必须 ≤ 词池。
    """
    text = _SCHEDULER.read_text(encoding="utf-8")
    m = re.search(r'"douyin"\s*:\s*\(\s*\d+\s*,\s*"[a-z]+"\s*,\s*(\d+|None)\s*\)', text)
    assert m, "在调度器里找不到 douyin 档位定义"
    cap = m.group(1)
    pool = len(bc.DEFAULT_KEYWORDS_DOUYIN)
    if cap != "None":
        cap = int(cap)
        assert 0 < cap <= pool, "抖音每轮 %d 词 > 词池 %d 词" % (cap, pool)
    else:
        assert pool > 0, "全量模式要求词池非空"
