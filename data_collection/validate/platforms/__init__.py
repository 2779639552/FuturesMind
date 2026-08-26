"""
平台适配器注册表
===============

【模块角色】
本模块是整个"平台采集层"的入口/工厂:
把各平台适配器类集中登记到 ADAPTERS 注册表，
外部代码(BatchCollector)只需调 get_adapter(name) 即可拿到对应适配器实例，
无需关心具体 import 哪个类。

【支持的平台】
    xhs    — 小红书 (Spider_XHS API 签名引擎)
    weibo  — 微博 (m.weibo.cn 移动端 API, 纯 HTTP + Cookie)
    zhihu  — 知乎 (Playwright 浏览器, 自动算 x-zse-96 签名)
    xueqiu — 雪球 (Playwright 浏览器, 浏览器内 fetch 绕过 WAF)

【新增平台步骤】
1. 新建 xx_adapter.py，继承 base.PlatformAdapter 并实现 init/search/normalize；
2. 在本文件顶部 import，并把"平台名 → 适配器类"加入 ADAPTERS 注册表。
"""

from .base import PlatformAdapter  # 【调用包】基类契约(注册表类型标注用)
from .eastmoney_guba_adapter import EastmoneyGubaAdapter  # 【调用包】东方财富股吧适配器类(注册到 ADAPTERS)
from .weibo_adapter import WeiboAdapter  # 【调用包】微博适配器类(注册到 ADAPTERS)
from .xhs_adapter import XHSAdapter  # 【调用包】小红书适配器类(注册到 ADAPTERS)
from .xueqiu_adapter import XueqiuAdapter  # 【调用包】雪球适配器类(注册到 ADAPTERS)
from .zhihu_adapter import ZhihuAdapter  # 【调用包】知乎适配器类(注册到 ADAPTERS)

# 平台注册表: 平台标识 → 适配器类(注意值是"类"而非实例，由 get_adapter 创建实例)
ADAPTERS: dict[str, type[PlatformAdapter]] = {  # 【变量】平台标识→适配器类(值为类, get_adapter 负责实例化)
    "xhs": XHSAdapter,
    "weibo": WeiboAdapter,
    "zhihu": ZhihuAdapter,
    "xueqiu": XueqiuAdapter,
    "eastmoney_guba": EastmoneyGubaAdapter,
}

# 平台标识 → 展示名(用于日志/界面显示)
ADAPTER_DISPLAY_NAMES = {  # 【变量】平台标识→展示名(日志/界面显示用)
    "xhs": "小红书",
    "weibo": "微博",
    "zhihu": "知乎",
    "xueqiu": "雪球",
    "eastmoney_guba": "东方财富股吧",
}


def get_adapter(name: str) -> PlatformAdapter:
    """获取平台适配器实例。

    【功能】按平台名返回一个已初始化的适配器实例(工厂方法)。
    【参数】name: 平台名，大小写/空白不敏感 ("xhs" | "weibo" | "zhihu" | "xueqiu")
    【返回】PlatformAdapter 实例 (未调用 init，需采集前手动 init)
    【异常】ValueError: 未知平台名
    【关键逻辑】每次调用都新建实例(返回 ADAPTERS[name]() 新对象)，
    保证不同采集任务互不共享状态；用小写+去空白做归一化，容错输入。
    """
    name = name.lower().strip()
    if name not in ADAPTERS:
        available = ", ".join(ADAPTERS.keys())
        raise ValueError(f"Unknown platform '{name}'. Available: {available}")
    return ADAPTERS[name]()  # 【调用函数】实例化对应平台适配器(每次新建, 互不共享状态)


def list_platforms() -> list[str]:
    """列出所有已注册平台。

    【功能】返回当前支持的所有平台标识。
    【参数】无 【返回】平台标识字符串列表，如 ["xhs", "weibo", "zhihu", "xueqiu"]
    """
    return list(ADAPTERS.keys())
