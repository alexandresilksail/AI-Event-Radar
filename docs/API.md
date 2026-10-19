# API 文档（v1.1 更新）

交互式文档：启动后访问 `http://<host>/docs`。

## 认证与安全

写操作需 `Authorization: Bearer <token>`；读操作（GET）公开。
`POST /auth/login` 获取 token（12 小时有效）。

| 端点（新增） | 说明 |
|---|---|
| POST /auth/change-password | 已登录修改密码（旧密码校验，≥12 位） |
| POST /auth/reset-password | 安全重置（需 .env 的 ADMIN_RESET_TOKEN，未配置返回 403） |
| GET /health/live | 存活探针（无副作用，Docker HEALTHCHECK 用） |
| GET /health/ready | 就绪探针（检查数据库；不可用时返回 503，不伪装 ready） |
| GET /status | 含 role、leader_holder、running_task_source_ids 的真实状态 |
| GET /health | 旧版兼容健康检查 |

## 完整端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /auth/login · /auth/change-password · /auth/reset-password | 认证 |
| GET | /status · /health(/live)(/ready) | 状态与探针 |
| GET | /sources | 数据源列表 |
| POST/PUT/DELETE | /sources[/{id}] | 数据源 CRUD（需认证） |
| POST | /sources/{id}/pause · /resume · /approve · /trigger · /test | 管理操作 |
| GET | /events | 筛选：q, country, city, type, confidence, verified, finished, free, start_from/to |
| GET | /events/{id} | 详情（含来源、变更历史） |
| GET | /calendar | 日历数据 |
| GET | /crawl-logs | 采集日志 |
| GET | /stats/dashboard · /stats/analysis | 统计 |
| GET/POST/PUT/DELETE | /notification-rules[/{id}[/test]] | 通知规则 |
| GET/PUT | /settings · /settings/system_setting | 非敏感配置 |

## SSRF/采集安全（safe_get 行为）

- 每跳重定向都重新校验（公共域名重定向到内网/元数据 → `SSRF 防护拦截（第 N 跳）`）。
- 重定向上限 3 跳（MAX_REDIRECTS）；响应体上限 5MB（MAX_RESPONSE_BYTES）。
- 域名解析结果经守护型解析器过滤，连接时也拒绝解析到内网的目标。

## Alembic 迁移

```bash
cd backend
alembic upgrade head        # 应用迁移（升级）
alembic downgrade -1        # 回滚一步
alembic history             # 查看历史
```
应用启动时会自动执行 `migrate_or_init()`：新库建立结构；老库补齐新表并 stamp 基线（数据保留）。
