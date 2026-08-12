from copy import deepcopy  # 【调用包】深拷贝配置字典,避免外部修改污染全局配置

import tradingagents.default_config as default_config  # 【调用包】项目默认配置(DEFAULT_CONFIG 初始值来源)

# Use default config but allow it to be overridden
_config: dict | None = None  # 【变量】模块级全局配置缓存(None=尚未初始化;get_config 返回其深拷贝)


# 【功能】初始化全局配置:把默认配置深拷贝一份到 _config(幂等,仅首次生效)。
# 【关键】_config 为 None 时才执行,避免重复初始化覆盖已有自定义配置。
def initialize_config():
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


# 【功能】用自定义配置覆盖全局配置(只更新传入的键,不整体替换)。
# 【参数】config: 用户传入的覆盖配置字典。
# 【返回】无。
# 【关键】字典型键做一层浅合并(如 data_vendors 保留未覆盖的子键),标量键直接替换;
#        先确保已初始化,再对传入配置深拷贝防止别名污染。
def set_config(config: dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    global _config
    initialize_config()
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(_config.get(key), dict):
            _config[key].update(value)
        else:
            _config[key] = value


# 【功能】返回当前配置的深拷贝,供各模块只读使用。
# 【返回】dict:全局配置的快照副本(修改它不会影响全局)。
def get_config() -> dict:
    """Get the current configuration."""
    if _config is None:
        initialize_config()
    return deepcopy(_config)


# Initialize with default config
initialize_config()  # 【调用函数】模块加载时即初始化默认配置(保证 _config 可用)
