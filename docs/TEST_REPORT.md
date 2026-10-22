# 测试报告 — AI Event Radar v1.4

**执行日期**：2026-10-22
**环境**：Windows 10 (10.0.19045) / Python 3.13.12 / pytest 9.1.1 / Node 24.20.0
**代码基线**：commit `f8f471d` + 本次审计修复（工作树）
**结果**：**387 passed, 3 deselected, 130 warnings in 687.61 s (0:11:27)**

---

## 1. 执行摘要

| 指标 | 数值 |
|---|---|
| 用例总数 | 390 |
| 收集到 | 387 |
| 通过 | **387** |
| 失败 | **0** |
| 跳过（deselected） | 3（`real_network` marker，默认排除） |
| 错误 | 0 |
| 总耗时 | 687.61 s (11 min 27 s) |
| 警告 | 130（其中 **0 条** Python 弃用警告；其余为 starlette/httpx 互操作提示、Alembic 配置提示、PyJWT 测试用短密钥提示） |

### 与审计前基线对比

| 时间点 | 结果 |
|---|---|
| 审计开始时（基线） | 208 passed / 6 failed / 3 deselected |
| 修复认证收紧后的中间态 | 218 passed / 3 deselected |
| 业务缺陷修复后 | 218 passed / 3 deselected |
| **最终（本报告）** | **387 passed / 3 deselected** |

净增 **179 项**测试（+85.7%），同时 **0 失败**。

---

## 2. 逐文件实测结果

以下数字全部来自本次实际运行（每个文件单独执行一次），非估算：

| 测试文件 | 用例数 | 结果 | 耗时 | 对应审计阶段 |
|---|---|---|---|---|
| `test_security_audit.py` | 76 | ✅ 76 passed | 9.33 s | Stage 6 |
| `test_release_hardening.py` | 48 | ✅ 48 passed | 15.45 s | Stage 1 / 3 |
| `test_business_integrity.py` | 47 | ✅ 47 passed | 1.52 s | Stage 5 |
| `test_deployment_config.py` | 41 | ✅ 41 passed | 0.40 s | Stage 2 |
| `test_lock_migration.py` | 41 | ✅ 41 passed | 75.57 s | 迁移互斥 |
| `test_integration.py` | 30 | ✅ 30 passed | 27.55 s | 端到端 |
| `test_hardening.py` | 29 | ✅ 29 passed | 38.20 s | 历史加固回归 |
| `test_ssrf_final.py` | 26 | ✅ 25 passed, 1 deselected | 19.65 s | Stage 3 |
| `test_units.py` | 20 | ✅ 20 passed | 0.41 s | 单元 |
| `test_source_management.py` | 18 | ✅ 18 passed | 6.23 s | 数据源生命周期 |
| `test_scheduler_lock_stress.py` | 5 | ✅ 5 passed | **298.27 s** | Stage 4 |
| `test_migrations.py` | 4 | ✅ 4 passed | 39.88 s | 迁移链 |
| `test_backup_restore.py` | 3 | ✅ 3 passed | 2.47 s | Stage 9 |
| `test_real_e2e.py` | 3 | ⏭ 3 deselected | — | 真实网络 |
| **合计** | **390** | **387 passed / 3 deselected** | **687.61 s** | — |

> `test_scheduler_lock_stress.py` 耗时近 5 分钟，因为它真的创建并竞争多个独立 OS 进程，而非线程模拟。

---

## 3. pytest 配置

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

**设计理由**：默认运行必须给出**确定性**信号。访问第三方主机的用例把上游可用性引入了回归门禁——上游 TLS 握手超时会让"应用有缺陷"的结论失真。marker 隔离后：

```bash
pytest tests/ -q              # 387 passed  — 确定性门禁
pytest tests/ -q -m real_network   # 3 selected — 需真实网络，手动触发
```

`--strict-markers` 确保未注册的 marker 会直接报错，防止拼写错误导致的静默跳过。

---

## 4. 覆盖矩阵

### 4.1 Stage 1 — 代码审计修复项回归

