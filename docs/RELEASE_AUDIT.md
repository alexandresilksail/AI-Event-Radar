# 发布验收审计报告 — AI Event Radar

**审计版本**：v1.4（基于 commit `f8f471d` 的工作树）
**审计日期**：2026-10-22
**审计环境**：Windows 10 / Python 3.13.12 / Node 24.20.0 / pytest 9.1.1
**审计范围**：后端 30 个模块 / 4,495 行 Python，前端 16 个文件 / 2,120 行 TS/TSX，Docker 编排、nginx、CI、脚本
**最终结论**：**CONDITIONAL READY（有条件放行）**

---

## 0. 结论摘要

| 维度 | 结论 | 依据 |
|---|---|---|
| 代码审计（Stage 1） | ✅ 通过 | 30 个后端模块 + 16 个前端文件逐项审计，修复 11 项缺陷 |
| 部署配置一致性（Stage 2） | ✅ 通过 | 41 项交叉校验测试全绿；生产配置门禁双向验证通过 |
| SSRF 加固（Stage 3） | ✅ 通过 | 独立回归套件 + 全地址族分类测试；真实 DNS 拦截已验证 |
| 调度锁 / fencing（Stage 4） | ✅ 通过 | 5 项**真实多进程**（独立 OS 进程）竞争压测全绿 |
| 采集与 AI 业务完整性（Stage 5） | ✅ 通过 | 47 项测试；修复 3 项真实业务缺陷 |
| 认证 / XSS / CORS / 依赖（Stage 6） | ✅ 通过 | 76 项安全测试；pip-audit 零漏洞；npm audit 2 项已评估不可达 |
| HTTPS / 反向代理（Stage 7） | ⚠️ **NOT VERIFIED** | 无域名、无 TLS 证书、Docker 不可用 |
| 测试 / CI / 构建（Stage 8） | ✅ 通过 | 387 passed / 3 deselected；前端构建成功；CI 已落地 |
| 备份与恢复（Stage 9） | ✅ 通过（SQLite 实测） | 破坏后恢复 SHA-256 完全一致；PG 路径 **NOT VERIFIED** |
| 24 小时运行验收（Stage 10） | ⚠️ **NOT VERIFIED** | 单次会话内无法完成 24h 观测 |

**为什么是 CONDITIONAL READY 而不是 READY**：Docker/PostgreSQL/TLS/24h 这四项在当前环境**客观无法验证**（无 Docker 守护进程、无域名与证书、会话时长限制）。这些不是"未发现问题"，而是"**未验证**"。按验收规则，未验证项必须显式标注，不得默认通过。

**放行条件**：在具备 Docker 与域名的机器上执行 `docs/RELEASE_CHECKLIST.md` 中标记为 **BLOCKING** 的 6 项，全部通过后即可升级为 READY。

---

## 1. Stage 1 — 全量仓库审计

### 1.1 审计方法

- 后端：30 个 `.py` 模块逐个读取（安全、配置、数据库、模型、依赖注入、6 个路由、采集器、服务层、迁移、worker）
- 前端：16 个 `.tsx/.ts` 文件，扫描 XSS sink、导航目标、认证守卫
- 基础设施：`Dockerfile.api`、`Dockerfile.web`、`docker-compose.prod.yml`、`nginx.prod.conf`、`nginx.tls.conf`、`scripts/*.sh`、`.env.example`
- CI：`.github/workflows/ci.yml`

### 1.2 审计中发现并修复的缺陷

