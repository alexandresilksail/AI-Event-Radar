# 发布验收清单 — AI Event Radar v1.4

**配套文档**：`RELEASE_AUDIT.md`（实测数据）、`TEST_REPORT.md`（测试明细）、`SECURITY_AUDIT.md`（安全详情）

**使用方法**：在目标环境逐项执行并勾选。凡本清单标 **NOT VERIFIED** 的项目，表示**当前验收环境客观无法验证**（无 Docker / 无域名 / 会话时长限制），**不等于通过**。

**图例**
- `[x]` ✅ 本次验收环境已实测通过
- `[ ]` ⛔ **BLOCKING** — 放行前必须在目标环境执行并通过
- `[ ]` ⚠️ NOT VERIFIED — 本次无法验证，需在目标环境补做
- `[ ]` 📋 建议项 — 不阻断发布，但建议纳入首轮运维

---

## 一、代码质量门禁（本次已全部通过）

- [x] 后端完整测试套件全绿：`387 passed, 3 deselected`（687.61 s）
- [x] 前端 `npm run build` 成功（tsc 类型检查 + Vite 打包，48 modules，234.70 kB / gzip 74.33 kB）
- [x] Alembic 迁移链单一 head：`888363d8d20c (head)`
- [x] 全部 `app/` 模块可正常 import（30 个模块，导入失败 0）
- [x] Python 3.13 弃用警告清零（原 563 条 → 0）
- [x] `pytest.ini` 存在，`--strict-markers` 生效，`real_network` 默认排除
- [x] CI 工作流已落地：`.github/workflows/ci.yml`（backend + frontend 双 job）
- [x] `requirements.txt` 全部固定版本，可复现构建

**复现命令**

```bash
cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q
cd frontend && npm run build
cd backend && ../.venv/Scripts/python.exe -m alembic heads
```

---

## 二、Stage 1 — 代码审计修复项回归（本次已全部通过）

以下 11 项缺陷均已修复并有对应回归测试，**发布前需确认目标分支包含这些修复**：

- [x] **P0** worker 进程安装 DNS 级 SSRF 守卫 → `app/worker.py` → `test_release_hardening.py`
- [x] **P0** 解析器卸载恢复原生实现（消除跨测试模块泄漏） → `app/security.py` → `test_release_hardening.py`
- [x] **P1** 运维可信内网主机豁免机制 → `app/security.py` + `config.py` → `test_release_hardening.py`
- [x] **P1** 通知规则编辑保留已存密钥（掩码值语义化） → `app/routers/notifications.py` → `test_release_hardening.py`
- [x] **P1** 9 个运维端点加认证 → 全部 routers → `test_release_hardening.py`
- [x] **P1** Webhook URL 走 SSRF 策略 → `app/services/notifier.py` → `test_release_hardening.py`
- [x] **P1** 非订阅源响应显式报错 → `app/collectors/rss_sitemap.py` → `test_business_integrity.py`
- [x] **P1** 活动改期可生效并记入变更历史 → `app/services/dedup.py` → `test_business_integrity.py`
- [x] **P1** 阻断 `javascript:` 协议（存储层 + 渲染层） → `app/security.py` + `frontend/src/api.ts` → `test_security_audit.py`
- [x] **P1** 登录暴力破解防护 → `app/routers/auth.py` → `test_security_audit.py`
- [x] **P2** 口令常量时间比对 → `app/security.py` → `test_security_audit.py`

---

## 三、Stage 2 — 部署配置（⛔ BLOCKING：Docker 实测部分）

### 本次已验证

- [x] `config.py` ↔ `.env.example` ↔ compose ↔ nginx 配置交叉一致（41 项测试全绿）
- [x] 生产配置门禁双向行为：合法配置接受 / 弱配置逐条拒绝（实测输出见 AUDIT §2）
- [x] `encode_db_url()` 幂等单层百分号编码
- [x] compose 启动顺序：`db` → `migrate`（一次性） → `api` / `worker`
- [x] `.env.example` 含 `TRUSTED_INTERNAL_HOSTS`
- [x] nginx 静态资源缓存已配置

