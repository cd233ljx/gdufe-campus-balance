
### metrics.utopiacd.online（广财校园工具箱使用统计）

- 公网链路：Cloudflare Tunnel 通配入口 → Caddy `:8080` 显式 `@campus_metrics` Host 路由 → `127.0.0.1:18110`。
- 进程：用户级 `campus-metrics.service`，代码及 unit 位于 `/srv/stacks/campus-metrics/`；数据位于 `/srv/data/campus-metrics/metrics.sqlite3`，目录权限 700。
- 公开接口仅有 `GET /healthz`、`POST /v1/events`、`POST /v1/delete`；报表仅通过服务器本机命令导出 CSV，不提供公网管理页面。
- 验证：`systemctl --user is-active campus-metrics.service`；`curl --noproxy '*' http://127.0.0.1:18110/healthz`；`curl --noproxy '*' https://metrics.utopiacd.online/healthz`；未知通配子域名应返回 404。
- 回滚：从 `/srv/stacks/caddy/Caddyfile` 移除 `@campus_metrics` 路由，校验并重载 Caddy；执行 `systemctl --user disable --now campus-metrics.service`。保留数据库以处理删除及核对请求。
- 上线日期：2026-09-28。Caddy 改动前备份：`/srv/stacks/caddy/Caddyfile.bak.campus-metrics-20260928`。
