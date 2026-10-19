# 24 小时运行验收方案

> 未真正运行满 24 小时并记录以下指标前，不宣称通过稳定性验收。

## 执行步骤

1. `docker compose up -d --build`（或本机 uvicorn），加载 `scripts/seed_sources.py` 种子源。
2. 记录开始时间；期间不干预。
3. 第 24 小时收集数据：

```bash
curl -s http://localhost:8000/stats/analysis | python -m json.tool
curl -s http://localhost:8000/crawl-logs?page_size=100 | python -m json.tool
curl -s http://localhost:8000/status
```

## 记录表

| 指标 | 数据来源 | 合格线 |
|---|---|---|
| 任务执行次数 | /crawl-logs total | ≥ 数据源数 × (24×60/频率) × 0.9 |
| 成功/失败次数 | crawl-logs 按 status 计数 | 成功率 ≥ 80%（外网抖动允许） |
| 新增活动数量 | /stats/analysis daily_new | 与源内容匹配即可（无造假要求） |
| 数据源可用率 | sources: healthy/(total-candidate) | ≥ 70% |
| 重复数据比例 | duplicate_count/(found) 聚合 | < 90%（证明去重工作） |
| 通知成功率 | notification-logs: sent/(sent+failed) | 有凭证通道 ≥ 90% |
| 内存/CPU | docker stats / 任务管理器 | 记录峰值，无明显泄漏（线性增长） |
| 重启恢复 | 第 12 小时 `docker compose restart api` | /status 显示 job_count 恢复，日志继续追加 |

## 判定

全部指标达标且无未恢复的关键异常 → 通过。
不达标项需在 TROUBLESHOOTING.md 建档，修复后重新计时 24 小时。