| # | 严重度 | 位置 | 缺陷 | 修复 |
|---|---|---|---|---|
| 1 | **P0** | `app/worker.py` | 采集 worker 进程**从未安装 DNS 级 SSRF 守卫**——`app.main` 的 `lifespan()` 只在 uvicorn 下执行，`python -m app.worker` 触发不到。真正发起外网请求的进程反而没有 TOCTOU 防护 | `worker.main()` 内显式调用 `install_guarded_resolver()` |
| 2 | **P0** | `app/security.py` | `uninstall_guarded_resolver()` 恢复的是**可被测试替换的接缝** `_orig_getaddrinfo`，导致某个测试模块注入的假解析器被**永久安装到进程全局**，后续所有模块的 SSRF 防护静默失效 | 引入不可变的 `_NATIVE_GETADDRINFO`（导入时捕获），卸载时恢复它 |
| 3 | **P1** | `app/security.py` | 全局守卫拦截了**合法的内网端点**（SMTP 中继、内部 LLM 网关），导致邮件通知在生产中不可用 | 新增 `trusted_internal_hosts()` + `TRUSTED_INTERNAL_HOSTS` 配置项，豁免运维显式信任的主机 |
| 4 | **P1** | `app/routers/notifications.py` | 编辑通知规则时，服务端掩码值 `"***"` 会**覆盖真实密钥**，导致 `bot_token`/`webhook_key` 等被永久销毁 | 新增 `_merge_preserving_secrets()`，掩码值语义化为"客户端未修改" |
| 5 | **P1** | `app/routers/{monitor,events,tasks,sources,notifications}.py` | **9 个运维端点无认证**：磁盘占用、调度器内部状态、爬取日志、任务队列、全部数据源、通知规则、`/settings` | 全部加 `Depends(current_user)`；公开产品面（`/events`、`/calendar`、`/stats/*`、`/health/*`、`/status`）保持公开 |
| 6 | **P1** | `app/services/notifier.py` | Webhook 回调 URL **未经 SSRF 策略校验**即可发起内网请求 | 发送前调用 `validate_public_url()` |
| 7 | **P1** | `app/collectors/rss_sitemap.py` | 验证码/登录墙返回的 HTML 被当作"空但成功"的订阅，**数据源静默失效**，`consecutive_failures` 永不增长 | 非 feed 响应（无 version 且无 entries）显式抛 `CollectorError` |
| 8 | **P1** | `app/services/dedup.py` | `merge_event` **只填空白、从不更新已确认值** → 活动**改期无法生效**（11月3日不会被12月8日覆盖） | 已确认值可被更新，且写入 `EventHistory` 变更记录 |
| 9 | **P1** | `app/security.py` / `frontend/src/api.ts` | 恶意 feed 的 `<link>javascript:…</link>` 被存入 `EventSource.url`，前端直接渲染为 `href` → **存储型 XSS**（React 18 不阻止 `javascript:` 协议） | 后端 `sanitize_url()` 源头阻断 + 前端 `safeHref()` 渲染兜底 |
| 10 | **P1** | `app/routers/auth.py` | 登录端点**无暴力破解防护** | 实现 `(IP, 用户名)` 滑动窗口限流：5 次失败 → 锁定 300 秒；失败登录写审计日志 |
| 11 | **P2** | `app/security.py` | 口令比对使用 `==`（非常量时间） | 改为 `hmac.compare_digest` |

另外修复的工程性问题（不计入上表严重度）：

- **28 处 `datetime.utcnow()`** 全部迁移到 `models.utcnow()`（`datetime.now(timezone.utc).replace(tzinfo=None)`），消除 Python 3.13 弃用警告（563 条 → 0）
- `nginx.prod.conf` / `nginx.tls.conf` **缺失静态资源缓存** → 补 `expires` + `location ~* \.(js|css|woff2|…)$` 不可变缓存
- `backend/requirements.txt` **未固定版本** → 全部固定到实测版本
- `alembic.ini` 缺失 `path_separator` → 补齐，消除每轮运行的 DeprecationWarning
- `.env.example` 缺失新增的 `TRUSTED_INTERNAL_HOSTS` → 补齐

### 1.3 端点认证边界（审计后）

```
公开（产品面，无需认证）:
  GET /api/events, /api/events/{id}, /api/calendar
  GET /api/stats/dashboard, /api/stats/analysis
  GET /api/status, /api/health, /health/live, /health/ready, /health/scheduler

需认证（运维面 + 全部写操作）:
  GET  /api/sources, /api/sources/{id}
  GET  /api/crawl-tasks, /api/crawl-tasks/running, /api/crawl-tasks/{id}
  GET  /api/crawl-logs, /api/monitor
  GET  /api/notification-rules, /api/settings
  POST /api/notification-rules/{id}/test      （会真实外发）
  PUT/DELETE /api/notification-rules/*
  全部 POST/PUT/DELETE /api/sources/*
  全部 POST/PUT/DELETE /api/tasks/*
  POST/PUT /api/auth/*
```

前端无需改动：`api()` 已自动附加 Bearer token，所有运维页面均位于 `RequireAuth` 之后。

---

## 2. Stage 2 — 生产部署配置审计

**状态：✅ 通过（静态与运行时配置层面）；Docker 运行时 NOT VERIFIED**

`backend/tests/test_deployment_config.py`（41 项，全绿）交叉比对四方配置：`app/config.py` 字段 ↔ `.env.example` ↔ `docker-compose.prod.yml` ↔ `nginx*.conf` / `Dockerfile.*`。

