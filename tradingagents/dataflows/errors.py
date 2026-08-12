"""Vendor data-error taxonomy.

A single hierarchy so the routing layer reacts by *behavior*, not by vendor:
every condition where a vendor cannot return usable data derives from
``VendorError``, and the router catches the base types. A new vendor raises
these (or a thin vendor-named subclass) and needs no new ``except`` clause.

    VendorError
    ├── NoMarketDataError          no usable rows (empty result OR stale data)
    ├── VendorRateLimitError       transient throttle -> skip to next vendor
    └── VendorNotConfiguredError   missing API key/config -> vendor unavailable

The number of types is the number of distinct router reactions, not the number
of human-describable causes: empty and stale data get identical handling, so
they share ``NoMarketDataError`` and differ only in the free-text ``detail``.
"""

from __future__ import annotations  # 【调用包】延迟求值类型注解(字符串形式注解,兼容运行时)


# 【功能】供应商错误基类:所有"供应商无法返回可用数据"的情形都继承它,
#        使路由层能按基类型统一捕获、按行为分流处理。
class VendorError(Exception):
    """Base for any condition where a vendor could not return usable data."""


# 【功能】"无可用数据"错误:供应商返回空结果或过期数据时抛出。
# 【参数】symbol: 用户请求的品种/标的代码;canonical: 实际查询时使用的规范化代码
#        (与 symbol 不同时用于提示);detail: 额外具体原因(如"最新行已是…已过期")。
# 【关键】空结果与过期数据共用此类型(处理行为一致),区别只体现在 free-text detail。
class NoMarketDataError(VendorError):
    """A vendor returned no usable rows for a symbol (empty result or stale data).

    Carries both the symbol the user requested and the canonical symbol the
    vendor was actually queried with, plus a free-text ``detail``, so callers
    can build a clear message instead of emitting a vendor-specific empty
    string into the data channel.
    """

    def __init__(self, symbol: str, canonical: str | None = None, detail: str = ""):
        self.symbol = symbol  # 【变量】用户原始请求的标的代码
        self.canonical = canonical or symbol  # 【变量】实际查询供应商时用的规范化代码
        self.detail = detail  # 【变量】额外原因说明(可为空字符串)
        msg = f"No market data for {symbol!r}"
        if canonical and canonical != symbol:
            msg += f" (queried as {canonical!r})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# 【功能】"供应商限流"错误:请求被限流时抛出,路由层跳过该供应商尝试下一个。
class VendorRateLimitError(VendorError):
    """A vendor throttled the request; the router skips to the next vendor."""


# 【功能】"供应商未配置"错误:选中的供应商缺少 API key/配置时抛出。
# 【关键】多重继承 ValueError,既让路由层按"供应商不可用"处理,
#        又兼容既有捕获 ValueError 的旧调用方。
class VendorNotConfiguredError(VendorError, ValueError):
    """A vendor was selected but its API key/configuration is missing.

    Also a ``ValueError`` so existing callers that catch ``ValueError`` keep
    working while the routing layer can treat it as "vendor unavailable".
    """