### 必须在目标环境执行（⛔ BLOCKING）

- [ ] **Docker 镜像构建成功**（`Dockerfile.api` / `Dockerfile.web`）
- [ ] **`docker compose -f docker-compose.prod.yml up -d` 全栈启动成功**
- [ ] **`migrate` 一次性服务正常退出（exit 0），api/worker 未抢跑**
- [ ] **PostgreSQL 15+ 容器内 `pg_dump` / `psql` 正常工作**
- [ ] **容器间网络可达**（api → db、worker → db、api → 外部）
- [ ] **`.env` 中所有必需变量已在目标环境真实赋值**（不得使用 `.env.example` 默认值）

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs migrate
```

---

## 四、Stage 3 — SSRF 纵深防护（本次已通过）

- [x] URL 策略校验（协议白名单 + 地址黑名单 + `is_global` 兜底）
- [x] 全局 `getaddrinfo` 守卫（关闭 DNS rebinding TOCTOU 窗口）
- [x] 重定向逐跳重校验
- [x] 全地址族分类：IPv4/IPv6、IPv4-mapped、6to4、Teredo、NAT64
- [x] 运维可信主机豁免不削弱其他主机的防护
- [x] worker 进程同样受保护

**目标环境补充验证**（⛔ BLOCKING，因为真实 DNS 环境与本地模拟不同）：

- [ ] 真实内网地址 `http://127.0.0.1/`、`http://169.254.169.254/`（云元数据）被拒绝
- [ ] 私有网段 `http://10.x.x.x/`、`http://192.168.x.x/` 被拒绝
- [ ] IPv6 环回 `http://[::1]/` 被拒绝
- [ ] DNS rebinding 场景（域名先解析到公网、再解析到私网）被拒绝
- [ ] 合法公网 URL 采集正常

---

## 五、Stage 4 — 调度器租约与 fencing（本次已通过）

- [x] 8 进程竞争 → 有且仅有 1 个领导者（真实独立 OS 进程）
- [x] 落败进程无法心跳、无法通过 fencing
- [x] `lease_version` fencing 令牌单调递增
- [x] 租约行在竞争过程中不撕裂
- [x] 被降级领导者无法派发任务
- [x] 崩溃后租约可被接管（TTL 过期路径）

**目标环境补充验证**（📋 建议）：

- [ ] 多副本部署时仅一个实例派发任务（观察日志中 leader 唯一性）
- [ ] worker 重启后租约在 TTL 内被接管

---

## 六、Stage 5 — 采集与 AI 业务完整性（本次已通过）

- [x] RSS 2.0 / Atom 解析
- [x] 畸形 XML 与验证码页显式报错（不再静默失效）
- [x] 去重三层（精确 URL / 标题+城市相似度 / 内容指纹）
- [x] 日期抽取支持 `至`/`到`/`~`/`–`/英文 `to`；`"total"` 不误匹配
- [x] 城市 / 国家 / 线上线下 / 主办方抽取
- [x] 未知字段用占位符（`未知` / `待核实`）而非编造
- [x] 端到端：采集 → 抽取 → 去重 → 入库 → 通知 → 增量与去重计数
- [x] 活动改期可生效并写入 `EventHistory`

**目标环境补充验证**（⛔ BLOCKING，需要真实源）：

- [ ] 至少 3 个真实 RSS 源采集成功
- [ ] 至少 1 个真实 HTML/API 源采集成功
- [ ] 真实 AI 模型抽取返回结构化字段（`AI_BASE_URL` / `AI_API_KEY` 已配置）
- [ ] 通知真实投递成功（邮件或企业微信）
- [ ] 去重在真实数据上生效（同一活动不重复入库）

---

## 七、Stage 6 — 认证 / XSS / CORS / 依赖（本次已通过）

