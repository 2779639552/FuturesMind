import getpass  # 【调用包】终端安全输入(用于 require_attention 时的回车确认)

import requests  # 【调用包】HTTP 请求,拉取远程公告接口
from rich.console import Console  # 【调用包】Rich 终端输出
from rich.panel import Panel  # 【调用包】Rich 面板组件,渲染公告框

from cli.config import CLI_CONFIG  # 【调用包】CLI 静态配置(公告地址/超时/兜底文本)


# 【功能】从远端 API 拉取公告;任何异常都静默回退到本地兜底文本,不阻塞 CLI 启动。
# 【参数】url:公告接口地址(默认取 CLI_CONFIG["announcements_url"]);timeout:超时秒数(默认 1.0)。
# 【返回】dict:{"announcements": 公告文本列表, "require_attention": 是否需要用户注意}。
# 【关键】网络/解析异常一律捕获并返回兜底数据,保证公告展示失败不影响主流程。
def fetch_announcements(url: str = None, timeout: float = None) -> dict:
    """Fetch announcements from endpoint. Returns dict with announcements and settings."""
    endpoint = url or CLI_CONFIG["announcements_url"]  # 【变量】实际请求的接口地址(支持调用方覆盖)
    timeout = timeout or CLI_CONFIG["announcements_timeout"]  # 【变量】请求超时(秒)
    fallback = CLI_CONFIG["announcements_fallback"]  # 【变量】接口失败/无公告时的兜底富文本

    try:
        response = requests.get(endpoint, timeout=timeout)  # 【调用函数】外部 API 请求拉取公告
        response.raise_for_status()
        data = response.json()
        return {
            "announcements": data.get("announcements", [fallback]),
            "require_attention": data.get("require_attention", False),
        }
    except Exception:
        return {
            "announcements": [fallback],
            "require_attention": False,
        }


# 【功能】在终端用 Rich Panel 展示公告;若 require_attention 为真,则阻塞等待用户回车确认。
# 【参数】console:Rich Console 实例;data:fetch_announcements 返回的 dict。
# 【返回】无
# 【关键】公告列表为空直接返回;require_attention 用 getpass 阻塞等待,确保重要公告被看到。
def display_announcements(console: Console, data: dict) -> None:
    """Display announcements panel. Prompts for Enter if require_attention is True."""
    announcements = data.get("announcements", [])
    require_attention = data.get("require_attention", False)  # 【变量】是否需要用户注意(阻塞等待回车)

    if not announcements:
        return

    content = "\n".join(announcements)  # 【变量】多行公告文本拼成单一内容

    panel = Panel(
        content,
        border_style="cyan",
        padding=(1, 2),
        title="Announcements",
    )
    console.print(panel)

    if require_attention:
        getpass.getpass("Press Enter to continue...")
    else:
        console.print()
