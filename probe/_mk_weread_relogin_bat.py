# -*- coding: utf-8 -*-
"""生成桌面「一键重登公众号(读书通道)」bat —— 照抄既有 `relogin_weibo_xhs.bat` 的形态。

【为什么需要它】
  读书通道用的是**借来的网页会话**（wr_skey），会超时失效，必须人工扫码。
  而你桌面上已有同款先例（`relogin_weibo_xhs.bat` 一键重登微博/小红书）⇒ 沿用同一形态：
    · 先拉回屏幕外窗口（Chrome 窗口可能跑到屏幕外）
    · 分步提示 + 每步 errorlevel 告警
    · 结尾"扫完告诉我一声，我来验证"

【编码】与先例一致用 **GBK**（cmd 默认代码页是 936；写 UTF-8 中文会乱码）。
【可测】支持参数透传（`%*`）⇒ 可用 `--check-only` 在**不弹二维码**的情况下验证接线。
"""
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
OUT = DESKTOP / "重登公众号读书通道.bat"

LINES = [
    "@echo off",
    "title 重登公众号(读书通道)",
    "rem ============================================================================",
    "rem  微信读书通道：网页登录态(wr_skey)会超时，必须人工扫码一次。",
    "rem  状态目录: %USERPROFILE%\\.tradingagents\\weread\\cookies.json",
    "rem  支持参数透传，例如：",
    "rem     重登公众号读书通道.bat --check-only    (只验证已存登录态，不弹二维码)",
    "rem ============================================================================",
    "setlocal",
    "set APPDIR=D:\\AgentSense\\AgentSense",
    "set NODE=C:\\Program Files\\nodejs\\node.exe",
    'set SCRIPT=%APPDIR%\\probe\\_weread_login.mjs',
    "",
    'if not exist "%NODE%"   ( echo [ERROR] node not found: %NODE%   & pause & exit /b 1 )',
    'if not exist "%SCRIPT%" ( echo [ERROR] script not found: %SCRIPT% & pause & exit /b 1 )',
    "",
    "echo ============================================================",
    "echo  0/2 先把屏幕外的窗口拉回来(含本窗口)...",
    "echo ============================================================",
    # 【2026-10-23】桌面整理后 ps1 已移入 _归档_20261023\02_脚本\。
    # 两处都探：老位置在则用老的，否则用归档位置 —— 整理前后都能正常工作。
    'if exist "%USERPROFILE%\\Desktop\\_fix_offscreen_windows.ps1" '
    'powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\\Desktop\\_fix_offscreen_windows.ps1"',
    'if not exist "%USERPROFILE%\\Desktop\\_fix_offscreen_windows.ps1" if exist '
    '"%USERPROFILE%\\Desktop\\_归档_20261023\\02_脚本\\_fix_offscreen_windows.ps1" '
    'powershell -NoProfile -ExecutionPolicy Bypass -File '
    '"%USERPROFILE%\\Desktop\\_归档_20261023\\02_脚本\\_fix_offscreen_windows.ps1"',
    "echo.",
    "echo ============================================================",
    "echo  1/2 扫码登录（二维码会打开在 Chrome 窗口里）",
    "echo      用微信 App 扫它。二维码会过期并自动刷新，不要关窗口。",
    "echo      默认最多等 180 秒；想多等一会儿：本 bat 后面加 --wait 300",
    "echo ============================================================",
    'cd /d "%APPDIR%"',
    '"%NODE%" "%SCRIPT%" %*',
    "if errorlevel 1 echo [WARN] 扫码这步失败或超时，请看上方输出。",
    "echo.",
    "echo ============================================================",
    "echo  2/2 登录后自检（脚本内部会立刻用新 cookie 试拉文章清单）",
    "echo      看到 4 个号都 HTTP 200 且有数据 = 成功。",
    "echo ============================================================",
    "echo.",
    "echo  完成。告诉我一声，我会：",
    "echo    1) 立刻跑一次采集(不占 LLM)把新文落进 inbox",
    "echo    2) 顺带测一下这次登录态到底能活多久(判它能不能撑过定时轮)",
    "pause",
    "",
]

OUT.write_bytes(("\r\n".join(LINES)).encode("gbk"))
b = OUT.read_bytes()
print("  已写: %s" % OUT)
print("  尺寸: %d 字节   编码: GBK   换行: CRLF=%d" % (len(b), b.count(b"\r\n")))
# 回读校验（用 gbk 解）
back = b.decode("gbk")
print("  回读校验（前 3 行）:")
for l in back.split("\r\n")[:3]:
    print("    %s" % l)