- [x] PBKDF2-HMAC-SHA256，迭代 120,000（≥100,000）
- [x] 口令常量时间比对
- [x] JWT HS256 / 12 h / 篡改与过期被拒
- [x] 认证边界：9 个运维端点 401，公开端点 200
- [x] `javascript:` / `data:` / `vbscript:` / `file:` 协议在存储层阻断
- [x] CORS 显式白名单，通配符被拒
- [x] 写操作审计留痕，审计不含 token
- [x] 登录暴力破解 → 429 锁定，不泄露用户存在性
- [x] `pip-audit -r requirements.txt` → **No known vulnerabilities found**
- [x] `pip-audit --local` → 无漏洞（已升级 certifi / pip）
- [x] `npm audit --omit=dev --audit-level=high` → 2 项 moderate（react-router），**已逐一验证不可达**

**目标环境配置确认**（⛔ BLOCKING）：

- [ ] `JWT_SECRET` 长度 ≥32 且为随机值
- [ ] `ADMIN_PASSWORD` 强度足够且**已修改默认值**
- [ ] `DATABASE_URL` 为 PostgreSQL 且密码 ≥16 位
- [ ] `ALLOWED_ORIGINS` 为**实际域名**白名单（非 `*`）
- [ ] `TRUSTED_INTERNAL_HOSTS` 仅包含真实内网主机（如有内网 SMTP/网关）

---

## 八、Stage 7 — HTTPS / 反向代理 ⚠️ NOT VERIFIED（⛔ BLOCKING）

**本次验收环境无域名、无证书、无 Docker，本节全部项目无法验证。**

已完成静态审查（**不能替代实测**）：

- [x] `nginx.tls.conf` 含 TLS 配置块
- [x] 安全响应头（`X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy`）
- [x] `/nginx-health` 直答，不回落 SPA `index.html`
- [x] 静态资源 `expires` + 不可变缓存

必须在目标环境执行（⛔ 全部 BLOCKING）：

- [ ] 域名 DNS 解析到服务器
- [ ] TLS 证书链完整且未过期（`openssl s_client -connect <domain>:443 -servername <domain>` 验证）
- [ ] HTTP (80) 自动跳转 HTTPS (443)
- [ ] HSTS 响应头生效
- [ ] TLS 配置符合当前主流浏览器要求（TLS 1.2+，禁用弱套件）
- [ ] `X-Forwarded-For` 正确传递，客户端 IP 未被伪造
- [ ] `bash scripts/healthcheck.sh` 全部 ✅
- [ ] 证书自动续期已配置（certbot / cloud LB）

```bash
openssl s_client -connect radar.example.com:443 -servername radar.example.com
curl -I http://radar.example.com/            # 期望 301/308 → https
curl -I https://radar.example.com/           # 期望 200 + HSTS
bash scripts/healthcheck.sh
```

---

## 九、Stage 8 — 测试 / CI / 构建（本次已通过）

- [x] 后端 387 passed / 3 deselected
- [x] 前端构建成功
- [x] CI 工作流落地（backend + frontend）
- [x] Alembic 单 head
- [x] CI 中 real-network 测试为非阻塞项

**目标环境确认**（📋 建议）：

- [ ] 首次 CI 运行全绿
- [ ] `npm audit` 门禁在 CI 中未阻断（当前仅 2 项 moderate，低于 high 阈值）

---

## 十、Stage 9 — 备份与恢复（⛔ BLOCKING：PG 路径未验证）

### 本次已实测（SQLite 路径）

- [x] 真实备份 → 破坏 → 恢复演练
- [x] 恢复后 SHA-256 与基线**完全一致**
- [x] 11 张表行数逐表比对通过
- [x] 被删除的行成功回滚（证明是时间点快照）
- [x] 3 项永久回归测试

实测输出：
```
RESULT: PASS
  rows restored           : 75
  tables verified         : 11
  sha256 match            : yes
  deleted rows rolled back: 3 (in crawl_logs)
```

### 必须在目标环境执行（⛔ BLOCKING）

