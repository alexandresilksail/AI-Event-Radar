# AI Event Radar — 全球 AI 活动实时监控与情报平台

7×24 小时持续监控互联网公开渠道，自动发现、识别、去重、核实并推送 AI 相关活动预告。
**所有仪表盘数字均来自真实数据库，系统不生成任何模拟/假数据。**

## 实际可用的数据源类型（已实现并测试）

| 类型 | 说明 | 凭证需求 | 状态 |
|---|---|---|---|
| RSS / Atom | feedparser 解析，公开订阅源 | 无 | ✅ 已实现，真实 E2E 测试通过（MIT News AI） |
| HTML 网页解析 | 按 sources.method 中的 CSS 选择器配置 | 无 | ✅ 已实现（选择器按源配置，不硬编码） |
| 官方 API (JSON) | 通用 JSON API + path_to_array 配置 | 视 API 而定 | ✅ 已实现框架 |
| Sitemap | 解析 sitemap.xml 并抽样抓取 | 无 | ✅ 已实现 |
| 页面变更检测 | 内容哈希比对，变更才处理 | 无 | ✅ 已实现 |
| 搜索引擎发现 | serper / tavily / bing，可插拔 | **需要 API Key** | ✅ 已实现，未配置 Key 时明确提示、不伪装 |
| AI 结构化抽取 | OpenAI 兼容接口，JSON 输出 | **需要 API Key** | ✅ 已实现，未配置时回退规则抽取 |
| 通知 | Webhook / 邮件 / Telegram / 企业微信 | 视通道而定 | ✅ 已实现，未配置凭证时记录 skipped 并继续运行 |

## 受访问限制、暂不批量采集的来源

- 微信公众号文章：受访问控制，不批量抓取；支持人工提交 URL 单条收录。
- 需登录/验证码/付费墙的平台（部分 Meetup / Eventbrite 页面）：不绕过技术措施，
  建议走官方 API（Eventbrite API 已在 APICollector 框架内可直接配置接入）。

## 快速开始（开发环境）

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
copy ..\.env.example .env                        # 修改 ADMIN_PASSWORD / JWT_SECRET
python scripts/seed_sources.py                   # 写入真实公开数据源
uvicorn app.main:app --port 8000                 # 启动 API + 调度器
# 新终端
cd frontend && npm install && npm run dev        # http://localhost:5173
```

登录：`admin` / `.env` 中的 `ADMIN_PASSWORD`。

## Docker 部署

```bash
cp .env.example .env   # 必须修改 JWT_SECRET、ADMIN_PASSWORD、POSTGRES_PASSWORD
docker compose up -d --build
# API: http://localhost  (Nginx -> api:8000)
# 控制台: http://localhost/web/
```

## 目录结构

```
ai-event-radar/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口（生命周期：建表/恢复调度）
│   │   ├── config.py          # .env 配置
│   │   ├── database.py        # SQLite(开发)/PostgreSQL(生产)
│   │   ├── models.py          # 13 张表
│   │   ├── security.py        # PBKDF2 + JWT + SSRF 防护
│   │   ├── deps.py            # 认证依赖 + 审计日志
│   │   ├── collectors/        # 采集插件（base/rss_sitemap/html_api/search）
│   │   ├── services/          # pipeline/extraction/dedup/notifier/discovery/scheduler
│   │   └── routers/           # auth/sources/events/notifications
│   ├── tests/                 # 单元+集成+真实E2E（pytest -m real_network）
│   └── scripts/seed_sources.py
├── frontend/                  # React 18 + TypeScript + Vite（7 个页面）
├── docs/                      # API 文档/部署/故障排查/安全审计/已知限制
├── Dockerfile / docker-compose.yml / nginx.conf / .env.example
```

## 数据库表（13 张）

users, sources, crawl_tasks, crawl_logs, raw_documents, events, event_sources,
event_history, event_tags, notification_rules, notification_logs, system_settings, audit_logs

## 核心机制

- **调度**：APScheduler 按源频率执行；失败指数退避（连续失败翻倍，封顶 24h）；
  同源任务不重叠；重启后从数据库恢复全部任务。
- **状态区分**：`GET /status` 明确区分调度器运行 / 任务执行中 / 数据源健康 / 最近成功采集时间。
- **去重（5 层）**：URL 规范化 → 名称相似度（SequenceMatcher）→ 主办方+时间 → 城市+报名地址 → 合并保留全部来源。同名不同活动不会被误删。
- **可信度分级（规则生成，非 AI 自评）**：A=官方源或完整信息；B=时间地点齐全+多来源；C=单一非官方来源；待核实=关键字段缺失。
- **抽取铁律**：网页内容视为不可信数据；页面中的指令一律忽略；未确认字段填「未知/待核实」，禁止编造。
- **SSRF 防护**：协议白名单、localhost/内网/云元数据/DNS 重绑定拦截。
- **通知去重**：`(rule, event, type)` 唯一约束，绝不重复推送。

## 自动化测试

```bash
cd backend
pytest tests/ -m "not real_network"   # 49 项：单元/集成/安全/去重/通知/权限
pytest tests/ -m real_network         # 2 项：真实公开源端到端采集
```

最近一次结果：**49 passed + 2 passed（真实网络）**。

## 24 小时运行验收方案

见 `docs/ACCEPTANCE.md`。验收完成前，系统不会宣称已通过 24h 稳定性验收。

## 尚未完成 / 依赖外部条件的事项

见 `docs/LIMITATIONS.md`（微信内容合规接入、AI/搜索 Key 实际启用、24h 验收执行等）。
