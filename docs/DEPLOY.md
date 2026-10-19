# 部署文档

## 开发环境（Windows / Linux）

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Linux: source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env           # 修改 ADMIN_PASSWORD、JWT_SECRET
python scripts/seed_sources.py
uvicorn app.main:app --port 8000 --reload
```

前端：`cd frontend && npm install && npm run dev`（Vite 已配置 /api 代理到 8000 端口）。

## 生产部署（Docker Compose + Nginx + PostgreSQL）

```bash
cp .env.example .env
# 必改：JWT_SECRET（≥32位随机）、ADMIN_PASSWORD、POSTGRES_PASSWORD
docker compose up -d --build
```

- API：`http://<host>/`（Nginx → api:8000）
- 控制台：`http://<host>/web/`
- 数据持久化：`pgdata` 卷（数据库）、`appdata` 卷（SQLite 备用/文件）

## 数据库迁移

开发用 SQLite（`data/radar.db`）。切换 PostgreSQL：修改 `.env` 的 `DATABASE_URL` 后重启，
`Base.metadata.create_all` 自动建表。表结构升级建议使用 Alembic（当前版本以
create_all + 显式字段演进为主，字段变更前请备份数据库）。

## 升级/重启

- `docker compose restart api` — 调度器启动时从数据库恢复全部任务（重启恢复已实现并测试）。
- 日志：`docker compose logs -f api`。
