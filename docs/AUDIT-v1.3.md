# AI Event Radar v1.3 — 最终安全加固审计与部署验收报告

- 仓库：https://github.com/alexandresilksail/AI-Event-Radar
- 提交：`362015f`（v1.3，远端已验证一致）
- 执行时间：2026-10-20
- 执行环境：Windows 本机（Python 3.13.12 托管 venv、Node 22.22.2、SQLite 隔离测试库）
- 本机无 Docker：镜像构建 / Compose 集成测试标记为 **BLOCKED**，以替代验证说明（见 §6）

问题状态总览：**无 NOT_FIXED**。全部 P0/P1 已修复；其中 2 项依赖 Docker 的集成验证受外部环境限制。

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `backend/app/security.py` | **重写**：IPv4/IPv6 全地址族分类、严格 URL 解析、DNS 多地址全校验、端口白名单 |
| `backend/app/collectors/base.py` | **重写** safe_get：逐跳 SSRF 校验、超时分离、大小上限、线程局部并发信号量 |
| `backend/app/config.py` | 幂等 `encode_db_url`、扩展生产校验（DB 密码/ALLOWED_ORIGINS）、ALLOWED_PORTS、AUTO_MIGRATE、WORKER_HEALTH_PORT |
| `backend/app/migrate.py` | **新建**：独立一次性迁移入口，跨进程互斥 + 僵死 pid 守卫接管 |
| `backend/app/services/scheduler.py` | **重写**：原子 CAS 租约 + lease_version fencing + 续租失败停调度 |
| `backend/app/models.py` | SchedulerLock 增加 `lease_version` 列 |
| `backend/alembic/versions/888363d8d20c_*.py` | **新建**：lease_version 增量迁移（server_default 保老库） |
| `backend/app/main.py` | 迁移仅在 `AUTO_MIGRATE=false` 的开发环境自动执行 |
| `backend/app/worker.py` | **重写**：内置 stdlib 分层健康服务（守护线程、可退出） |
| `backend/app/routers/health.py` | **重写**：live/ready/scheduler 三类探针 |
| `Dockerfile.api` / `Dockerfile.web` | 角色感知健康检查；Web 改 `/nginx-health` 真实静态路径 |
| `docker-compose.prod.yml` | 新增一次性 `migrate` 服务；depends_on 真实条件（db healthy → migrate 成功 → api/worker → web） |
| `nginx.prod.conf` / `nginx.tls.conf` | 健康检查路径；新增 TLS 变体（HTTP→HTTPS 301、HSTS、TLS 终止说明） |
| `scripts/deploy.sh` | 第 7 步改为 migrate-first 并校验迁移服务成功退出 |
| `backend/tests/test_ssrf_final.py` | **新建** 26 项 SSRF 回归测试 |
| `backend/tests/test_lock_migration.py` | **新建** 40 项锁/迁移/编码/部署/健康测试 |
| `backend/tests/test_real_e2e.py` | 修正对齐 v1.2 partial 语义（自包含、不弱化断言） |
| `README.md` / `docs/API.md` / `docs/DEPLOY.md` / `.env.example` | 同步 v1.3 |

---

## 2. 各项问题根因与修复摘要

### P0-1 SSRF 地址分类不完整 → FIXED_AND_TESTED
- **根因**：旧 `is_public_ip` 仅黑名单少量网段，漏 IPv4 映射 IPv6（`::ffff:127.0.0.1` 可绕过）、TEST-NET/保留段、广播/组播；混合 DNS 结果被静默过滤而非拒绝。
- **修复**：基于 `ipaddress` 标准属性的显式 allow 策略（`is_global` 之外显式拒绝 reserved/multicast/loopback/link_local/unspecified/IPv4-mapped-not-public/ULA）；`getaddrinfo` 全记录**任一非公网即拒绝**；URL 经严格解析（拒 userinfo 伪装、歧义 URL、非白名单端口）。

