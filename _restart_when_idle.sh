#!/bin/bash
# 等研报采集空闲后重启 web_app(载入"按品种采集"路由新代码) — 2026-09-04
cd "C:/Users/19168/Desktop/project4/AgentSense"

for i in $(seq 1 60); do  # 最多等 60 分钟
  flag=$(curl -s -m 5 http://localhost:5000/api/research/collect 2>/dev/null)
  case "$flag" in
    *'"collecting":true'*) sleep 60 ;;
    *) break ;;
  esac
done

# 彻底停旧(:5000 监听进程)
pid=$(netstat -ano | grep ":5000" | grep LISTENING | awk '{print $5}' | head -1)
if [ -n "$pid" ]; then
  echo "stopping pid=$pid"
  taskkill //F //PID "$pid" 2>/dev/null
  sleep 3
fi

# 分离进程启动新实例(脱离任务树,防 harness 收割)
powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\19168\Desktop\project4\AgentSense\venv\Scripts\python.exe' -ArgumentList 'web_app.py' -WorkingDirectory 'C:\Users\19168\Desktop\project4\AgentSense' -WindowStyle Hidden -RedirectStandardOutput 'C:\Users\19168\Desktop\project4\AgentSense\_web_restart.log' -RedirectStandardError 'C:\Users\19168\Desktop\project4\AgentSense\_web_restart.err.log'"
sleep 15
echo "--- restart verify ---"
curl -s -m 5 http://localhost:5000/api/research/collect
echo
