# 部署文档（v1.3 生产架构）

目标环境：**OCI Always Free Ubuntu ARM64**（2 OCPU / 12GB，也可在 1 OCPU 试用）。
镜像均提供 linux/arm64 与 linux/amd64 支持（python:3.13-slim / nginx:1.27-alpine / postgres:16-alpine）。

## 前置条件

1. Docker Engine 24+ 与 compose 插件。
2. 开放端口：仅 `HTTP_PORT`（默认 80）。PostgreSQL/API 仅内网。
3. `.env` 已按 `.env.example` 填写（生产 ENV=production，缺项会被拒绝启动）。

## 一键部署

```bash
git clone https://github.com/alexandresilksail/AI-Event-Radar.git && cd AI-Event-Radar
cp .env.example .env   # 填写全部必填
bash scripts/deploy.sh
```

deploy.sh 会依次：检查 Docker → 检查环境变量 → 检查磁盘（≥2GB）→ 校验 compose →
构建镜像 → 启动 db 并等待 healthy → **执行一次性 migrate（独立容器，跨进程互斥，失败即中止）** →
启动 api/web → 等待 /health/live 与 /api/health/ready → 启动 worker → 校验状态。
**任何一步失败立即中止，不会伪报成功。** API/Worker 启动不再执行迁移（AUTO_MIGRATE=false）。

## 服务与端口

| 服务 | 镜像 | 公网暴露 | 说明 |
|---|---|---|---|
| web (nginx) | Dockerfile.web | `${HTTP_PORT:-80}` | 唯一公网入口：SPA + /api 反代 + 安全响应头；TLS 由外部反代或 nginx.tls.conf 终止 |
| migrate | Dockerfile.api | 无 | 一次性 `python -m app.migrate`：Alembic upgrade head + 跨进程互斥；成功完成后 API/Worker 才启动 |
| api | Dockerfile.api | 无（仅内网） | FastAPI，ROLE=api 不带调度器；分层健康 /health/live·ready·scheduler |
| worker | Dockerfile.api | 无 | `python -m app.worker`，DB 领导锁单调度器；内置健康服务（WORKER_HEALTH_PORT，仅内网） |
| db | postgres:16-alpine | 无 | 数据卷 pgdata |

## 日常运维

```bash
# 备份 / 恢复 / 体检
bash scripts/backup.sh
bash scripts/restore.sh backups/radar_YYYYmmdd_HHMMSS.sql.gz   # 需输入 RESTORE 确认
bash scripts/healthcheck.sh

# 查看日志
docker compose -f docker-compose.prod.yml logs -f api|worker|web

# 重启（worker 会从 DB 恢复全部采集任务）
docker compose -f docker-compose.prod.yml restart

# 更新版本
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

## ARM64 构建验证

```bash
docker buildx build --platform linux/arm64 -f Dockerfile.api -t radar-api:arm64 --load .
docker buildx build --platform linux/arm64 -f Dockerfile.web -t radar-web:arm64 --load .
docker run --rm radar-api:arm64 python -c "import app.main; print('api image ok')"
docker run --rm radar-web:arm64 sh -c "ls /usr/share/nginx/html/index.html"
```

## 异地备份（免费资源不保证永久可用）

1. 将 `backups/` 目录加入 cron 每日执行 `scripts/backup.sh`。
2. 将备份文件同步到对象存储（如 OCI Object Storage 免费 10GB，或任意异地位置）：
   ```cron
   30 3 * * * cd /opt/AI-Event-Radar && bash scripts/backup.sh /shared-backups && rclone sync /shared-backups remote:radar-backups
   ```
3. 云资源回收/区域容量不足时，用最近备份在新实例上重新 deploy + restore 即可恢复。

## HTTPS（可选）

前端已声明安全响应头；在 web 前增加 TLS 终止（Caddy / cloudflared / OCI 负载均衡）。
生产 ALLOWED_ORIGINS 应设置为你的 HTTPS 域名。
