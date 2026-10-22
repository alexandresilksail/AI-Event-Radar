# 安全审计报告 — AI Event Radar（v1.5 发布审计轮）

**审计日期**：2026-10-22
**被审计 Git commit**：`a4d7124ff2635d14d9b2a122e2061beaa870c961`（审计基线）
**修复后 commit**：见 `docs/RELEASE_AUDIT.md` §7
**审计范围**：后端应用层 / 数据库连接与调度器 / 生产部署配置 / 第三方依赖
**配套文档**：`TEST_REPORT.md`（逐条测试证据）、`RELEASE_AUDIT.md`（放行结论）
**安全结论**：**代码层面通过；2 项部署环境验证未完成（NOT VERIFIED，见 §11）**

---

## 1. 威胁模型

### 1.1 资产

| 资产 | 敏感性 | 保护目标 |
|---|---|---|
| 管理员口令哈希 | 高 | 不可逆存储、离线破解成本高 |
| JWT 签名密钥 | 高 | 不可泄露、不可弱于 256-bit |
| **数据库连接串与口令** | 高 | 不可硬编码、不可日志泄漏、**不可被 URL 拼接破坏** |
| 通知渠道凭证（`bot_token`、`webhook_key`、SMTP 口令） | 高 | 编辑往返不丢失、读取不返回明文 |
| 爬取到的第三方内容 | 中（不可信） | 不得作为代码执行、不得发起内网请求 |
| 活动数据（Event / RawDocument） | 中 | 写操作需认证、有审计留痕 |
| **调度领导权（DB 租约）** | 高 | 失效租约的旧 Leader 不得再写入任何业务数据 |

### 1.2 信任边界

```
互联网 (完全不可信)
   │
   ├─► nginx (TLS 终止 / 反向代理)   ← 唯一入口
   │       │
   │       ├─► API 容器 (FastAPI)
   │       │        ├─► PostgreSQL
   │       │        ├─► 外部 RSS / API 站点   ← SSRF 面
   │       │        └─► AI 模型 API
   │       └─► Worker 容器 (APScheduler)      ← 真正发起爬取的进程
   │                ├─► PostgreSQL
   │                └─► 外部站点              ← 独立的 SSRF 面
   └─► 通知出口 (SMTP / 企业微信 / Webhook)    ← 独立的 SSRF 面
```

### 1.3 攻击者能力假设

- 外部攻击者：可完全控制自己提交的数据源配置、注册的目标站点内容、通知规则中的 URL
- 内部攻击者（低权限管理员）：可读取运维端点
- 网络中间人：仅在 HTTP 场景（TLS 由 nginx 终止）
- **被接管的 Worker 进程**：租约过期后仍在运行并试图提交结果（本轮 P0 级 fencing 缺口的场景）

---

## 2. 本轮修复的缺陷

### 2.1 审计请求的四项

| # | 严重度 | 类别 | 缺陷 | 攻击/失效路径 | 修复 |
|---|---|---|---|---|---|
| S1 | **P0** | 数据库凭据 | `DATABASE_URL` 内嵌口令的编码状态靠「是否含 `%XX`」猜测 | 原始口令 `pa%41ss` 被误判为「已编码」→ 驱动解码成 `paAss` → **口令被静默篡改，登录失败且原因难辨** | 改用「转义解码后是否落在 `quote(safe="")` 的安全集内」判定（§4.2）；另提供 `DB_PASSWORD` + `DB_PASSWORD_ENCODED` **显式**通道作为无歧义解法 |
| S2 | **P0** | 调度一致性 | 旧 Leader 在租约被接管后仍能提交采集结果 | 抓取期间租约过期 → 新 Leader 接管并重跑 → 旧 Leader 随后 commit，**产生重复事件与重复通知** | `lease_version` 作为 fencing token，在**每次业务写入前**校验（§5） |
| S3 | **P0** | 调度一致性 | `db.get(SchedulerLock, 1)` 返回 **identity map 缓存**，不重新查库 | 长连接 Session 持续读到**陈旧租约行** → 已被接管的 Leader 仍自认是 Leader → fencing 机制整体失效 | 所有租约读取改为 `_read_lease_row_fresh()`（`populate_existing()`，绕过 identity map） |
| S4 | **P1** | 部署配置 | `docker-compose.prod.yml` 用字符串拼接组装数据库 URI | 口令含 `@ : / % + #` 或空格时 URI 结构被破坏 → migrate / api / worker **可能连到不同数据库**，或迁移成功而 API 连不上 | compose 传递**离散分量** `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`，由 `encode_db_url()` 编码恰好一次；三个服务取值完全一致 |