| 缺陷 | 回归测试位置 | 覆盖方式 |
|---|---|---|
| worker 未装 DNS 守卫（P0） | `test_release_hardening.py` | 导入 `app.worker` 后断言 `_RESOLVER_INSTALLED is True` |
| 解析器卸载恢复假实现（P0） | `test_release_hardening.py` | 卸载后断言 `socket.getaddrinfo is _NATIVE_GETADDRINFO`（非测试替身） |
| 合法内网端点被误杀（P1） | `test_release_hardening.py` | 信任主机解析成功 / 非信任主机仍抛 `SSRFError` |
| 通知规则编辑销毁密钥（P1） | `test_release_hardening.py` | 掩码值往返后密钥不变；新密钥能替换旧密钥 |
| 9 个运维端点无认证（P1） | `test_release_hardening.py` | 无 token → 401；公开端点 → 200 |
| Webhook 未做 SSRF 校验（P1） | `test_release_hardening.py` | 内网 URL 被拒 |
| 非订阅源静默失效（P1） | `test_business_integrity.py` | 验证码 HTML → 抛 `CollectorError` |
| 活动改期不生效（P1） | `test_business_integrity.py` | `merge_event` 后 `start_time` 被更新 |
| `javascript:` 存储型 XSS（P1） | `test_security_audit.py` | `sanitize_url()` 拒绝危险协议；前端 `safeHref` 兜底 |
| 登录无暴力破解防护（P1） | `test_security_audit.py` | 连续失败 → 429 |
| 口令非常量时间比对（P2） | `test_security_audit.py` | 断言源码使用 `hmac.compare_digest` |

### 4.2 Stage 3 — SSRF

| 维度 | 覆盖内容 |
|---|---|
| 协议 | `http`/`https` 允许；`file`/`ftp`/`gopher`/`data`/`javascript` 拒绝 |
| IPv4 私网 | `0.0.0.0/8`、`10/8`、`172.16/12`、`192.168/16`、`127/8`、`169.254/16`、`100.64/10`、`224/4` |
| IPv6 私网 | `::1`、`fc00::/7`、`fe80::/10`、`ff00::/8`、`::` |
| 映射地址 | IPv4-mapped、6to4、Teredo、NAT64 均正确还原后判定 |
| DNS rebinding | 全局 `getaddrinfo` 守卫拦截解析到私网的域名 |
| 逐跳 | 重定向每一跳重新校验 |
| 豁免机制 | 运维可信主机放行，其余不受影响 |

### 4.3 Stage 4 — 调度器租约

**使用真实独立 OS 进程**（`subprocess.Popen` + 独立解释器），非线程模拟：

| 测试 | 断言 |
|---|---|
| `test_only_one_process_wins_the_lease` | 8 进程并发竞争 → 恰好 1 个 `acquired=True`，租约行 holder 与胜者一致 |
| `test_losers_cannot_heartbeat_or_pass_fencing` | 落败进程心跳被拒、fencing 校验不通过 |
| `test_fencing_version_is_monotonic` | 多轮接管后 `lease_version` 严格递增 |
| `test_lease_row_is_never_inconsistent` | 竞争全程租约行（holder/version/heartbeat）不撕裂 |
| `test_demoted_leader_cannot_dispatch` | 被降级的旧领导者派发前复验失败 → 拒绝派发 |

### 4.4 Stage 5 — 业务完整性

| 领域 | 用例覆盖 |
|---|---|
| RSS 2.0 | 条目解析、标题/链接/摘要/正文、`published` → `updated` 回退 |
| Atom | `<entry>` + `<link href>` |
| 异常输入 | 截断 XML、验证码 HTML、登录墙 HTML → 显式报错而非静默成功 |
| 去重 L1 | 精确 URL，忽略 scheme 与 www 差异 |
| 去重 L2 | 标题 + 城市相似度阈值 |
| 去重 L3 | 内容指纹 |
| 日期抽取 | `2026年5月1日`、`2026-12-05`、`2026-12-05 to 2026-12-06`、`2026-12-05~2026-12-06`、`2026-12-05 – 2026-12-06`、`2026年12月5日至12月6日`；`"total"` 不误匹配 |
| 字段抽取 | 城市、国家（上海→中国）、线上/线下、主办方、未知字段用 `未知`/`待核实` 占位 |
| 端到端 | 采集 → 抽取 → 去重 → 入库 → 通知 → 增量/去重计数 |

### 4.5 Stage 6 — 安全

