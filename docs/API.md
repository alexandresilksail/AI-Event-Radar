# API 文档（v1.3 更新）

交互式文档：启动后访问 `http://<host>/docs`。

## 认证与安全

写操作需 `Authorization: Bearer <token>`；读操作（GET）公开。
`POST /auth/login` 获取 token（12 小时有效）。

| 端点 | 说明 |
|---|---|
| POST /auth/login | 登录获取 JWT |
| POST /auth/change-password | 已登录修改密码（校验旧密码，新密码 ≥10 位） |
| POST /auth/reset-password | 安全重置（需 .env 配置 ADMIN_RESET_TOKEN，未配置返回 403） |
| GET /health/live | 存活探针：仅证明进程活着（无副作用，不做业务判断） |
| GET /health/ready | 就绪探针：检查数据库连通；不可用返回 503，不伪装 ready |
| GET /health/scheduler | 调度器健康：数据库租约是否有效（leader_elected/lease_valid/ok/lease_version），无有效租约时 ok=false |
| GET /status | 含 role、leader_holder、running_task_source_ids 的真实状态 |
| GET /health | 旧版兼容健康检查 |

> Worker 容器另在 `WORKER_HEALTH_PORT`（默认 8001，仅监听内部网络）暴露同款 `/health/live|ready|scheduler`，
> 供容器编排判断 Worker 进程与调度状态。

## 完整端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /auth/login · /auth/change-password · /auth/reset-password | 认证 |
| GET | /status · /health(/live)(/ready) | 状态与探针 |
| GET | /sources | 列表：keyword/type/enabled/health 筛选 + 分页；返回 items、total、summary（healthy/failing/paused/never 计数） |
| POST/PUT/DELETE | /sources[/{id}] | 数据源 CRUD（需认证）；DELETE 默认软删除，`?permanent=true` 永久删除级联日志 |
| GET | /sources/{id} | 详情：基本信息 + 最近 24h/7d 趋势 + 成功/失败/部分成功计数 + 最近日志 + 最近原始文档 |
| POST | /sources/{id}/pause · /resume | 暂停/恢复（同步调度器） |
| POST | /sources/{id}/approve | 审核候选源 |
| POST | /sources/{id}/trigger | 立即采集（真实创建任务并执行） |
| POST | /sources/{id}/test | 连通性测试（只读探测，不写入数据） |
| GET | /events | 筛选：q, country, city, type, confidence, verified, finished, free, start_from/to |
| GET | /events/{id} | 详情（含来源、变更历史） |
| GET | /calendar | 日历数据 |
| GET | /crawl-tasks | 任务列表：source_id/status 筛选 + 分页；返回 id、source_name、status（pending/running/success/partial/failed）、trigger（manual/scheduled/retry/seed）、retry_of、attempts、started_at/finished_at、duration_ms、found_count、new_count、updated_count、duplicate_count、error |
| GET | /crawl-tasks/{id} | 任务详情：时间线、关联日志、HTTP 结果、重试链 |
| POST | /crawl-tasks/{id}/retry | 失败重试（创建 retry 关联的新任务） |
| GET | /crawl-logs | 采集日志（按源/状态筛选） |
| GET | /monitor | 系统监控：api/worker/db 健康、调度器任务与下次运行、最近错误 |
| GET | /stats/dashboard | 仪表盘：活动总数、今日新增、源健康汇总、今日成功率、平均耗时、运行中任务、7 天趋势、系统状态 |
| GET | /stats/analysis | 多维分析统计 |
| GET/POST/PUT/DELETE | /notification-rules[/{id}[/test]] | 通知规则 |
| GET/PUT | /settings · /settings/system_setting | 非敏感配置（敏感键禁止写入） |

## 任务状态机

`pending → running → success | partial | failed`

- `success`：采集成功且有新增内容
- `partial`：采集成功但无新增（内容未变化/全部已去重）——与 success、failed 是三种不同状态
- `failed`：请求失败或解析失败，自动记录 error 与指数退避重试

## SSRF/采集安全（safe_get 行为）

- 仅允许 http/https；拒绝 localhost、私有 IPv4/IPv6、链路本地、云元数据地址
- 手动重定向（最多 3 跳）：**每一跳**都重新做 URL 校验
- 守护型 DNS 解析：实际连接时解析到内网地址也会被拒绝（防 rebinding 竞态）
- 响应体上限 5MB、请求超时 30s、域名级限速、全局限并发
- 用户提交 URL、API 返回 URL、Sitemap 链接使用同一套校验策略

## Alembic 迁移

生产使用 Alembic 版本化迁移（`alembic/versions/`）：

```
alembic upgrade head            # 升级
alembic downgrade <revision>    # 回滚
```

- 全新库：直接 `upgrade head` 建全部表
- 老库（无 alembic_version）：自动补齐缺失表 → stamp 基线 → 执行增量迁移，**不丢数据**
- 启动时由 `migrate_or_init()` 自动执行；迁移失败会输出明确错误并阻止启动