- [ ] **`bash scripts/backup.sh` 成功生成 `backups/radar_*.sql.gz`**
- [ ] **备份文件大小合理（非 0 字节）**
- [ ] **`bash scripts/restore.sh <dump>` 在目标库上成功恢复**
- [ ] 恢复后行数与备份时一致（用 `SELECT COUNT(*)` 逐表核对）
- [ ] 恢复后应用可正常启动并读取数据
- [ ] **定时备份已配置**（cron / systemd timer），且已在 `backups/` 中看到历史文件
- [ ] **保留策略生效**（最近 14 份，更早的已清理）
- [ ] **备份文件存放在与容器不同的介质/对象存储**（容器重建不丢失）
- [ ] 备份文件权限受限（仅运维可读，含数据库全量数据）

```bash
bash scripts/backup.sh
ls -lh backups/
bash scripts/restore.sh backups/radar_YYYYMMDD_HHMMSS.sql.gz   # 需输入 RESTORE 确认
```

---

## 十一、Stage 10 — 24 小时运行验收 ⚠️ NOT VERIFIED（⛔ BLOCKING）

**本次验收在单次会话内完成，无法进行真实 24h 连续观测。**

替代证据（**不构成 24h 验证**）：
- 测试套件连续运行 11 分 27 秒无泄漏告警
- 多进程竞争下租约状态始终一致
- 分层健康检查：`/health/live`（进程）、`/health/ready`（DB，异常诚实 503）、`/health/scheduler`（租约真实性）

必须在预发/生产环境执行（⛔ 全部 BLOCKING）：

- [ ] 连续运行 ≥24 小时，无重启、无 OOM
- [ ] 内存曲线平稳（无持续增长趋势）
- [ ] 调度器租约续约成功率 ≥99%
- [ ] 数据库连接池无耗尽
- [ ] 爬取任务成功率达标
- [ ] 错误日志聚类无高频未处理异常
- [ ] `/health/ready` 全程 200（DB 异常时诚实 503，不得静默假健康）
- [ ] `/health/scheduler` 全程反映真实租约持有者

---

## 十二、运维交接（📋 建议）

- [ ] `README.md` / `DEPLOY.md` 与目标环境一致
- [ ] `docs/LIMITATIONS.md` 已更新本次已知限制
- [ ] 管理员初始密码已轮换
- [ ] 数据库备份异地副本已就绪
- [ ] 监控告警接入（Sentry / Prometheus / 云监控）
- [ ] 值班响应流程明确

---

## 十三、v1.5 审计轮新增的 BLOCKING 复验项（⛔ 全部 BLOCKING）

本轮（v1.5，审计基线 commit `a4d7124`）修复了 **3 项 P0 + 4 项 P1**，全部有回归测试锁定并通过。
但以下 3 项在当前环境**客观无法验证**（无 Docker 守护进程；PostgreSQL 的 Windows 二进制在本主机
`initdb` 阶段崩溃于 `ucrtbase.dll` / `0xC0000409`，两套独立构建同样复现），必须在目标环境复验。

### B1 — Docker 镜像构建与容器编排（⛔ BLOCKING）

```bash
bash scripts/deploy.sh
```

必须全部满足：

- [ ] `docker compose build` 三个镜像构建成功（api / web / db）
- [ ] `migrate` 一次性服务 **exit 0**（`docker compose ps migrate` 显示 `exited (0)`）
- [ ] 迁移失败时 `api` 与 `worker` **根本不会启动**（`service_completed_successfully` 生效）
- [ ] `api` 与 `worker` 均进入 **healthy**（不是仅 `Up`）
- [ ] `curl http://localhost/api/health/live` 与 `curl http://localhost/api/health/ready` 返回 200
      （注意：**不可**用 `/health/live`，该路径落到 SPA 的 `location /`，恒返回 index.html）
- [ ] 数据库口令含 `@ : / % + #` 与空格时，migrate / api / worker **连到同一个库**且能成功登录

### B2 — 真实 PostgreSQL 上的调度锁 / fencing 复验（⛔ BLOCKING）

本轮的多进程竞争测试跑在 SQLite 上；PostgreSQL 的锁语义不同，必须复跑：