### 2.2 审计过程中额外发现并修复的缺陷

| # | 严重度 | 类别 | 缺陷 | 影响 | 修复 |
|---|---|---|---|---|---|
| S5 | **P0** | 生产启动阻断 | `app/database.py` 中 `create_engine(DB_URL, connect_args=…, pool_pre_ping=True, **pool_kwargs)` —— PostgreSQL 分支的 `_pool_kwargs` **也**含 `pool_pre_ping` 与 `connect_args` | **导入期** `TypeError: create_engine() got multiple values for keyword argument 'pool_pre_ping'` → **生产环境 API 与 Worker 根本无法启动**。因全部测试跑在 SQLite（该分支不触发）而从未暴露 | 合并为**单一 options 字典**，两个分支不可能再冲突（`app/database.py`） |
| S6 | **P1** | SSRF | `is_public_ip()` 的判定顺序：显式黑名单在转换逻辑**之后** | `2002::/16`（6to4）与 `2001::/32`（Teredo）两条黑名单是**死代码** —— 含公网 IPv4 的 6to4 地址被放行 | 重排判定顺序：先黑名单、后转换（§3.2） |
| S7 | **P1** | SSRF | IPv6 特殊用途网段覆盖不全 | `2001:20::/28`（ORCHIDv2）、`5f00::/16`（SRv6 SID）、`2001:1::1`（PCP anycast）、`fec0::/10`（site-local）、`2620:fe::fe`（6bone）、`3fff::/20`（文档段）被放行 | 补齐 IANA 特殊用途注册表（§3.2） |
| S8 | **P1** | 部署验收 | `deploy.sh` 的存活探针打的是 `$base/health/live` | 该路径在 nginx 落到 SPA 的 `location /`，**恒返回 index.html** → 探针恒成功，等于没有探针 | 改为 `$base/api/health/live`（nginx `location /api/` 会剥前缀转发给 `api:8000`） |
| S9 | **P2** | 部署验收 | `deploy.sh` 仅检查 worker 容器 `Up` | 容器在跑但 healthcheck 失败（DB 连不上、租约获取失败）时部署仍会「成功」 | 轮询至 `healthy`；遇到 `unhealthy`/`exited` 立即 `fail` |
| S10 | **P2** | 部署文档 | compose 头注释描述的启动顺序与实际 `depends_on` 不一致 | 事故处置时按过时注释判断 | 注释改为与 `service_completed_successfully` 一致的真实顺序 |

### 2.3 上一轮（v1.4）已修复、本轮复核仍然有效

S1–S11 见 `docs/AUDIT-v1.3.md` 与 git 历史（commit `a4d7124`）：Worker 缺失 DNS 级守卫（P0）、守卫卸载恢复可替换接缝（P0）、Webhook 未过策略（P1）、9 个运维端点无认证（P1）、`javascript:` 协议 URL 存储（P1）、通知规则编辑销毁密钥（P1）、登录无暴力破解防护（P1）、全局守卫误杀内网端点（P1）、口令非常量时间比对（P2）、依赖未固定（P2）、nginx 缺静态资源缓存（P2）。

本轮 `tests/test_security_audit.py`（76 项）与 `tests/test_release_hardening.py` 全部通过，确认这些修复未回归。

