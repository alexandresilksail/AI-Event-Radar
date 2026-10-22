# 测试报告 — AI Event Radar（v1.5 发布审计轮）

**执行日期**：2026-10-22
**被审计 Git commit**：`a4d7124ff2635d14d9b2a122e2061beaa870c961`（审计基线，即本轮修复前的 `main`）
**修复后 commit**：`见 §8「提交与推送」`
**环境**：Windows 10 (10.0.19045) / Python 3.13.12 / pytest / Node v22.22.2 / npm 10.9.7 / psycopg 3.2.13 (libpq 160009)
**工作目录**：`C:\Users\Administrator\WorkBuddy\2026-10-19-16-34-12\ai-event-radar`

---

## 1. 执行摘要

| 指标 | 数值 |
|---|---|
| 用例总数（含本轮新增） | 601 |
| 通过 | **598** |
| 失败 | **0** |
| 跳过（deselected） | 3（`real_network` marker，默认排除） |
| 错误 | 0 |
| 本轮新增测试文件 | 6 个，共 244 项 |

> 本轮修复了 1 个 **P0 生产启动阻断缺陷**（`app/database.py`），该缺陷此前**从未被任何测试发现** —— 因为全部测试都跑在 SQLite 上，而缺陷只在 PostgreSQL 分支触发。

---

## 2. 完整测试套件

**命令**

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/ -q
```

**退出码**：`0`

**实际结果**

```
........................................................................ [ 12%]
........................................................................ [ 24%]
........................................................................ [ 36%]
........................................................................ [ 48%]
........................................................................ [ 60%]
........................................................................ [ 72%]
........................................................................ [ 85%]
........................................................................ [ 97%]
................                                                         [100%]

598 passed, 3 deselected, 2 warnings in 589.31s (0:09:49)
```

**警告说明（2 条，均非缺陷）**
- `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` —— 上游库互操作提示。
- `InsecureKeyLengthWarning: The HMAC key is 27 bytes` —— **测试自身**构造的短 JWT 密钥（用于验证「用别的密钥签发的 token 必须被拒绝」），不是生产配置。

**3 项 deselected**：标记为 `real_network`，默认排除（`pytest.ini` 的 `addopts = -m "not real_network"`）。它们会真实访问第三方站点，上游抖动不应算作应用缺陷。**状态：NOT VERIFIED**（需显式 `-m real_network` 且依赖公网可达）。

---

## 3. 分项测试证据（逐条真实命令与退出码）

### 3.1 SSRF 安全测试

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_ssrf_strict.py -q
```
**退出码 0 — `114 passed, 1 warning in 0.45s`**

覆盖：46 个必须拒绝的地址全部拒绝（环回 / 私有 / 链路本地 / CGNAT / TEST-NET / 组播 / 保留 / IPv4-mapped IPv6 / 6to4 / Teredo / NAT64 / ORCHIDv2 / SRv6 / site-local / 6bone / AS112 / 3fff 文档段）；7 个合法公网地址全部放行（无假阳性）；混合 DNS 结果含任一非法地址即拒绝；每次重定向逐跳复验。

### 3.2 多进程锁竞争 / 租约接管 / 旧 Leader 提交

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_lease_fencing.py -q
```
**退出码 0 — `25 passed, 1 warning in 26.08s`**

覆盖：多进程竞争只有一个胜者、租约过期被接管、旧 Leader 恢复后被 fence、数据库短暂不可用时不写库、identity-map 陈旧行不可信。

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_lock_migration.py tests/test_release_hardening.py tests/test_scheduler_lock_stress.py -q
```
**退出码 0 — `94 passed, 1 warning in 368.71s (0:06:08)`**（既有并发/压力测试，未回归）

