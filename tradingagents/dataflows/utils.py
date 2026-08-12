import re  # 【调用包】正则校验路径安全的 ticker 字符
from datetime import date, datetime, timedelta  # 【调用包】日期处理
from typing import Annotated  # 【调用包】类型标注(带说明的 str 别名)

import pandas as pd  # 【调用包】DataFrame 保存

# 【变量】保存路径类型: None 表示不落盘
SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]

# Tickers can contain letters, digits, dot, dash, underscore, caret
# (index symbols like ^GSPC), equals (futures like GC=F), and plus
# (forex/CFD symbols like XAUUSD+). None of these enable directory
# traversal, so the value never escapes a containing directory when
# interpolated into a path. Anything else is rejected.
# 【变量】合法 ticker 字符正则(字母/数字/._-^=+), 且不含目录穿越字符
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^=+]+$")


# 【功能】校验 ``value`` 可安全拼进文件系统路径。
# 【参数】value: ticker 字符串; max_len: 最大长度(默认 32)。
# 【返回】通过校验时原样返回; 否则抛 ValueError。
# 【异常】ValueError: 空值/超长/含非法字符/纯点值。
# 【关键】ticker 来自用户 CLI 或 LLM 工具调用, 均可能被注入恶意内容(如新闻里的提示
#         注入); 不校验时 ``"../../../etc/foo"`` 会经 os.path.join 逃出配置的缓存/检查点/结果目录。
def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    Tickers come from user CLI input or from LLM tool calls, both of which
    can be influenced by attacker-controlled content (e.g. prompt injection
    embedded in fetched news). Without validation, a value like
    ``"../../../etc/foo"`` flows into ``os.path.join`` / ``Path /`` and
    escapes the configured cache, checkpoint, or results directory.

    Returns ``value`` unchanged when it matches the allowed pattern; raises
    ``ValueError`` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_PATH_RE.fullmatch(value):
        raise ValueError(f"ticker contains characters not allowed in a filesystem path: {value!r}")
    # The regex above allows '.', so values like '.', '..', '...' would pass,
    # and as a path component they traverse the parent directory. Reject any
    # value that's only dots.
    if set(value) == {"."}:  # 【变量】纯点值会指向父目录, 拒绝
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    return value


# 【功能】把 DataFrame 存为 CSV(仅当给了 save_path)。
# 【参数】data: 数据帧; tag: 输出标识; save_path: 落盘路径(None 不保存)。
def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    if save_path:
        data.to_csv(save_path, encoding="utf-8")  # 【调用函数】写 CSV
        print(f"{tag} saved to {save_path}")


# 【功能】返回今天日期字符串(yyyy-mm-dd)。
def get_current_date():
    return date.today().strftime("%Y-%m-%d")  # 【调用函数】今天日期格式化


# 【功能】构造一个类装饰器: 对类里所有可调用属性应用给定装饰器。
# 【参数】decorator: 要应用到每个方法的装饰器。
# 【返回】类装饰器函数。
def decorate_all_methods(decorator):
    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():  # 【变量】遍历类属性
            if callable(attr_value):
                setattr(cls, attr_name, decorator(attr_value))  # 【调用函数】逐个方法套装饰器
        return cls

    return class_decorator


# 【功能】把给定日期推到下一个工作日(跳过周末)。
# 【参数】date: datetime 或 yyyy-mm-dd 字符串。
# 【返回】下一个工作日的 datetime。
def get_next_weekday(date):

    if not isinstance(date, datetime):
        date = datetime.strptime(date, "%Y-%m-%d")  # 【调用函数】字符串转 datetime

    if date.weekday() >= 5:  # 【变量】周六(5)/周日(6)
        days_to_add = 7 - date.weekday()  # 【变量】加到下周一的偏移
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        return date
