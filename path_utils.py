"""Shared filesystem path resolution for FuturesMind.

Centralizes the "where is the 思路2 validate data?" question so every module
(web_app, signal_analyzer, price_fetcher, scheduler) resolves it the same way:

  1. ``$THINK2_DIR`` wins when set (explicit override).
  2. Otherwise the first existing local candidate (real user data).
  3. Otherwise the bundled repo sample (``data/think2_validate``) so a fresh
     clone can still render without the local 思路2 project.
"""

# =============================================================================
# 【模块角色】
#   path_utils.py 是项目的"路径集中管理"模块,回答一个贯穿全局的问题:
#   "思路2 的验证数据到底放在哪里?" 所有需要读取该数据的模块
#   (web_app、signal_analyzer、price_fetcher、scheduler 等)都统一调用
#   resolve_think2_dir() 获取目录,而不是各自写死一个路径。
#
# 【为什么要把路径集中管理?(工程实践)】
#   早期代码里硬编码了各种绝对路径(如 C:/Users/xxx/Desktop/思路2/validate),
#   带来三个问题:
#     1. 换一台电脑 / 换一个用户名就无法运行,需要到处改路径;
#     2. 同一个"数据目录"在多个文件里被写多次,改一处漏一处,容易不一致;
#     3. 新克隆的仓库没有本地数据时,程序会因找不到目录而报错。
#   本模块用一个候选路径元组(_THINK2_CANDIDATES)按优先级依次探测,并支持
#   环境变量 THINK2_DIR 显式覆盖,把"找数据目录"这件事收敛到唯一入口。
#
# 【查找优先级】
#     1. 环境变量 $THINK2_DIR 存在 → 直接使用(显式覆盖);
#     2. 依次检查候选路径中"真实存在"的目录(用户本地思路2 数据);
#     3. 全部不存在时,退回候选列表第一项,并依赖仓库内置样例
#        data/think2_validate,保证全新克隆也能渲染页面。
# =============================================================================

import os
from pathlib import Path

# 候选路径元组:按优先级从上到下探测,存在即用。
# 前 3 个指向用户本地的思路2 项目(不同命名/位置的兼容写法);
# 最后 1 个是仓库自带的样例数据目录 data/think2_validate,兜底使用。
_THINK2_CANDIDATES = (
    Path(os.path.expanduser("~/Desktop/思路2/validate")),
    Path(os.path.expanduser("~/Desktop/silu2/validate")),
    Path(os.path.expanduser("~/projects/silu2/validate")),
    Path(__file__).parent / "data" / "think2_validate",  # bundled sample
)


def resolve_think2_dir() -> Path:
    """定位思路2 验证数据的目录。

    【功能】按优先级返回思路2 验证数据所在目录的 Path 对象。
    【参数】无。
    【返回】Path: 解析出的目录路径。
    【关键逻辑】
            - 先看环境变量 THINK2_DIR,设置了就直接用它(显式覆盖,最高优先)。
            - 否则遍历 _THINK2_CANDIDATES,返回第一个 exists() 为真的目录。
            - 全都不存在时返回第一个候选路径(即仓库内置样例位置),
              保证调用方总能拿到一个"可用的"路径而不抛异常。
    """
    env = os.environ.get("THINK2_DIR", "").strip()
    if env:
        return Path(env)
    for candidate in _THINK2_CANDIDATES:
        if candidate.exists():
            return candidate
    return _THINK2_CANDIDATES[0]