```bash
cd backend
pytest tests/test_scheduler_lock_stress.py tests/test_lease_fencing.py -v
```

必须全部满足：

- [ ] 多进程竞争只有一个胜者，失败者无法心跳、无法通过 fencing
- [ ] 租约过期被接管后，旧 Leader 的**下一次业务写入被 fence**（不得产生重复事件 / 重复通知）
- [ ] 旧 Leader 恢复后无法续约、无法提交
- [ ] 数据库短暂不可用时不写库

### B3 — 真实 PostgreSQL 上的凭据与迁移端到端复验（⛔ BLOCKING）

```bash
cd backend
pytest tests/test_db_connection_wire.py -v
python -m app.migrate        # 指向真实 PostgreSQL
```

必须全部满足：

- [ ] 口令 `P@ss:w/rd%+er#2026 x` 能成功连接真实 PostgreSQL
- [ ] `ALTER USER ... WITH PASSWORD '<含特殊字符口令>'` 后，驱动以同一口令成功认证
- [ ] 迁移链在真实 PostgreSQL 上全部执行成功
- [ ] `create_engine()` 不再因重复关键字参数而失败（本轮 P0 的直接验证）

---

## 十四、最终放行判定

| 阶段 | 状态 | 阻断发布 |
|---|---|---|
| 1 代码审计 | ✅ 通过 | 否 |
| 2 部署配置（静态） | ✅ 通过 | 否 |
| 2 Docker 实测 | ⚠️ NOT VERIFIED | **是** |
| 3 SSRF（本地） | ✅ 通过 | 否 |
| 3 SSRF（真实 DNS） | ⚠️ NOT VERIFIED | **是** |
| 4 调度锁 | ✅ 通过 | 否 |
| 5 业务完整性（本地） | ✅ 通过 | 否 |
| 5 真实源与真实 AI | ⚠️ NOT VERIFIED | **是** |
| 6 安全（代码 + 依赖） | ✅ 通过 | 否 |
| 6 生产密钥配置 | ⚠️ NOT VERIFIED | **是** |
| 7 HTTPS / 反代 | ⚠️ NOT VERIFIED | **是** |
| 8 测试 / CI / 构建 | ✅ 通过 | 否 |
| 9 备份恢复（SQLite） | ✅ 通过 | 否 |
| 9 备份恢复（PostgreSQL） | ⚠️ NOT VERIFIED | **是** |
| 10 24h 运行 | ⚠️ NOT VERIFIED | **是** |
| v1.5 B1 Docker 编排实测 | ⚠️ NOT VERIFIED | **是** |
| v1.5 B2 真实 PostgreSQL 调度锁复验 | ⚠️ NOT VERIFIED | **是** |
| v1.5 B3 真实 PostgreSQL 凭据与迁移 | ⚠️ NOT VERIFIED | **是** |

### 判定结论

**当前状态：CONDITIONAL READY（有条件放行）**

- v1.5 审计轮：修复 **3 项 P0 + 4 项 P1**（含 1 项此前从未被任何测试暴露的 P0 生产启动阻断缺陷
  `app/database.py`），**未修复的 P0/P1 = 0**，不阻断发布；新增 6 个测试文件、244 项回归测试
- 全量测试套件 **598 passed / 3 deselected / 0 failed**（退出码 0）；干净环境后端与前端构建退出码 0；
  迁移 / 启动 / 故障恢复冒烟 `rc=0`；`pip-audit` 零漏洞
- **NOT VERIFIED 的原因是环境客观限制**（无 Docker 守护进程；PostgreSQL Windows 二进制在本主机
  `initdb` 崩溃），**不是"未发现问题"**
- 上述标 **BLOCKING** 的项目全部通过后，本版本可升级为 **READY**

### 放行签字

| 项目 | 结论 | 签字 | 日期 |
|---|---|---|---|
| 代码审计 | | | |
| 部署配置 | | | |
| 安全 | | | |
| 备份恢复 | | | |
| 运行验收 | | | |
| **最终批准** | ⛔ CONDITIONAL READY | | |
