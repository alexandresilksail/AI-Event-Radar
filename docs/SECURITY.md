# 安全审计报告（v1.1 增补）

## 本轮新增控制措施

| 类别 | 措施 | 测试 |
|---|---|---|
| 生产启动安全 | ENV=production + 默认/弱 JWT_SECRET、弱管理员密码、SQLite 库、通配 CORS → 拒绝启动（清晰错误） | test_default_jwt_secret_rejected / test_default_admin_password_rejected / test_sqlite_rejected_in_production / test_wildcard_origins_rejected_in_prod / test_good_production_config_passes ✅ |
| 管理员生命周期 | 初始化幂等（永不在启动时改已有密码）；可凭旧密码改密；ADMIN_RESET_TOKEN 安全重置（未配置禁用） | test_admin_creation_idempotent_no_reset / test_change_password / test_reset_endpoint_disabled_without_token ✅ |
| 数据库凭证 | DATABASE_URL 密码 URL 编码（特殊字符）、连接池 pre_ping/recycle/超时 | encode_db_url 单元覆盖 ✅ |
| 迁移系统 | Alembic 版本化迁移，upgrade/downgrade 双向可执行；老库自动补齐新表并 stamp（数据保留） | test_upgrade_creates_all_tables / test_downgrade_reverses / test_legacy_db_gets_new_tables ✅ |
| SSRF 重定向 | 手动跟随重定向，每一跳重新校验；上限 3 跳；响应体 5MB 上限 | test_redirect_to_metadata_blocked_every_hop / test_redirect_to_private_ip_blocked / test_redirect_loop_terminates ✅（真实本地 HTTP 服务器验证实际拦截） |
| DNS 竞态 | 守护型 getaddrinfo：连接时实际解析到的内网地址也被过滤 | test_guarded_resolver_filters_private ✅ |
| CORS | 环境变量白名单；生产禁止通配；开发限定本地源 | test_origins_from_env / test_dev_default_local_origins / test_prod_refuses_wildcard ✅ |
| 健康检查 | /health/live（无副作用）与 /health/ready（DB 不可用返回 503 不伪装） | test_live_and_ready ✅ |
| 变更检测安全 | 请求失败不回写哈希；状态持久化 source_states | test_failed_request_keeps_hash / test_state_is_db_backed_not_memory ✅ |
| API 数据校验 | 非 dict 行/非 http URL 字段被拒绝，不视为安全 | test_type_validation ✅ |
| 公网暴露面 | compose 仅 web 映射 80；db/api/worker 走内网 | docker-compose.prod.yml（人工审查） |
| 镜像安全 | 多阶段构建、非 root user、无密钥打进镜像、npm ci 锁版本 | Dockerfile.api/.web（人工审查） |
| 密钥不入镜像/日志 | 全部走 .env → 环境变量；日志不打印凭证 | 代码审查 |

## 残余风险

1. 读接口公开：如需私有化，给 `list_events` 等加 `current_user` 依赖。
2. 公开 GET 无速率限制：生产建议在 Nginx 加 `limit_req`。
3. ALLOWED_ORIGINS 部署者误配：部署脚本已做必填校验，但值本身由运维保证。