| 类别 | 用例覆盖 |
|---|---|
| 口令 | PBKDF2-HMAC-SHA256 120,000 迭代（断言 ≥100,000）；常量时间比对 |
| JWT | 签名、篡改、错误密钥、过期、算法 |
| 认证边界 | 9 个运维端点 401；公开产品面 200 |
| XSS | 危险协议在存储层与渲染层双端阻断 |
| CORS | 显式白名单；通配符拒绝；携带凭据时禁 `*` |
| 审计 | 写操作留痕；失败登录记 `login_failed`；审计内容不含 token |
| 暴力破解 | 429 锁定；不泄露用户存在性；成功登录清零 |
| 依赖 | `requirements.txt` 全部固定版本（含 `psycopg[binary]==3.2.13`） |

### 4.6 Stage 9 — 备份恢复

| 测试 | 断言 |
|---|---|
| 备份→变更→恢复 | 恢复后状态与备份**字节相同** |
| 时间点快照 | 备份后的变更不影响备份内容；恢复可回滚已删数据 |
| 损坏文件恢复 | 用损坏文件恢复后可正确恢复数据 |

---

## 5. 手工执行的验证（不属于 pytest 套件）

### 5.1 备份恢复真实演练

在开发库 `backend/data/radar.db`（606,208 bytes）上执行：

```
STEP 1 backup written : snapshot.db (606208 bytes)
STEP 2 baseline sha256: 52e0e794e06b2088c69f10e73776e297285eb7c23045d8848888751e740d5e99
STEP 2 baseline counts: sources=5, crawl_tasks=3, crawl_logs=3, raw_documents=52,
                        events=4, event_sources=4, event_history=0,
                        notification_rules=0, audit_logs=3, users=1  (total 75)
STEP 3 mutation       : deleted 3 rows from crawl_logs; total 75 -> 72
STEP 4 restore        : snapshot copied back over the live DB
STEP 5 restored sha256: 52e0e794e06b2088c69f10e73776e297285eb7c23045d8848888751e740d5e99

RESULT: PASS
  rows restored           : 75
  tables verified         : 11
  sha256 match            : yes
  deleted rows rolled back: 3 (in crawl_logs)
```

### 5.2 生产配置门禁双向验证

```
合法生产配置 → PROD CONFIG: OK

弱配置（sqlite + 5 字符 JWT + admin 默认口令 + ALLOWED_ORIGINS=*）→ 逐条拒绝：
  - ADMIN_PASSWORD 为默认值或长度不足 12 位，生产环境必须使用强密码
  - 生产环境必须使用 PostgreSQL（DATABASE_URL=postgresql+psycopg://…）
  - 生产环境 ALLOWED_ORIGINS 不允许使用通配符 *
```

### 5.3 前端构建

```
✓ 48 modules transformed.
dist/index.html                   0.42 kB │ gzip:  0.33 kB
dist/assets/index-Dhpb4ZYE.css   16.60 kB │ gzip:  4.38 kB
dist/assets/index-CS9j2xbB.js   234.70 kB │ gzip: 74.33 kB
✓ built in 9.51s
```

### 5.4 Alembic 迁移链

```
$ python -m alembic heads
888363d8d20c (head)
```

单一 head，无分叉；补齐 `path_separator = os` 后不再产生 DeprecationWarning。

---

## 6. 依赖扫描

### 6.1 Python（pip-audit 2.10.1）

```
$ pip-audit -r requirements.txt --progress-spinner off
No known vulnerabilities found

$ pip-audit --local --progress-spinner off --skip-editable
（初次扫描：3 项，全部位于构建工具链 certifi / pip，不在应用依赖中）
→ pip install --upgrade "certifi>=2026.10.19" "pip>=26.2"
→ 复扫：无已知漏洞
```

### 6.2 前端（npm audit）

```
$ npm audit --omit=dev --audit-level=high
2 moderate severity vulnerabilities   (react-router / react-router-dom)
```

| 公告 | 判定 | 依据 |
|---|---|---|
| GHSA-wrjc-x8rr-h8h6 | **不可达** | 全部导航目标均为静态字面量或内部数字 ID，无用户可控外部重定向 |
| GHSA-337j-9hxr-rhxg | **不可达** | 纯客户端 SPA（`HashRouter` + `createRoot`），无 SSR/hydration 路径 |