---

## 3. SSRF 纵深防御

### 3.1 三层结构

| 层 | 实现 | 拦截的威胁 |
|---|---|---|
| L1 策略校验 | `validate_public_url()` | 协议白名单、私网/环回/链路本地/保留地址黑名单、重定向前预检 |
| L2 解析层守卫 | `install_guarded_resolver()` 包裹 `socket.getaddrinfo` | **DNS rebinding**：策略校验通过后、实际连接前，域名解析结果被改为私网 |
| L3 逐跳复验 | `safe_get()` 每跳重新执行 L1 + L2 | 重定向到内网（`Location: http://10.0.0.1/`） |

L2 是关键：单纯做 URL 字符串校验存在 **TOCTOU 窗口**。全局守卫在**实际解析的那一刻**判定地址是否合法，窗口被关闭。

### 3.2 地址分类（本轮补齐）

判定顺序（本轮修正，S6）：**先查显式黑名单 → 再做封装还原（IPv4-mapped / 6to4 / Teredo）→ 最后才用 `is_global` 兜底**。修正前黑名单在转换之后，导致 6to4 与 Teredo 两条规则形同虚设。

**IPv4 黑名单**

```
0.0.0.0/8          10.0.0.0/8         100.64.0.0/10      127.0.0.0/8
169.254.0.0/16     172.16.0.0/12      192.0.0.0/24       192.0.2.0/24
192.88.99.0/24     192.168.0.0/16     198.18.0.0/15      198.51.100.0/24
203.0.113.0/24     224.0.0.0/4        240.0.0.0/4
```

**IPv6 黑名单（本轮补齐 S7）**

```
::/128  ::1/128  64:ff9b::/96  64:ff9b:1::/48  100::/64  2001::/23
2001:db8::/32  fc00::/7  fe80::/10  ff00::/8  2002::/16  2001::/32
2001:20::/28 (ORCHIDv2)   2001:30::/28 (Drone Remote ID)
5f00::/16 (SRv6 SIDs)     fec0::/10 (site-local)
2620:4f:8000::/48 (AS112-v6)   3fff::/20 (文档段)
2620:fe::fe (6bone anycast，已废弃)
```

**映射形式**：`::ffff:x.x.x.x`（IPv4-mapped）、`2002::/16`（6to4）、`2001::/32`（Teredo）、`64:ff9b::/96`（NAT64）—— 均先还原出内嵌 IPv4 再判定。

**实测覆盖（`tests/test_ssrf_strict.py`，114 项，退出码 0）**

| 类别 | 数量 | 结果 |
|---|---|---|
| 必须拒绝的地址（含全部特殊用途段与封装形式） | 46 | **全部拒绝** |
| 必须放行的合法公网地址 | 7 | **全部放行**（无假阳性） |
| 混合 DNS 结果（含 1 个非法即拒绝） | ✔ | 拒绝 |
| 重定向到 `169.254.169.254` / `10.0.0.5` | ✔ | 拒绝，且**内网目标从未被请求** |
| 逐跳复验（校验调用位于重定向循环体内） | ✔ | 通过 |

### 3.3 豁免机制及其风险控制

`trusted_internal_hosts()` 收集三类主机：`TRUSTED_INTERNAL_HOSTS` 显式配置、`SMTP_HOST`、`AI_BASE_URL` 的 host。

**风险控制**：豁免是**按主机精确匹配**的，不接受 CIDR / 通配符 / 网段形式。

### 3.4 覆盖面

| 出口 | 是否受保护 | 实现位置 |
|---|---|---|
| RSS/Sitemap 采集 | ✅ | `collectors/base.py` → `safe_get()` |
| HTML / API 采集 | ✅ | 同上 |
| 搜索引擎发现 | ✅ | 同上 |
| Webhook 通知 | ✅ | `services/notifier.py` → `validate_public_url()` |
| 企业微信通知 | ✅ | 校验完整 URL 形态；裸 key 形式固定指向官方主机 |
| 邮件通知（SMTP） | ✅（豁免） | `SMTP_HOST` 自动加入信任列表 |
| AI 模型调用 | ✅（豁免） | `AI_BASE_URL` 自动加入信任列表 |

