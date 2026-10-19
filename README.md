# AI Event Radar — 全球 AI 活动实时监控与情报平台

7×24 小时持续监控互联网公开渠道，自动发现、识别、去重、核实并推送 AI 相关活动预告。
**所有仪表盘数字均来自真实数据库，系统不生成任何模拟/假数据。**

## 架构（v1.1 生产架构）

```
                ┌────────────┐
   公网 80/443 →│  nginx/web │ 唯一公网入口：SPA 静态资源 + /api 反向代理
                └─────┬──────┘
              ┌───────┴───────┐
              ▼               ▼
        ┌──────────┐    ┌──────────┐
        │   api    │    │  worker  │ 独立后台采集进程（APScheduler）
        │ FastAPI  │    │ ROLE=worker
        └────┬─────┘    └────┬─────┘
             │               │  DB 领导锁：全局仅 1 个活跃调度器
             └───────┬───────┘
                     ▼
              ┌────────────┐
              │ PostgreSQL │ 仅内网通信
              └────────────┘
```

- **api**：只处理 Web 请求，不启动调度器（ROLE=api）。
- **worker**：`python -m app.worker`，通过数据库领导锁保证全集群只有一个活跃调度器；
  worker 重启后从 sources 表恢复全部任务；API 重启不影响 worker。
- **web**：多阶段构建的静态前端，由 Nginx 直接提供（运行时不执行 npm install）。
- SSRF：所有出站请求逐跳校验 + 守护型 DNS 解析器（连接时也过滤内网解析结果）。

## 数据源插件（全部已实现并测试）

| 类型 | 凭证需求 | 状态 |
|---|---|---|
| RSS / Atom | 无 | ✅ 真实 E2E 测试通过（MIT News AI） |
| HTML 网页解析 | 无 | ✅ 按 sources.method 配置选择器 |
| 官方 JSON API | 视 API | ✅ 含类型校验 |
| Sitemap | 无 | ✅ |
| 页面变更检测 | 无 | ✅ 状态持久化到 DB（source_states），重启不丢基线 |
| 搜索引擎发现 | **需要 API Key** | ✅ serper/tavily/bing 可插拔，未配置明确提示 |
| AI 结构化抽取 | **需要 API Key** | ✅ OpenAI 兼容接口，未配置自动回退规则抽取 |
| 通知 | 视通道 | ✅ webhook/email/telegram/企业微信，(规则,活动,类型) 去重 |

## 快速开始

### 本地开发
```bash
cd backend && python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp ../.env.example .env   # ENV=dev 可用默认值
python scripts/seed_sources.py
uvicorn app.main:app --port 8000
# 新终端
cd ../frontend && npm install && npm run dev
```

### 生产部署（OCI ARM64 / 任意 Docker 主机）
```bash
cp .env.example .env        # 按注释填写 ENV=production、JWT_SECRET、ADMIN_PASSWORD、DB_PASSWORD、ALLOWED_ORIGINS
bash scripts/deploy.sh      # 检查→构建→迁移→启动→健康检查，失败即中止并报错
```
详见 `docs/DEPLOY.md`。备份：`bash scripts/backup.sh`；恢复：`bash scripts/restore.sh <file>`；
体检：`bash scripts/healthcheck.sh`。

## 目录结构

```
ai-event-radar/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口 + 生产配置校验 + 迁移初始化
│   │   ├── worker.py          # 独立采集进程入口（python -m app.worker）
│   │   ├── config.py          # .env + 生产启动安全校验（P0）
│   │   ├── security.py        # PBKDF2 + JWT + SSRF 逐跳校验 + 守护型 DNS
│   │   ├── models.py          # 15 张表（含 scheduler_locks / source_states）
│   │   ├── collectors/        # rss / html / api / sitemap / change_detection / search
│   │   ├── services/          # pipeline / extraction / dedup / notifier / discovery / scheduler(DB锁)
│   │   ├── routers/           # auth / sources / events / notifications / health
│   │   ├── seed.py            # 幂等种子数据源（python -m app.seed）
│   │   └── alembic 相关迁移在 backend/alembic/
│   └── tests/                 # 82 项测试 + 2 项真实 E2E
├── frontend/                  # React 18 + TS + Vite，8 页面
├── scripts/                   # deploy / backup / restore / healthcheck
├── docs/                      # API / DEPLOY / ACCEPTANCE / SECURITY / LIMITATIONS / TROUBLESHOOTING
├── Dockerfile.api / Dockerfile.web / docker-compose.prod.yml / nginx.prod.conf
└── .env.example / .gitattributes
```

## 安全要点（详见 docs/SECURITY.md）

- 生产启动校验：默认 JWT_SECRET / 弱管理员密码 / SQLite / 通配 CORS 一律**拒绝启动**。
- 管理员初始化幂等：只建号，**永不重置已存在密码**；支持凭密码修改与 ADMIN_RESET_TOKEN 重置。
- SSRF：拒绝跳内网、云元数据、非 http(s)、重定向超限；DNS 解析后过滤；响应 5MB 上限。
- 密钥只存服务端 .env；通知凭证在 API 打码；敏感配置禁止写入系统设置接口。

## 测试

```bash
cd backend
pytest tests/ -m "not real_network"   # 82 项：单元/集成/安全/迁移/启动配置/SSRF 重定向
pytest tests/ -m real_network         # 2 项：真实公开源端到端采集
```

## 24 小时验收

见 `docs/ACCEPTANCE.md`。**未真实运行满 24 小时前不宣称通过稳定性验收。**
