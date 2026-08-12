"""CLI 静态配置:发布公告(announcements)相关参数,供 cli.announcements 拉取与展示。"""

CLI_CONFIG = {
    # Announcements
    "announcements_url": "https://api.tauric.ai/v1/announcements",  # 【变量】公告接口地址(Tauric Research API)
    "announcements_timeout": 1.0,  # 【变量】请求公告接口的超时时间(秒)
    "announcements_fallback": "[cyan]For more information, please visit[/cyan] [link=https://github.com/TauricResearch]https://github.com/TauricResearch[/link]",  # 【变量】接口失败/无数据时的兜底富文本提示
}
