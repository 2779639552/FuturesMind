@echo off
rem AgentSense web_app 一键启动(独立 cmd 窗口,不受 Claude 会话后台内存清理影响)
cd /d C:\Users\19168\Desktop\project4\AgentSense
venv\Scripts\python.exe web_app.py
pause