### P0-2 DNS rebinding → FIXED_AND_TESTED
- **根因**：校验与连接之间 HTTP 客户端重新解析域名，竞态可绕过。
- **修复**：双保险——`install_guarded_resolver()` 守护型解析器（socket 层把出站解析约束到已校验地址集）+ 应用层逐跳重新校验（每次重定向都完整重走 URL→DNS→IP 校验）。测试以受控 DNS 替身 + 假传输层（替身亦走 `getaddrinfo`，模拟真实连接行为）验证，未探测任何真实第三方内网。

### P0-3 重定向防护 → FIXED_AND_TESTED
- 手动逐跳跟随：每跳重新校验；协议降级、非标端口、超限（默认 3 跳）、循环一律拒绝并记录原因（测试覆盖 13 个场景）。

### P0-4 资源限制 → FIXED_AND_TESTED
- 连接/读取/总体超时分离、响应体 5MB 上限（流式逐块累计，防 zip-bomb）、重定向次数上限、域名级限速、线程局部并发信号量（修掉 sitemap 嵌套调用可能死锁的问题）、`trust_env` 固定策略。

### P1-5 迁移并发/启动顺序 → FIXED_AND_TESTED（Docker 编排部分 BLOCKED，见 §6）
- **根因**：容器启动命令、API lifespan、Worker 启动都可能执行 `migrate_or_init()`，多实例竞争同一迁移。
- **修复**：生产由独立一次性 `migrate` 服务执行（compose `depends_on: db healthy → migrate service_completed_successfully → api/worker`）；API/Worker 设 `AUTO_MIGRATE=false`，dev 才自动迁移。
- **附带修复三个真实缺陷**（压测发现，非测试问题）：
  1. Windows 上 `os.kill(pid,0)` 对已死 pid 抛 `ERROR_INVALID_PARAMETER` 被当作存活 → 僵死守卫白等 120s；修复为跨平台 `_pid_alive`（Windows: `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess != STILL_ACTIVE`）。
  2. SQLAlchemy 池化连接在 Windows 并发初始化瞬间可被降级为只读且**永久毒化**（busy_timeout 无效）→ guard 操作改用一次性新 engine。
  3. `_ensure_guard_table` 的 DDL 竞态导致 `attempt to write a readonly database` 崩溃 → 幂等 CREATE + 有界重试 + deadline。

### P1-6 调度器领导锁 CAS 竞态 → FIXED_AND_TESTED
- **根因**：原实现先 SELECT 再 INSERT/UPDATE，两个 Worker 可同时判定自己为 Leader（双调度）。
- **修复**：单条原子 CAS（`UPDATE ... WHERE id=1 AND (holder='' OR heartbeat 过期)`，靠 rowcount 判定）；`lease_version` fencing token；续租失败或租约被接管即停调度；`_crawl_job` 派发前 fencing 校验（旧 Leader 丢锁后零副作用，测试验证不产生 CrawlTask）；心跳 daemon 线程随进程退出。

### P1-7 DB URL 双重编码 → FIXED_AND_TESTED
- **根因**：`quote_plus` 作用于完整 URL 字符串，`%40 → %2540`。
- **修复**：`encode_db_url` 幂等——按最后 `@` 分割 userinfo + 启发式判断是否已编码，对**原始**用户名/密码各精确编码一次；非 PG URL 原样透传。生产校验复用同一解析逻辑（拒弱/默认/缺失密码）。

### P1-8 健康检查误报 → FIXED_AND_TESTED
- **根因**：Web 容器检查 `/health/live`，SPA fallback 会用 index.html 应答 → 假阳性。
- **修复**：Web 检查 `/nginx-health`（nginx 独占 location，不经 fallback）；API 分层 `/health/live`（进程）、`/health/ready`（数据库，503 不伪装）、`/health/scheduler`（租约真实性：过期租约 `ok=false`）；Worker 内置健康服务（`WORKER_HEALTH_PORT=8001`，仅内网）。