---

## 4. 数据库凭据处理

### 4.1 配置来源与优先级

| 优先级 | 来源 | 语义 |
|---|---|---|
| 1（首选） | `DB_USER` + `DB_PASSWORD` + `DB_HOST` + `DB_NAME`（+ `DB_PORT`） | 口令是**字面值**，程序编码**恰好一次**。`DB_PASSWORD_ENCODED=true` 表示该值**已**被编码，原样使用 |
| 2（回退） | `DATABASE_URL` | 内嵌凭据：已编码则原样透传；未编码则编码一次（判定规则见 §4.2） |

`app/database.py`、`alembic/env.py`、API 与 Worker **全部**经由 `encode_db_url()`，不存在旁路。

### 4.2 编码状态判定规则（本轮 S1 的核心）

**问题**：原始口令与已编码口令在字符串层面**不可区分**——原始口令 `pa%41ss` 与「`paAss` 的编码形式」完全同形。猜测错误会**静默篡改凭据**。

**解法**：`quote(value, safe="")` 只保留 `A-Za-z0-9_.-~`。因此一个**真正已编码**的凭据，必然至少包含一个「解码后落在该安全集之外」的 `%XX` 转义。

| 输入 | 判定 | 结果 | 驱动最终收到的口令 |
|---|---|---|---|
| `p@ss:w/rd` | 无 `%XX` → 原始 | `p%40ss%3Aw%2Frd` | `p@ss:w/rd` ✅ |
| `pa%41ss` | `%41`→`A`（安全集内）→ 原始 | `pa%2541ss` | `pa%41ss` ✅（**不再被误解码为 `paAss`**） |
| `pa%40ss` | `%40`→`@`（需转义）→ 已编码 | 原样 | `pa@ss` ✅（**不二次编码**） |
| `pa%3Ass` | `%3A`→`:`（需转义）→ 已编码 | 原样 | `pa:ss` ✅ |
| `pa%2541ss` | `%25`→`%`（需转义）→ 已编码 | 原样 | `pa%41ss` ✅ |
| `复杂密码123` | 无 `%XX` → 原始 | UTF-8 百分号编码 | `复杂密码123` ✅ |

**残余歧义（不可约化）**：原始口令若字面包含 `%40` 这类「解码后需转义」的转义串，与「已编码」形式不可区分。此时**必须**使用显式的 `DB_PASSWORD` 通道（编码状态被声明而非猜测）。已在代码 docstring、`docs/DEPLOY.md` 与本报告中标注。

### 4.3 部署一致性

`docker-compose.prod.yml` 的 `migrate` / `api` / `worker` 三个服务**内联同一组**：

```yaml
DATABASE_URL: postgresql+psycopg://${DB_USER:-radar}:${DB_PASSWORD}@db:5432/${DB_NAME:-radar}
DB_HOST: db
DB_PORT: "5432"
DB_NAME: ${DB_NAME:-radar}
DB_USER: ${DB_USER:-radar}
DB_PASSWORD: ${DB_PASSWORD:?required, set in .env}
```

- `DATABASE_URL` 仍导出以兼容直接读取该变量的工具，且**只引用变量、不含任何硬编码口令**；
- 应用层**优先**使用离散分量（`encode_db_url` 的优先级 1），因此**被拼接破坏的 URI 永远不是实际连接所用的 URI**；
- `DB_PASSWORD` 使用 `:?required` 形式 —— 缺失时 compose **直接失败**；
- 三个服务取值**完全一致**（由 `test_deploy_config_fencing.py` 断言）。

### 4.4 生产配置校验

