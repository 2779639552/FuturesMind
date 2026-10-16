"""在 5050 端口起一个**临时验证服务**,用真实 HTTP 打一遍国君数据模块的两个新接口。

【为什么需要它】
生产 :5000 的进程是 09-29 18:24 启动的,早于本次改动,因此直接打线上接口只会 404。
正常做法是重启服务,但此刻有研报子进程
(`research_collector_dongzheng.py --dynamics`,PID 24360)在跑 —— 重启会让它变孤儿,
看门狗再拉起新实例后收尾逻辑可能重复处理,也就是"LLM 双花"。所以**不动生产进程**,
另起一个独立进程(本脚本)在 5050 端口做等价验证。

【为什么不复用 __main__】
`import web_app` 时 `__name__ == "web_app"`,所以 web_app 里的启动块(端口 5000
启动保护、调度器 start_scheduler、卡死研报清扫)**都不会执行** —— 正合需要:
只要 Flask app 对象和它的路由,不要任何副作用。

用法:python probe/_gtja_serve.py
然后 curl http://127.0.0.1:5050/api/gtja/overview?variety=BU
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")  # 免登录,便于本地验证

from web_app import app  # noqa: E402  【导入】只要 Flask app,不触发 __main__ 副作用

if __name__ == "__main__":
    from waitress import serve

    print("probe server on 127.0.0.1:5050 (separate process, production :5000 untouched)")
    serve(app, host="127.0.0.1", port=5050, threads=4, channel_timeout=120)