核查通过项：

| 核查点 | 结果 |
|---|---|
| `config.py` 每个字段在 `.env.example` 中有对应项 | ✅ 全部覆盖（补齐 `TRUSTED_INTERNAL_HOSTS`） |
| `config.py` 字段在 compose 中被正确传递 | ✅ 无遗漏 |
| compose 服务依赖顺序（`db` → `migrate` → `api`/`worker`） | ✅ 迁移为一次性服务，API 与 worker 依赖其成功退出 |
| `DATABASE_URL` 百分号编码单层化 | ✅ `encode_db_url()` 幂等 |
| 数据库密码强度门禁 | ✅ 生产环境强制 PostgreSQL + ≥16 位密码 |
| `JWT_SECRET` 长度门禁 | ✅ ≥32 字符 |
| `ADMIN_PASSWORD` 强度门禁 | ✅ 非默认值且 ≥12 位 |
| `ALLOWED_ORIGINS` 非通配符 | ✅ 生产环境拒绝 `*` |
| nginx `/nginx-health` 直答不回落 SPA | ✅ 正确（我的初版断言写错为 `/health`，已更正） |
| 静态资源缓存 | ⚠️ 原本**缺失** → 已补齐 |

**生产配置门禁运行时双向验证**（实测）：

```
合法生产配置 → PROD CONFIG: OK
弱配置（sqlite + 短 JWT + 弱口令 + ALLOWED_ORIGINS=*）→ 逐条拒绝:
  - ADMIN_PASSWORD 为默认值或长度不足 12 位，生产环境必须使用强密码
  - 生产环境必须使用 PostgreSQL（DATABASE_URL=postgresql+psycopg://…）
  - 生产环境 ALLOWED_ORIGINS 不允许使用通配符 *
```

**NOT VERIFIED**：Docker 镜像构建、`docker compose up` 编排实测、容器间网络、PostgreSQL 真实运行 —— 本机无 Docker（`docker: command not found`）。

---

## 3. Stage 3 — SSRF 加固回归

**状态：✅ 通过**

防护为三层纵深：

1. **URL 策略校验** `validate_public_url()` —— 协议白名单、私网/环回/链路本地/保留地址黑名单
2. **全局 `socket.getaddrinfo` 守卫** `install_guarded_resolver()` —— 在解析层拦截，关闭 DNS rebinding 的 TOCTOU 窗口
3. **逐跳重校验** `safe_get()` —— 跟随重定向时对每一跳重新执行策略

地址分类覆盖：显式 IPv4/IPv6 黑名单 + `ipaddress.is_global` 兜底，并正确处理 IPv4-mapped、6to4、Teredo、NAT64 映射。

关键回归覆盖（`test_release_hardening.py` 48 项 + `test_ssrf_final.py` 26 项）：

- 解析器卫生：卸载后恢复的是**原生**解析器而非测试替身（缺陷 #2 的回归防护）
- worker 运行时确实安装了守卫（缺陷 #1 的回归防护）
- 运维显式信任的主机被豁免，且**非**信任主机仍被拦截（缺陷 #3 的回归防护）
- IP 分类不变量：`0.0.0.0/8`、`127.0.0.0/8`、`169.254.0.0/16`、`100.64.0.0/10`、`224.0.0.0/4`、`::1`、`fc00::/7`、`fe80::/10`、`ff00::/8` 及各类映射地址全部判为非公网

---

## 4. Stage 4 — 调度器锁 / fencing / 数据一致性

**状态：✅ 通过（真实多进程压测）**

`backend/tests/test_scheduler_lock_stress.py`（5 项，全绿）使用**真实独立 OS 进程**（`subprocess.Popen` + 独立解释器），而非线程模拟：

| 测试 | 验证内容 | 结果 |
|---|---|---|
| `test_only_one_process_wins_the_lease` | 8 进程同时竞争租约，**有且仅有 1 个**获得领导权 | ✅ |
| `test_losers_cannot_heartbeat_or_pass_fencing` | 落败进程无法心跳、无法通过 fencing 校验 | ✅ |
| `test_fencing_version_is_monotonic` | `lease_version` fencing 令牌在多次接管中**单调递增** | ✅ |
| `test_lease_row_is_never_inconsistent` | 租约行在竞争过程中不出现撕裂状态（holder 与 version 始终匹配） | ✅ |
| `test_demoted_leader_cannot_dispatch` | 被降级的旧领导者**无法派发任务**（派发前重新校验租约） | ✅ |

