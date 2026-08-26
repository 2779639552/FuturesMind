# 部署路线一加访问令牌 + 限流 —— 待办记录(2026-08-25)

> 状态:⏸ **已提出、待实施**(用户确认"不急,先计入待办")。
> 背景:Cloudflare Tunnel 快速隧道已上线(见 work-journal 2026-08-25 18:16),
> 公网 URL 无鉴权,拿到链接即可触发分析 → 烧 LLM API 预算。

## 目标

- `web_app.py` 加 **env 门控令牌鉴权**:`AGENTSENSE_AUTH_TOKEN` 设置了才要求 token,
  未设置时行为不变(本机免密照常)。
- 校验来源:`X-Auth-Token` 头 / `Authorization: Bearer` / `?token=` 查询参均可。
- 可选:对分析类接口(触发一次约 10 次 LLM 调用)加**每 IP 限流**(如 24h N 次)。

## 验收

- 未设 env → 本机访问行为与现在完全一致(回归测试不受影响)。
- 设 env + 无/错 token → 401;带正确 token → 正常。
- 加限流 → 超配额返回 429,并有清晰错误消息。

## 实施顺序(待用户点头)

1. web_app.py 加 `before_request` 守卫 + `_AUTH_TOKEN` 读取。
2. `pyproject.toml` 补测试(`tests/test_auth_guard.py`),覆盖无 env / 有 env 三种传法。
3. 重启 web 服务(隧道短暂断连)→ 给用户带 token 的新链接。

## 关联

- work-journal 2026-08-25 18:16(隧道上线)
- memory `2026-08-25-deploy-route1-tunnel.md`(cloudflared 位置/临时 URL)