### P1-9 生产网络边界与 HTTPS → FIXED_NOT_FULLY_VERIFIED
- PG/API/Worker 不映射公网端口；Web 为唯一入口；`nginx.tls.conf` 提供可执行 TLS 方案（证书挂载、`return 301 https://`、HSTS、反代头处理）。**未验证部分**：需在有 Docker/真实域名的主机执行 `docker compose -f docker-compose.prod.yml -f <tls overlay> config` 与实际证书加载（本机无 Docker）。

### 补充：生产 CORS/凭证/日志 → FIXED_AND_TESTED
- 生产缺 `ALLOWED_ORIGINS` 或通配符 → 拒绝启动（测试以子进程隔离验证）；默认 JWT/管理员密码/SQLite 一律拒绝；错误响应不含堆栈；通知凭证 API 打码。

---

## 3. 新增/修改测试清单（66 项）

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `test_ssrf_final.py` | 26 | IP 分类（16 场景字面量）、混合 DNS 拒绝、userinfo/歧义 URL、端口、逐跳重定向（13 场景含循环/降级/超限）、TLS 校验生效、超大响应体中止、超时处理 |
| `test_lock_migration.py` | 40 | 租约获取/续租/过期/接管/fencing（旧 Leader 零副作用）、双 Worker 竞争单赢家、迁移互斥（flock 语义）、僵死 pid 接管、存活 pid 阻止并发、DB URL 编码（9 种特殊字符单层编码 + 已编码不乱码 + 幂等 + sqlite 透传）、生产凭据校验（子进程：强密码接受/弱密码拒绝/缺 origins 拒绝/通配拒绝/sqlite 拒绝）、部署配置断言（compose 顺序、migrate 服务、TLS 文件、Web 健康路径）、健康检查 6 项（live 无副作用、ready 查库、scheduler 诚实性 3 态、worker 内嵌服务 4 端点） |
| `test_real_e2e.py` | 2（修改） | 真实 MIT RSS 采集 + 二次采集幂等（partial 语义对齐 v1.2：0 新增为诚实成功态，不断言弱化） |

---

## 4. 测试命令与结果（真实执行，退出码 0）

```bash
# 全量测试（不含真实网络）
pytest tests/ -m "not real_network" -q -p no:randomly
# → 166 passed, 3 deselected, 479 warnings in 164.17s   ✅

# 真实网络 E2E（连续 3 次确认稳定）
pytest tests/ -m real_network -q          # → 2 passed ✅（x3）

# SSRF 专项
pytest tests/test_ssrf_final.py -q        # → 26 passed ✅

# 锁/迁移/编码/部署/健康专项（连续 5 轮全文件 + 20 轮并发压测）
pytest tests/test_lock_migration.py -q    # → 41 passed ✅（5/5 轮）
python /tmp/repro_conc2.py                # → 20 轮双进程并发迁移 20/20 ok ✅

# 全模块导入检查
python -c "walk_packages(app)"            # → modules OK ✅

# 迁移链验证（新建隔离库）
alembic upgrade head                      # → 3 steps ✅
alembic downgrade base                    # → 3 steps ✅
alembic upgrade head                      # → 3 steps，lease_version 列在 ✅
alembic history                           # → 单一 head 888363d8d20c，线性 3  revision ✅

# 前端生产构建
npm run build                             # → ✓ built in 6.00s（234KB JS / 16.6KB CSS）✅

# 本机服务联调（v1.3 新代码）
GET /status /health/live /health/ready /health/scheduler → 200 ✅
前端 http://localhost:5173 → 200，/api 代理 200 ✅
```

---

