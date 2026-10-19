# API 文档

交互式文档：启动后端后访问 `http://localhost:8000/docs`（Swagger UI）。

## 认证

写操作需 `Authorization: Bearer <token>`；读操作（GET）公开。
获取 token：`POST /auth/login {"username","password"}` → `{"token"}`，有效期 12 小时。

## 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /auth/login | 登录，返回 JWT |
| GET | /status | 调度器/任务/配置状态（真实状态，非模拟） |
| GET | /health | 健康检查（含数据库连通性） |
| GET | /sources | 数据源列表（含失败次数/最近错误/贡献量） |
| POST | /sources | 新增数据源（需认证；频率≥5分钟；类型白名单校验） |
| PUT | /sources/{id} | 编辑 |
| DELETE | /sources/{id} | 删除 |
| POST | /sources/{id}/pause · /resume · /approve | 暂停 / 恢复 / 候选源审核启用 |
| POST | /sources/{id}/trigger | 手动采集（同步、含指数退避重试） |
| POST | /sources/{id}/test | 连通性测试（不写入活动） |
| GET | /events | 活动列表。筛选：q, country, city, type, confidence, verified, finished, free, start_from, start_to |
| GET | /events/{id} | 详情（含全部来源/变更历史/原文链接） |
| GET | /calendar?view=month|week&date= | 日历视图数据 |
| GET | /crawl-logs | 采集日志（按日期/数据源/状态筛选） |
| GET | /stats/dashboard | 仪表盘统计 |
| GET | /stats/analysis | 情报分析统计 |
| GET/POST/PUT/DELETE | /notification-rules | 通知规则 CRUD（GET 对凭证打码） |
| POST | /notification-rules/{id}/test | 测试发送（如实报告失败原因） |
| GET | /settings | 非敏感系统配置 |
| PUT | /settings/system_setting?key= | 写入非敏感配置（敏感键 400 拒绝） |

## 数据源 method 配置示例

```json
// HTML 列表页
{"list_selector": "div.event-item", "title": "h3 a", "link": "h3 a@href",
 "fields": {"city": ".city", "date": ".date"}}
// JSON API
{"path_to_array": "data.items", "url_field": "url", "title_field": "title"}
// 搜索发现
{"keywords": ["人工智能大会", "AI summit"], "freshness": "m30", "num": 10}
```