`validate_production_config()` 在 `ENV=production` 时拒绝启动于以下情形：JWT 密钥为默认或 <32 位；管理员口令为默认或 <12 位；`ROLE` 非法；缺少数据库主机名；数据库口令 <16 位、为常见默认值或为纯数字；`ALLOWED_ORIGINS` 为空或含通配符。

---

## 5. 调度器租约与 Fencing

### 5.1 租约模型

`SchedulerLock(id=1)` 保存 `holder` / `acquired_at` / `heartbeat_at` / **`lease_version`**。`lease_version` 在每次获取时**单调递增**，是 fencing token。

### 5.2 Fencing 语义（本轮 S2 / S3）

| 环节 | 修复前 | 修复后 |
|---|---|---|
| 读取租约 | `db.get(SchedulerLock, 1)` —— **返回 identity map 缓存**，长连接 Session 持续读到陈旧行 | `_read_lease_row_fresh()` —— `populate_existing()` 强制刷新 |
| 心跳续约 | 只比对 `holder` | 比对 `holder` **且** `lease_version` |
| 调度前检查 | 检查 `holder` | 检查 `holder` + 新鲜度 + `lease_version` |
| **业务写入** | **完全不检查** —— 抓取期间租约被接管，旧 Leader 仍 commit | **每次 commit 前**经 `LeaseGuard.check()` 校验 `lease_version`；失效则回滚并中止 |

`LeaseGuard` 在 `scheduler._crawl_job()` 中创建，透传到 `pipeline.create_task_and_crawl()` → `crawl_source()`，覆盖**每一个**业务写入点：`RawDocument` 落库、`Event` 创建、`merge_event` 合并、搜索发现的 source 写入，以及最终的抓取状态汇总。

### 5.3 实测覆盖（`tests/test_lease_fencing.py`，25 项，退出码 0）

| 场景 | 断言 |
|---|---|
| 多进程竞争 | 只有一个进程拿到租约；失败者无法心跳、无法通过 fencing |
| 租约过期被接管 | 旧 Leader 的下一次写入被 fence；`{"fenced": true}` |
| 旧 Leader 恢复 | 无法续约、无法提交 |
| 数据库短暂不可用 | 不写库（校验异常即放弃本次任务） |
| identity map 陈旧行 | `current_lease_version` / `lease_still_valid` / `heartbeat` **均不信任**陈旧行 |

---

## 6. 认证与授权（v1.4 审计结论，本轮复核无回归）

### 6.1 口令存储

| 项 | 值 |
|---|---|
| 算法 | PBKDF2-HMAC-SHA256 |
| 迭代次数 | **120,000**（断言下限 100,000） |
| 比对 | `hmac.compare_digest`（常量时间） |
| 生成 | `secrets` 模块（CSPRNG） |

### 6.2 JWT

| 项 | 值 |
|---|---|
| 算法 | HS256 |
| 有效期 | 12 小时 |
| 密钥强度 | 生产环境强制 ≥32 字符 |
| 校验 | 签名、过期、`alg` 字段均校验；错误密钥签发的 token 被拒 |

### 6.3 暴力破解防护

`(IP, 用户名)` 滑动窗口：5 次失败锁定 300 秒；失败写入审计日志。

---

## 7. 输入与输出安全（v1.4 审计结论，本轮复核无回归）

- **存储型 XSS 路径已修复**：后端 `sanitize_url()` 在**不可信数据进入数据库的边界**阻断 `javascript:` 等危险协议；前端 `safeHref()` 兜底。
- 服务端输入统一经 `sanitize_text()` / `sanitize_url()` 处理。
- CORS：生产环境 `ALLOWED_ORIGINS` 必须显式配置，禁止通配符与携带凭证。

---

## 8. 依赖漏洞

### 8.1 Python — pip-audit 2.10.1（实际运行）