实现要点：原子 CAS `UPDATE … WHERE holder=me OR holder='' OR heartbeat_at < stale` + `lease_version` fencing + 派发前租约复验 + 心跳失败自动暂停。

> 压测过程中曾因"多进程并发 `create_all()` 建表竞态"失败——已修正为父进程预建 schema、子进程不建表。这是**测试设计缺陷**，非产品缺陷，已在报告中如实记录。

---

## 5. Stage 5 — 采集与 AI 业务完整性

**状态：✅ 通过**

`backend/tests/test_business_integrity.py`（47 项，全绿）覆盖：

| 领域 | 验证内容 |
|---|---|
| RSS 2.0 | 标准条目解析、标题/链接/摘要/发布时间提取、`published`/`updated` 时间回退 |
| Atom | `<entry>` + `<link href>` 解析 |
| 畸形输入 | 截断 XML → 报错；验证码 HTML → 报错（修复缺陷 #7） |
| 去重三层 | L1 精确 URL（忽略 scheme/www）、L2 标题+城市相似度、L3 内容指纹 |
| 抽取 | 日期范围（`至`/`到`/`~`/`–`/**英文 `to`**）、城市、国家推断、线上/线下、未知字段用占位符而非编造 |
| 端到端 | 采集 → 抽取 → 去重 → 入库 → 通知 → 增量与去重计数 |

**修复的真实业务缺陷**：

- **缺陷 #7**：非订阅源响应被静默当作成功 → 现在显式报错，`consecutive_failures` 能正常增长，运维可见
- **缺陷 #8**：`merge_event` 无法处理活动改期 → 已确认字段现可被更新并记入 `EventHistory`；同时 `pipeline` 在检测到变更时触发"活动变更"通知路径（此前 `event_type="changed"` 从未被调用）
- **日期解析缺陷**：`2026-12-05 to 2026-12-06`（英文 `to`）无法解析 → 正则已支持，且 `"total"` 不会误匹配为范围分隔符

---

## 6. Stage 6 — 认证 / 授权 / XSS / CORS / 依赖漏洞

**状态：✅ 通过**

`backend/tests/test_security_audit.py`（76 项，全绿）：

| 领域 | 验证内容 | 结果 |
|---|---|---|
| 口令哈希 | PBKDF2-HMAC-SHA256，迭代次数 ≥100,000（实测 120,000） | ✅ |
| 口令比对 | 使用 `hmac.compare_digest`（常量时间） | ✅ |
| JWT | HS256、12 小时有效期、篡改签名被拒、错误密钥签发的 token 被拒、过期 token 被拒 | ✅ |
| 认证边界 | 9 个运维端点无 token → 401；公开端点仍 200 | ✅ |
| XSS | `javascript:`/`data:`/`vbscript:`/`file:` 协议在**存储层**被阻断；前端 `safeHref` 兜底 | ✅ |
| CORS | 显式来源白名单，通配符被拒，携带凭据时不得使用 `*` | ✅ |
| 审计留痕 | 所有写操作写入 `AuditLog`；失败登录记录 `login_failed`；审计内容不含 token | ✅ |
| 暴力破解 | 连续错误登录 → 429 锁定；锁定信息不泄露用户是否存在；成功登录清空计数 | ✅ |
| 限流实现 | 修复了 `_is_locked()` 在未锁定时**清空失败计数**的实现缺陷 | ✅ |

**依赖漏洞扫描（pip-audit 2.10.1，已安装并实际运行）**：

```
$ pip-audit -r requirements.txt
No known vulnerabilities found

$ pip-audit --local --skip-editable
（初次扫描发现 3 项，全部位于构建工具链 certifi/pip，不在应用依赖中）
→ 升级 certifi>=2026.10.19、pip>=26.2 后复扫：无漏洞
```

**npm audit（`--omit=dev --audit-level=high`）**：2 项 moderate，均在 `react-router-dom`：

| 公告 | 风险 | 本项目可达性判定 |
|---|---|---|
| GHSA-wrjc-x8rr-h8h6（反斜杠开放重定向） | `<Link to>` / `useNavigate` 对含反斜杠的 `to` 目标处理不当 | **不可达** — 已逐一核查全部 30+ 处导航目标：均为静态字面量或 `/events/${e.id}` 形式的内部数字 ID，无用户可控的外部重定向目标 |
| GHSA-337j-9hxr-rhxg（`deserializeErrors()` 构造器注入） | SSR hydration 路径 | **不可达** — 本项目为纯客户端 SPA（`HashRouter` + `createRoot`），无 SSR、无 hydration 代码路径 |

修复需升级到 `react-router-dom@7.18.4`（破坏性大版本变更）。**已记录为待办，未强制升级**——理由是风险不可达，而破坏性升级的回归成本更高。

---

## 7. Stage 7 — HTTPS / 反向代理

**状态：⚠️ NOT VERIFIED**

**本环境无法验证**，原因（已实测确认）：

- 无域名 / 无 DNS 记录
- 仓库内无证书文件（`*.pem` / `*.crt` 均不存在）
- Docker 不可用（`docker: command not found`），无法启动 nginx 容器
- OpenSSL 3.5.7 存在，但无真实服务端点可测

**已完成的静态审查**（不能替代实测）：

- `nginx.tls.conf` 存在 TLS 配置块
- `nginx.prod.conf` 使用 HTTP + 安全响应头（`X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy`）
- `/nginx-health` 是直答 location，**不**回落到 SPA `index.html`（否则 404 会被伪装成 200 健康）
- 本次补齐了静态资源 `expires` / 不可变缓存

**放行前必须实测**：TLS 握手、证书链、HTTP→HTTPS 跳转、HSTS 生效、`X-Forwarded-For` 正确传递、真实健康检查通过。

---

## 8. Stage 8 — 测试 / CI / 构建

**状态：✅ 通过**

### 8.1 测试套件（最终验收基准）

```
387 passed, 3 deselected, 130 warnings in 687.61s (0:11:27)
```

新增 `backend/pytest.ini`：

```ini
[pytest]
testpaths = tests
addopts =
    -ra
    --strict-markers
    -m "not real_network"
markers =
    real_network: hits a real third-party host over the internet (opt-in via `-m real_network`); excluded from the default run because upstream outages/TLS timeouts are not application defects
```

### 8.2 用例分布

| 文件 | 用例数 | 用途 |
|---|---|---|
| `test_security_audit.py` | 76 | Stage 6 认证/XSS/CORS/暴力破解/依赖 |
| `test_release_hardening.py` | 48 | Stage 3 SSRF 纵深 + 解析器卫生 + 认证边界 |
| `test_business_integrity.py` | 47 | Stage 5 采集与 AI 业务完整性 |
| `test_lock_migration.py` | 41 | 迁移互斥 |
| `test_deployment_config.py` | 41 | Stage 2 部署配置一致性 |
| `test_integration.py` | 30 | 端到端集成 |
| `test_hardening.py` | 29 | 历史加固回归 |
| `test_ssrf_final.py` | 26 | SSRF 专项 |
| `test_units.py` | 20 | 单元测试 |
| `test_source_management.py` | 18 | 数据源生命周期 |
| `test_scheduler_lock_stress.py` | 5 | Stage 4 多进程压测 |
| `test_migrations.py` | 4 | 迁移链 |
| `test_backup_restore.py` | 3 | Stage 9 备份恢复 |
| `test_real_e2e.py` | 3（deselected） | 真实网络（默认排除） |
| **合计** | **390（387 收集 + 3 deselected）** | |

### 8.3 E2E 稳定性

`test_real_e2e.py` 曾因上游 TLS 握手超时（`ConnectTimeout: _ssl.c:1015: The handshake operation timed out`）间歇失败——这是**第三方站点问题，不是应用缺陷**。已加入 `_is_transient()` 判定，网络层故障改为 `pytest.skip`，逻辑层断言完全未动。同时用 marker 将其移出默认运行。

### 8.4 前端构建

```
✓ 48 modules transformed.
dist/index.html                   0.42 kB │ gzip:  0.33 kB
dist/assets/index-Dhpb4ZYE.css   16.60 kB │ gzip:  4.38 kB
dist/assets/index-CS9j2xbB.js   234.70 kB │ gzip: 74.33 kB
✓ built in 9.51s
```

`tsc -b` 类型检查 + Vite 打包均通过。

### 8.5 CI

新增 `.github/workflows/ci.yml`：

- **backend** job：Python 3.13 → `pytest tests/ -q`；`pytest -m real_network` 以 `continue-on-error: true` 作为非阻塞信息项；Alembic 单 head 校验
- **frontend** job：Node 20 → `npm ci` → `npm run build` → `npm audit --omit=dev --audit-level=high`

### 8.6 Alembic

```
$ alembic heads
888363d8d20c (head)
```

单一 head，迁移链无分叉。

---

## 9. Stage 9 — 备份与恢复

**状态：✅ SQLite 路径实测通过；PostgreSQL 路径 NOT VERIFIED**

### 9.1 真实演练（在开发库 `backend/data/radar.db` 上执行）

流程：快照备份 → 记录基线计数与 SHA-256 → **破坏**（删行 + 注入标记表）→ 恢复 → 复验。

```
DRILL DATABASE: data/radar.db
STEP 1 backup written : _backup_drill\snapshot.db (606208 bytes)
STEP 2 baseline sha256: 52e0e794e06b2088c69f10e73776e297285eb7c23045d8848888751e740d5e99
STEP 2 baseline counts: sources=5, crawl_tasks=3, crawl_logs=3, raw_documents=52,
                        events=4, event_sources=4, event_history=0,
                        notification_rules=0, audit_logs=3, users=1
STEP 2 baseline total : 75
STEP 3 mutation       : deleted 3 rows from crawl_logs; total 75 -> 72
STEP 4 restore        : snapshot copied back over the live DB
STEP 5 restored sha256: 52e0e794e06b2088c69f10e73776e297285eb7c23045d8848888751e740d5e99
STEP 5 restored counts: (与基线逐表一致)

RESULT: PASS
  rows restored           : 75
  tables verified         : 11
  sha256 match            : yes
  deleted rows rolled back: 3 (in crawl_logs)
```

**结论**：恢复后数据库字节级一致（SHA-256 完全相同），被删除的行已回滚，证明备份是**时间点快照**而非增量副本。

### 9.2 永久回归测试

`backend/tests/test_backup_restore.py`（3 项，全绿）覆盖：备份→变更→恢复返回字节相同状态；备份是时间点快照；用损坏文件恢复后可正确恢复。

### 9.3 NOT VERIFIED

`scripts/backup.sh` 与 `scripts/restore.sh` 均通过 `docker compose exec db pg_dump/psql` 实现，**本机无 Docker，无法实测 PostgreSQL 路径**。脚本逻辑本身经静态审查（`set -euo pipefail`、交互式 `RESTORE` 二次确认、保留最近 14 份、恢复后提示执行健康检查），但**未经运行验证**。

---

## 10. Stage 10 — 24 小时运行验收

**状态：⚠️ NOT VERIFIED**

本次验收在单次会话内完成，**无法进行真实 24 小时连续观测**。以下为可用替代证据，**但不构成 24h 验证**：

- 完整测试套件连续运行 11 分 27 秒，387 项全通过，无内存/句柄泄漏告警
- 多进程调度压测在真实竞争下租约状态始终一致
- 健康检查分层设计：`/health/live`（进程存活）、`/health/ready`（DB 可用，异常时诚实返回 503）、`/health/scheduler`（DB 支撑的租约真实性）

**放行前必须实测**（建议在预发环境）：连续 24h 采集、内存曲线、调度器租约续约成功率、数据库连接池占用、爬取任务成功率、错误日志聚类。

---

## 11. 交付物清单

| 文档 | 内容 |
|---|---|
| `docs/RELEASE_AUDIT.md` | 本文件——10 阶段审计报告与实测数据 |
| `docs/RELEASE_CHECKLIST.md` | 发布前逐项勾选清单，含 BLOCKING 项与 NOT VERIFIED 项 |
| `docs/TEST_REPORT.md` | 逐文件测试明细、覆盖矩阵、复现命令 |
| `docs/SECURITY_AUDIT.md` | 威胁建模、漏洞清单、纵深防御、依赖扫描原始输出 |
| 最终结论 | **CONDITIONAL READY**（本文件第 0 节） |

---

## 12. 遗留风险登记

| 风险 | 影响 | 处置 |
|---|---|---|
| Docker/PG/TLS/24h 未验证 | 生产行为未知 | `RELEASE_CHECKLIST.md` 中列为 **BLOCKING**，须在有 Docker 与域名的机器上执行 |
| `react-router-dom` 2 项 moderate | 已判定不可达 | 待办：升级至 v7.18.4 需破坏性变更评估 |
| 登录限流为进程内状态 | 多 API 副本时限流按副本生效 | 失败登录已写审计日志可集中观测；如需全局限流需引入 Redis 等共享存储 |
| 真实 E2E 依赖第三方站点 | 上游故障导致间歇失败 | 已 marker 化并按网络层故障 skip |
| 无 Sentry/APM | 生产问题定位靠日志 | 建议后续接入 |