## 5. 分项验收结果

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | SSRF 防护（分类/混合DNS/重定向/大小/超时） | ✅ | 26 项测试 + 守护解析器 |
| 2 | DNS rebinding | ✅ | 全地址校验 + 替身走真实 getaddrinfo 的 hop 间测试 |
| 3 | 迁移并发（空库/升级/双进程/僵死接管） | ✅ | 20/20 压测 + 6 项专项；老库 `804223700e45 → head` 增量升级不丢数据 |
| 4 | 多 Worker 调度（单 Leader/接管/竞态） | ✅ | CAS rowcount + fencing 测试；连续 5 轮无双 Leader |
| 5 | DB 特殊字符密码（@ : / % + # 空格） | ✅ | 9 种密码单层编码断言；已编码输入不乱码；sqlite 透传 |
| 6 | 健康检查（live/ready/scheduler/worker/web） | ✅ | 6 项测试；过期租约诚实报 `ok=false` |
| 7 | 编排/镜像/Docker 集成 | ⚠️ BLOCKED | 本机无 Docker；compose 配置通过静态断言测试验证（depends_on 条件、migrate 服务、TLS 文件、无公网 DB 端口）。**需在有 Docker 主机执行 `bash scripts/deploy.sh` 复核** |
| 8 | 24 小时稳定性 | ⚠️ 未验证 | 短时证据：20 轮并发压测、5 轮全文件、3 轮真实 E2E 均稳定，**不等同于 24h** |

---

## 6. BLOCKED 项与替代验证说明

1. **Docker 镜像构建 / Compose 集成测试 / ARM64 验证**：本机无 Docker Engine。替代验证：compose 结构以测试断言逐项核对（`test_lock_migration.py::TestDeploymentConfig`：db healthy 条件、migrate `service_completed_successfully`、api/worker/web 顺序、PG 无公网端口、Web 健康检查不经 SPA fallback、nginx.tls.conf 存在且含 301/TLS 指令）。在有 Docker 的主机按 `docs/DEPLOY.md` 执行 `bash scripts/deploy.sh` 即可完成剩余验证。
2. **HTTPS 实际加载**：需真实域名与证书；`nginx.tls.conf` 已就绪，验证命令已写入 `docs/DEPLOY.md`。
3. **PostgreSQL 实连测试**：本机无 PG 实例；编码测试覆盖字符串层全部边界（含已编码幂等），PG 实连建议在部署环境用 `psql` + 特殊字符密码复核一次。

---

## 7. 生产部署步骤与回滚

```bash
# 部署（docker 主机）
cp .env.example .env      # ENV=production + JWT_SECRET(>=32) + ADMIN_PASSWORD(>=12) + DB_PASSWORD + ALLOWED_ORIGINS
bash scripts/deploy.sh    # 检查→构建→db healthy→migrate→api/web→health→worker→seed→完成

# 回滚
git checkout d9210f0 -- . && bash scripts/deploy.sh   # 或 docker compose ... down 后按上一 tag 重建
bash scripts/backup.sh                                # 变更前备份（radar_YYYYmmdd_HHMMSS.sql.gz）
bash scripts/restore.sh <file>                        # 恢复（需输入 RESTORE 确认）
```
数据库仅前向兼容：`alembic downgrade` 已验证可用（生产回滚优先以备份恢复为准）。

---

## 8. 结论

- P0（SSRF 与出站安全）：**全部 FIXED_AND_TESTED**（26 项回归 + 守护解析器 + 逐跳校验）。
- P1（迁移并发、调度租约/fencing、DB URL 编码、健康检查、CORS/凭证/日志）：**全部 FIXED_AND_TESTED**。
- P1（Docker/HTTPS 部署集成）：**FIXED_NOT_FULLY_VERIFIED / BLOCKED**（无 Docker 环境，替代验证已说明）。
- 24h 稳定性：**未验证**（有短时压测证据，不宣称通过）。
- 无功能删除、无数据清空、无测试弱化（唯一测试修改是把 E2E 断言对齐 v1.2 已发布的 partial 语义，并改为自包含）。
- **可以进入生产验收前的最后一步：在有 Docker 的主机执行 `bash scripts/deploy.sh` 全流程复核。**
