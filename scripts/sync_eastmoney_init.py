"""把 eastmoney_guba 注册进项目内思路2 运行目录的 platforms/__init__.py（2026-08-26）。

web_app/scheduler 以 cwd=THINK2_DIR 跑采集，THINK2_DIR 现指向项目内思路2 validate
（project4/思路2/validate，resolve_think2_dir 优先探测该处）。运行目录的 platforms
注册表缺 eastmoney_guba → get_adapter('eastmoney_guba') 抛 ValueError → 前端更新页
勾选东财采集必失败。本脚本给注册表补齐 3 处：import / ADAPTERS / ADAPTER_DISPLAY_NAMES。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from path_utils import resolve_think2_dir  # noqa: E402

init = resolve_think2_dir() / "platforms" / "__init__.py"
txt = init.read_text(encoding="utf-8")

pairs = [
    # 1) import
    (
        "from .xueqiu_adapter import XueqiuAdapter\n",
        "from .xueqiu_adapter import XueqiuAdapter\n"
        "from .eastmoney_guba_adapter import EastmoneyGubaAdapter\n",
    ),
    # 2) ADAPTERS
    (
        '    "xueqiu": XueqiuAdapter,\n',
        '    "xueqiu": XueqiuAdapter,\n'
        '    "eastmoney_guba": EastmoneyGubaAdapter,\n',
    ),
    # 3) ADAPTER_DISPLAY_NAMES
    (
        '    "xueqiu": "雪球",\n',
        '    "xueqiu": "雪球",\n'
        '    "eastmoney_guba": "东财股吧",\n',
    ),
    # 4) docstring（注释，尽力匹配）
    (
        "    xueqiu    雪球 (xueqiu.com API, Cookie 认证)\n",
        "    xueqiu    雪球 (xueqiu.com API, Cookie 认证)\n"
        "    eastmoney_guba  东财股吧 (eastmoney JSONP, 免登录)\n",
    ),
]

hit = 0
for old, new in pairs:
    if old in txt:
        txt = txt.replace(old, new)
        hit += 1
        print(f"OK: {old.strip()[:50]}")
    else:
        print(f"MISS: {old.strip()[:50]}")

init.write_text(txt, encoding="utf-8")
print(f"写入完成, 命中 {hit}/{len(pairs)}")