修复需升级 `react-router-dom@7.18.4`（破坏性变更），已记录为待办而非强制执行。

---

## 7. 过程中发现并修复的测试缺陷（如实记录）

本次审计不仅修产品缺陷，也修正了**测试自身的问题**。如实列出：

| # | 问题 | 性质 | 处理 |
|---|---|---|---|
| 1 | `test_units.py::test_blocks_dns_rebind_to_private` 单跑通过、全量跑失败 | **测试设计缺陷**：解析器卸载恢复了可被测试替换的接缝，导致假解析器泄漏到后续模块 | 引入 `_NATIVE_GETADDRINFO`，卸载时恢复原生实现 |
| 2 | 压测中多进程并发 `create_all()` 建表竞态 | **测试设计缺陷** | 改为父进程预建 schema，子进程不建表 |
| 3 | 压测第二轮无胜者 | **测试设计缺陷**：上轮接管者留下新鲜租约 | 每轮开始前先老化租约 |
| 4 | `test_masked_secret_survives_edit_roundtrip` 断言 `'x' == '123:SECRET'` | **测试隔离缺陷**：按 name 查记录命中了前一个测试创建的行 | 改为捕获刚创建对象的 `id` |
| 5 | `test_settings_endpoint_no_secrets` 断言路径失效 | 测试未跟随认证收紧而更新 | 改为带认证请求后断言 |
| 6 | `assert 404 or True` | **永不失败的假断言** | 改为真实状态码校验 |
| 7 | RSS 源 mock 返回 HTML | mock 与新加固逻辑冲突 | mock 改为返回合法 RSS |
| 8 | `country == UNKNOWN` 断言 | 断言写错（上海→中国是正确行为） | 修正断言 |
| 9 | `db` fixture 跨测试共享导致绝对计数不可靠 | 测试隔离缺陷 | 本文件加 autouse 清理 fixture |
| 10 | `_is_locked()` 在未锁定时清空失败计数 | **产品实现缺陷**（限流被绕过） | 修正为仅在锁定过期时清理 |
| 11 | PBKDF2 迭代次数正则被 `password.encode()` 的括号截断 | 测试正则脆弱 | 改为匹配调用最后一个参数 |

---

## 8. 未验证项（明确标注）

| 项 | 原因 | 可在何处补做 |
|---|---|---|
| Docker 镜像构建 / compose 编排 | 本机无 Docker | `docs/RELEASE_CHECKLIST.md` §三 |
| PostgreSQL 真实运行 | 无 Docker | §三 |
| `scripts/backup.sh` / `restore.sh`（PG 路径） | 依赖 Docker | §十 |
| 真实公网 SSRF 拦截（DNS rebinding） | 需真实 DNS 环境 | §四 |
| 真实 RSS/API 源采集 | 需真实数据源 | §六 |
| 真实 AI 模型抽取 | 需 `AI_API_KEY` | §六 |
| TLS / HTTPS / 反向代理 | 无域名、无证书、无 Docker | §八 |
| 24 小时连续运行 | 会话时长限制 | §十一 |
| 真实网络 E2E（3 项） | 默认 marker 排除 | `pytest -m real_network` |

---

## 9. 复现命令

```bash
# 完整后端套件（确定性门禁）
cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q

# 单文件
cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_security_audit.py -q

# 仅多进程压测（约 5 分钟）
cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_scheduler_lock_stress.py -q

# 真实网络 E2E（需外网）
cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q -m real_network

# 依赖扫描
cd backend && ../.venv/Scripts/python.exe -m pip_audit -r requirements.txt --progress-spinner off
cd frontend && npm audit --omit=dev --audit-level=high

# 前端构建
cd frontend && npm run build

# 迁移链
cd backend && ../.venv/Scripts/python.exe -m alembic heads
```

---

## 10. 结论

**测试验收：通过。**

- 387 项用例全部通过，0 失败、0 错误
- 新增 179 项测试，覆盖本次审计发现的全部 11 项缺陷
- 每个新增测试都针对**具体缺陷或具体契约**，无"永不失败"的空断言
- 依赖扫描：Python 零已知漏洞；前端 2 项 moderate 已逐一验证不可达
- 8 类环境受限项已明确标注 **NOT VERIFIED** 并给出补做位置，未以任何形式冒充通过
