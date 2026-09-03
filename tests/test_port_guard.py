"""启动保护(2026-09-03):解析 netstat -ano 找出占用端口的监听进程。

纯逻辑单测,不联网、不调 LLM、不起服务器。覆盖 `_parse_netstat_listeners`:
多监听者、IPv4/IPv6 同 PID 去重、忽略其它端口与连接态、脏行/非数字 PID 不崩。
"""

import pytest

import web_app

# 一段典型的 netstat -ano 输出(中文系统表头无关紧要,解析只看数据行)。
NETSTAT_SAMPLE = """\
netstat -ano

Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       258248
  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       260916
  TCP    [::]:5000              [::]:0                 LISTENING       260916
  TCP    0.0.0.0:5001           0.0.0.0:0              LISTENING       111
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1234
  TCP    192.168.1.5:54321      10.0.0.1:443           ESTABLISHED     999
  UDP    0.0.0.0:5000           *:*                                    258248
"""


@pytest.mark.unit
class TestParseNetstatListeners:
    def test_finds_all_listeners_on_port_and_dedups(self):
        # IPv4 + IPv6 同一进程(PID 260916)只算一次;258248 仅 IPv4。
        assert web_app._parse_netstat_listeners(NETSTAT_SAMPLE, 5000) == [258248, 260916]

    def test_ignores_other_ports(self):
        assert web_app._parse_netstat_listeners(NETSTAT_SAMPLE, 5001) == [111]
        assert web_app._parse_netstat_listeners(NETSTAT_SAMPLE, 135) == [1234]

    def test_empty_when_port_unused(self):
        assert web_app._parse_netstat_listeners(NETSTAT_SAMPLE, 9999) == []

    def test_malformed_lines_are_skipped(self):
        messy = "\n".join(
            [
                "  TCP    0.0.0.0:5000   0.0.0.0:0   LISTENING   not-a-pid",
                "garbage line without columns",
                "  TCP    0.0.0.0:5000   0.0.0.0:0   LISTENING",  # 缺 PID 列
                "  TCP    0.0.0.0:5000   0.0.0.0:0   LISTENING   258248",
            ]
        )
        assert web_app._parse_netstat_listeners(messy, 5000) == [258248]

    def test_case_and_header_rows_ignored(self):
        # 表头"Proto TCP"之类以 TCP 开头的行没有 5 列,应被忽略不崩。
        out = "  TCP    Local Address  Foreign Address  State  PID\n  TCP    0.0.0.0:5000  0.0.0.0:0  LISTENING  42\n"
        assert web_app._parse_netstat_listeners(out, 5000) == [42]
