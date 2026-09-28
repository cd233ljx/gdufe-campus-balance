# 使用统计服务

本服务仅接收选择参与的桌面客户端发来的固定事件，不提供公网页面或报表接口。`/healthz` 是健康检查；`/v1/events` 接收最多 50 条事件并按 `event_id` 去重；`/v1/delete` 删除一个随机设备标识对应的事件，并短期拒绝该标识的滞后重传。

## home-server 部署

1. 把本目录的 `metrics_service.py`、`campus-metrics.service` 放在 `/srv/stacks/campus-metrics/`。创建权限为当前用户私有的 `/srv/data/campus-metrics/`。
2. 执行 `systemctl --user link /srv/stacks/campus-metrics/campus-metrics.service`、`systemctl --user daemon-reload`、`systemctl --user enable --now campus-metrics.service`，确认 `curl --noproxy '*' http://127.0.0.1:18110/healthz` 返回 `ok`。服务仅监听 `127.0.0.1:18110`。
3. 在 `/srv/stacks/caddy/Caddyfile` 的 `:8080` 站点中，给 `metrics.utopiacd.online` 添加显式 `host` 匹配并反代 `127.0.0.1:18110`。先校验 Caddy 配置，再重载。现有 Cloudflare Tunnel 通配入口会转发这个子域名；未知域名应继续返回 404。
4. 从公网验证健康检查和合成事件，导出 CSV 后删除合成设备。真实设备标识和原始事件不要复制到仓库或聊天。

导出某段时间的日报和功能选择汇总（只在服务器上运行）：

```bash
python3 /srv/stacks/campus-metrics/metrics_service.py export --start 2026-09-01 --end 2026-09-30 --output /srv/data/campus-metrics/reports/2026-09
```

输出为 `/srv/data/campus-metrics/reports/2026-09/daily.csv` 和 `features.csv`。日报最后一行按整个日期范围去重，不能把每日设备数直接相加当作总人数。

## 回滚

移除 Caddy 的统计域名路由并校验、重载；运行 `systemctl --user disable --now campus-metrics.service`。保留 `/srv/data/campus-metrics/` 以便核对和处理删除请求，待明确确认后再清理。
