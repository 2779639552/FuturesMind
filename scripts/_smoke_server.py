"""临时冒烟用启动器: 在 5055 端口起新代码实例(2026-08-26)。

用户运行中的 5000 端口实例跑的是旧代码(无 /api/analysis/graph 等新路由),
不能重启它。本启动器用**新代码**在 127.0.0.1:5055 起一个临时实例,供
scripts/smoke_graph_sankey.py 的 Playwright 验证前端图谱渲染。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import web_app  # noqa: E402
from waitress import serve  # noqa: E402

serve(web_app.app, host="127.0.0.1", port=5055, threads=4)