```
.venv/Scripts/python.exe -m pip_audit -r backend/requirements.txt
```
**退出码 0 — `No known vulnerabilities found`**（24 个锁定依赖，`backend/requirements.txt` 全部固定到实测版本）

### 8.2 前端

`frontend/package-lock.json` 锁定；本轮未改动前端依赖。

---

## 9. 密钥与凭证管理

| 项 | 状态 |
|---|---|
| `.env` | 被 `.gitignore` 排除，未入库 |
| `*.db` / `data/` | 被 `.gitignore` 排除，未入库 |
| 证书 / PEM | 未入库 |
| compose 中的口令 | **仅** `${DB_PASSWORD}` 变量引用，无任何硬编码 |
| 日志中的口令 | 通知渠道凭证与数据库口令均不写入日志 |
| `ADMIN_RESET_TOKEN` | 可选，留空即禁用；不返回明文 |

**本轮核验**：`git ls-files` 中不存在 `.env` / `*.db` / `data/` / `.venv/` / `node_modules/` / `*.pem` / `*.crt` / `secret` 等敏感文件。

---

## 10. 审计留痕

登录失败、通知发送、规则变更、爬取任务与调度状态变更均写审计日志；通知渠道凭证与数据库口令**不进入**日志。

---

## 11. 剩余风险登记

| # | 风险 | 等级 | 说明 | 缓解 / 复验计划 |
|---|---|---|---|---|
| R1 | 真实 PostgreSQL 服务端上的端到端连接未验证 | **中** | 宿主 `initdb.exe` 崩溃（`ucrtbase.dll` / `0xC0000409`，两套独立构建同样复现），已用 PostgreSQL v3 wire 协议 + 真实 psycopg 驱动逐字节验证凭据（7 项全绿），但 PostgreSQL 自身的口令存储与真实 SCRAM 协商未实测 | 在具备 PostgreSQL 的环境复跑 `tests/test_db_connection_wire.py` 与迁移/启动脚本 |
| R2 | 多进程锁竞争测试跑在 SQLite 上 | **中** | SQLite 与 PostgreSQL 锁语义不同 | 在目标 PostgreSQL 复跑 `tests/test_scheduler_lock_stress.py`、`tests/test_lease_fencing.py` |
| R3 | Docker 镜像构建与容器编排未执行 | **中** | 本机无 Docker；compose 仅做静态 YAML 解析验证 | 在目标主机执行 `bash scripts/deploy.sh` 完整验收 |
| R4 | `DATABASE_URL` 内嵌口令存在不可约化歧义 | **低** | 原始口令字面含 `%40` 之类转义串时不可区分 | 使用显式 `DB_PASSWORD` + `DB_PASSWORD_ENCODED`（无歧义）；已在代码与部署文档标注 |
| R5 | `real_network` 标记的 3 项测试未执行 | **低** | 需 `-m real_network` 且依赖公网可达 | CI 中作为独立任务按需触发 |
| R6 | Worker 容器内健康检查需 Docker 验证 | **低** | 判定逻辑已由脚本与配置测试覆盖 | 随 R3 一并复验 |

---

## 12. 安全结论

| 维度 | 结论 |
|---|---|
| P0 缺陷 | **0 项未修复**（本轮新发现 1 项 P0 生产启动阻断 + 2 项 P0 一致性/凭据缺陷，均已修复并有回归测试锁定） |
| P1 缺陷 | **0 项未修复**（本轮 4 项，均已修复并有回归测试锁定） |
| P2 缺陷 | **0 项未修复**（本轮 2 项，均已修复） |
| 依赖漏洞 | 0（pip-audit 实际运行） |
| 敏感文件入库 | 0 |
| 部署环境验证 | **2 项 NOT VERIFIED**（Docker 编排、真实 PostgreSQL 服务端）—— 见 R1 / R3 |

**代码与依赖层面：通过。** 未修复的 P0/P1 数量为 **0**，不阻断发布。

发布放行结论见 `docs/RELEASE_AUDIT.md`。
