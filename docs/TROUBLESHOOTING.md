# 常见故障排查手册（v1.1 增补）

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动即退出：⛔ 生产环境配置校验失败 | ENV=production 下用了默认/弱密钥或 SQLite | 按错误提示修改 .env：生成随机 JWT_SECRET（≥32 位）、强 ADMIN_PASSWORD（≥12 位）、PostgreSQL DATABASE_URL、显式 ALLOWED_ORIGINS |
| 采集失败 `SSRF 防护拦截（第 N 跳）` | 目标或其重定向落在内网/元数据 | 合法公网站点请反馈 URL 复现；内网站点不采集（设计行为） |
| 采集失败 `响应体超过大小上限` | 页面 >5MB | 增大 `.env` MAX_RESPONSE_BYTES 或改用更精确的 HTML 选择器 |
| 健康检查 503 `not-ready` | 数据库不可达 | 检查 db 容器 healthy；查 `docker compose logs db` |
| /status 显示调度器未运行 | 查询的是 api 进程（ROLE=api 无锁属预期） | 锁在 worker：`docker compose -f docker-compose.prod.yml logs worker` 看 "scheduler started as leader" |
| worker 启动后长时间无采集 | 未取得锁（另一个 worker 持有且心跳正常） | 确认只应有一个 worker 实例；如旧实例已死，等 SCHEDULER_LOCK_TTL(120s) 后自动接管 |
| 迁移报错 `alembic_version` 相关 | DB 处于中间状态 | `alembic current` / `alembic upgrade head`；老库已由 migrate_or_init 自动 stamp |
| 部署脚本某步失败 | 见脚本输出 | deploy.sh 每步中止且不伪报成功；按提示修后重跑 |
| 备份文件被清理 | 保留策略最近 14 份 | 调整 backup.sh 的 `tail -n +15` |
