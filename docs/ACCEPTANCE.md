# 24 小时运行验收方案（v1.1）

> 未真正运行满 24 小时并记录以下指标前，不宣称通过稳定性验收。

## 执行

1. `bash scripts/deploy.sh` 完成部署，确认 `scripts/healthcheck.sh` 全绿。
2. 记录开始时间；24 小时内只做观察，不手工干预采集。
3. 第 12 小时执行一次 `docker compose -f docker-compose.prod.yml restart worker`，
   验证「worker 重启后调度恢复」；再执行一次宿主机重启，验证「容器自恢复」。
4. 第 24 小时收集数据：

```bash
bash scripts/healthcheck.sh
curl -s http://$HOST/api/status | python -m json.tool          # leader/job 数
curl -s http://$HOST/api/stats/analysis | python -m json.tool
curl -s "http://$HOST/api/crawl-logs?page_size=200" | python -m json.tool
docker stats --no-stream
```

## 记录表

| # | 验收项 | 来源 | 合格线 |
|---|---|---|---|
| 1 | 连续运行 24h | docker ps | 无容器反复重启（restart 计数稳定） |
| 2 | 按频率执行任务 | crawl-logs | ≥ 数据源数 × 24×60/频率 × 0.9 |
| 3 | 失败记录并重试 | crawl-logs failed + retry_count | 失败有原因，重试次数递增 |
| 4 | 单源故障不阻塞 | 抽查失败源前后其他源日志 | 其他源正常运行 |
| 5 | 重启后调度恢复 | restart 后 /status job_count | 恢复为源数量（worker 持锁） |
| 6 | 无重复活动 | events 数 vs unique 名称/时间 | 重复采集不产生新活动 |
| 7 | 数据持久化 | docker compose down -V 前 pgdata 卷在 | 重启后数据保留 |
| 8 | 通知去重 | notification_logs | 同一活动同一类型仅 1 条 sent |
| 9 | 资源可观察 | docker stats | 记录内存/CPU 峰值 |
| 10 | 备份可恢复 | restore 到测试库 | 备份可用 |
| 11 | 外部 API 配额失败可见 | crawl-logs/search 错误 | 429/403 有明确错误记录 |
| 12 | 长时间无成功采集告警 | sources.last_success_at | 超过 3×频率无成功的源可被发现 |

## 判定

全部达标 → 通过。任一不达标在 `docs/TROUBLESHOOTING.md` 建档，修复后重新计时 24 小时。
**当前状态：未运行验收（待部署后执行）**。