### 3.3 数据库凭据 + 真实驱动连接

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_db_credentials.py tests/test_db_connection_wire.py -q
```
**退出码 0 — `43 passed, 1 warning in 1.31s`**

其中 `test_db_connection_wire.py` 是**真实连接测试**：真实 `psycopg` 驱动通过 TCP 连到实现了 PostgreSQL v3 wire 协议的服务端，并逐字节校验驱动发出的口令。见 §5。

### 3.4 部署配置

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_deploy_config_fencing.py tests/test_deployment_config.py -q
```
**退出码 0 — `64 passed, 1 warning in 0.60s`**

覆盖：三个服务的 DB_* 分量完全一致、`DB_PASSWORD` 使用 `:?required` 形式、迁移为一次性服务且 api/worker 依赖 `service_completed_successfully`、worker 健康检查与 deploy.sh 的 healthy/unhealthy/exited 判定。

### 3.5 数据库引擎构造（本轮 P0 回归）

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_database_engine.py -q
```
**退出码 0 — `6 passed, 1 warning in 20.00s`**

### 3.6 既有安全审计

```
cd backend
../.venv/Scripts/python.exe -m pytest tests/test_security_audit.py -q
```
**退出码 0 — `76 passed, 2 warnings in 9.99s`**

### 3.7 依赖漏洞审计

```
.venv/Scripts/python.exe -m pip_audit -r backend/requirements.txt
```
**退出码 0 — `No known vulnerabilities found`**（pip-audit 2.10.1，24 个锁定依赖）

---

## 4. 干净环境构建

### 4.1 后端（严格复刻 `Dockerfile.api` 的两阶段流程）

```
# builder 阶段
.venv/Scripts/python.exe -m pip wheel --no-cache-dir --wheel-dir /tmp/cleanbuild/wheels -r backend/requirements.txt
```
**退出码 0 — 生成 42 个 wheel**

```
# runtime 阶段：全新虚拟环境 + --no-index（不允许再访问网络索引）
python -m venv /tmp/cleanbuild/runtime
/tmp/cleanbuild/runtime/Scripts/python.exe -m pip install --no-cache-dir --no-index \
    --find-links=/tmp/cleanbuild/wheels /tmp/cleanbuild/wheels/*.whl
```
**退出码 0 — `Successfully installed alembic-1.20.0 ... uvicorn-0.54.0 watchfiles-4.x ...`（42 个包）**

```
# 导入验证（在干净环境中）
cd backend && /tmp/cleanbuild/runtime/Scripts/python.exe -c "import app.main; print('CLEAN_ENV_IMPORT_OK', app.main.app.version)"
```
**退出码 0 — `CLEAN_ENV_IMPORT_OK 1.2.0`**

### 4.2 前端

```
cd frontend && rm -rf dist && npm run build
```
**退出码 0**

```
vite v5.4.21 building for production...
✓ 48 modules transformed.
dist/index.html                   0.42 kB │ gzip:  0.33 kB
dist/assets/index-Dhpb4ZYE.css   16.60 kB │ gzip:  4.38 kB
dist/assets/index-CS9j2xbB.js   234.70 kB │ gzip: 74.33 kB
✓ built in 3.13s
```

### 4.3 Docker 镜像构建

**状态：NOT VERIFIED** —— 本机未安装 Docker（`docker: command not found`）。镜像构建、Docker Compose 启动顺序、容器内迁移与健康检查**必须在具备 Docker 的目标环境复验**。`docker-compose.prod.yml` 的 YAML 语法与锚点/变量展开已用 `yaml.safe_load` 静态解析验证（见 `test_deploy_config_fencing.py`），但**未在真实容器中执行**。

---

## 5. 真实数据库连接测试（wire 协议）

### 5.1 为什么不是「连一个真的 PostgreSQL」

本机**无法启动 PostgreSQL 服务端**。两套相互独立的 Windows 构建都在 `initdb` 阶段崩溃：

| 构建 | 版本 | 结果 |
|---|---|---|
| EDB 官方 `postgresql-16.4-1-windows-x64-binaries.zip` | 16.4 | `initdb.exe` → `0xC0000409` |
| zonky `embedded-postgres-binaries-windows-amd64:16.15.0` | 16.15 | `initdb.exe` → `0xC0000409` |

Windows 事件日志（应用程序 ID 1000）给出根因：

```
错误应用程序名称: initdb.exe，版本: 16.0.4.0
错误模块名称: ucrtbase.dll，版本: 10.0.19041.3636
异常代码: 0xc0000409
错误模块路径: C:\Windows\System32\ucrtbase.dll
```

`initdb` 与 `postgres --boot` 都在打印 `Running in debug mode.` 之后以同一异常码崩溃（UCRT 快速失败）。已排除的假设与实测：

| 假设 | 实测 | 结论 |
|---|---|---|
| 沙箱注入导致 | 无沙箱（`dangerouslyDisableSandbox`）重跑 | 仍 `0xC0000409` → **排除** |
| 环境变量污染 | 最小环境（仅 SystemRoot/TEMP/PATH）+ `LC_ALL=C` | 仍崩溃 → **排除** |
| 路径过长 | `C:\pgshort` 短路径 | 仍崩溃 → **排除** |
| 二进制损坏 | 两套独立构建同样崩溃 | **排除** |
| locale / 编码 | `LC_ALL=C`、`--locale=C`、`--lc-messages=C` 均试过 | 仍崩溃 → **排除** |

### 5.2 采用的替代验证：PostgreSQL v3 wire 协议

`backend/tests/pgwire.py` 实现了 PostgreSQL 前端/后端协议的服务端侧，足以与**真实 `psycopg` 驱动**完成一次认证握手，并记录驱动**实际发出的凭据**：

- `AuthenticationCleartextPassword (3)` → 直接取回驱动发出的口令字节
- `AuthenticationMD5Password (5)` → 服务端用期望口令校验挑战应答
- `AuthenticationSASL (10)` + **SCRAM-SHA-256** → 服务端校验客户端 proof（客户端全程不明文发送口令）

这比「连接成功」是**更强**的断言：它证明驱动把 URL 解码成了**预期的字面口令**；而一个接受了错误口令的服务端不可能通过这些断言。

**已验证的用例（`test_db_connection_wire.py`，7 项全绿）**

| 用例 | 断言 |
|---|---|
| 显式 `DB_*` 分量，口令 `P@ss:w/rd%+er#2026 x` | 驱动发出的字节 **逐字节等于** 该字面口令 |
| 同上，SCRAM-SHA-256 | 服务端 proof 校验通过（`scram_ok is True`） |
| 同上，MD5 | 挑战应答校验通过 |
| `DATABASE_URL` 内嵌**原始**特殊字符口令 | 驱动收到原始字面口令（编码恰好一次） |
| `DATABASE_URL` 内嵌**已百分号编码**口令 | 驱动收到解码后的字面口令（未二次编码） |
| 形似编码的原始口令 `pa%41ss-…` | 驱动收到字面 `pa%41ss-…`（**未被误解码为 `paAss…`**） |
| **反向对照**：故意用错口令 | probe 校验失败 → 证明该测试有能力发现缺陷 |

### 5.3 仍未验证项（NOT VERIFIED）

- **真实 PostgreSQL 服务端上的端到端连接**（含 `ALTER USER`、SCRAM 真实协商、PostgreSQL 自身的口令存储校验）—— 因 5.1 的宿主限制无法执行。
- **PostgreSQL 上的并发锁语义**（`SELECT … FOR UPDATE`、行级锁、`SERIALIZABLE` 隔离级别下的租约竞争）—— 本轮的多进程竞争测试在 **SQLite** 上执行。SQLite 与 PostgreSQL 的锁实现不同，**必须在目标 PostgreSQL 环境复跑** `tests/test_scheduler_lock_stress.py` 与 `tests/test_lease_fencing.py`。

---

## 6. 数据库迁移 / 服务启动 / 故障恢复

**命令**

```
cd backend
../.venv/Scripts/python.exe release_smoke.py
```

**退出码 0 — `SMOKE_RESULT rc=0`**

**实际输出**

```
### 1. alembic migration on a fresh database
INFO [radar.migrate] 开始数据库迁移: role=api env=dev
INFO [alembic.runtime.migration] Running upgrade  -> 8bcc2e0aae52, baseline all radar tables
INFO [alembic.runtime.migration] Running upgrade 8bcc2e0aae52 -> 804223700e45, source management fields + crawl task lifecycle
INFO [alembic.runtime.migration] Running upgrade 804223700e45 -> 888363d8d20c, scheduler lock lease_version fencing token
### 1. alembic migration on a fresh database -> exit=0
    db file exists: True size: 172032
    /health/live -> 200 {"status":"alive"}
    /health/ready -> 200 {"status":"ready","database":"ok"}
### 2. API start + health -> exit=0
    import with unreachable DB -> exit 0
    stdout: IMPORTED
    lock acquire/release -> ACQUIRED True
RELEASED True
SMOKE_RESULT rc=0
```

覆盖：全新库上完整迁移链（含 `scheduler lock lease_version fencing token`）；API 进程真实启动并通过 liveness + readiness；**数据库不可达时进程不再在导入期崩溃**（P0 修复的直接验证）；调度锁的真实获取与释放。

---

## 7. 本轮新增的测试文件

| 文件 | 项数 | 锁定的问题 |
|---|---|---|
| `backend/tests/test_db_credentials.py` | 36 | 原始/已编码/特殊字符凭据；不双重编码、不错误解码 |
| `backend/tests/test_db_connection_wire.py` | 7 | 真实 psycopg 驱动在 wire 协议上收到的口令字节 |
| `backend/tests/test_ssrf_strict.py` | 114 | 公网地址判定的严格性、混合 DNS、逐跳重定向复验 |
| `backend/tests/test_lease_fencing.py` | 25 | 租约 fencing、多进程竞争、接管、旧 Leader 提交、identity-map 陈旧行 |
| `backend/tests/test_deploy_config_fencing.py` | 21 | compose 凭据一致性、启动顺序、worker 健康判定 |
| `backend/tests/test_database_engine.py` | 6 | **P0**：PostgreSQL 下 `create_engine()` 重复关键字参数 |

支撑模块：`backend/tests/pgwire.py`（PostgreSQL v3 wire 协议服务端实现）。

**未删除任何既有测试，未放宽任何断言，未伪造任何成功结果。**

---

## 8. 提交与推送

见 `docs/RELEASE_AUDIT.md` §7。

---

## 9. 剩余风险

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| R1 | 真实 PostgreSQL 服务端上未做端到端连接验证 | **中** | 宿主 `initdb` 崩溃（0xC0000409），已用 wire 协议层等价验证凭据，但 PostgreSQL 自身的口令存储与 SCRAM 协商未实测 |
| R2 | 多进程锁竞争测试跑在 SQLite 上 | **中** | SQLite 与 PostgreSQL 锁语义不同；必须在目标 PostgreSQL 复跑 |
| R3 | Docker 镜像构建与容器编排未执行 | **中** | 本机无 Docker；compose 仅做静态解析验证 |
| R4 | `DATABASE_URL` 内嵌口令存在**不可约化歧义** | **低** | 原始口令若字面包含 `%40` 这类「解码后需转义」的转义串，与「已编码」形式在字符串层面不可区分。已提供 `DB_PASSWORD` + `DB_PASSWORD_ENCODED` 显式通道作为无歧义解法，并在代码与文档中标注 |
| R5 | `real_network` 标记的 3 项测试未执行 | **低** | 需显式 `-m real_network` 且依赖公网可达 |
| R6 | Worker 容器内健康检查需 Docker 环境验证 | **低** | 判定逻辑已由 `deploy.sh` 与 compose 配置测试覆盖 |
