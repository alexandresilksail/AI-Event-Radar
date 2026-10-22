# 安全审计报告 — AI Event Radar v1.4

**审计日期**：2026-10-22
**审计范围**：后端 30 个模块 / 前端 16 个文件 / 基础设施配置 / 第三方依赖
**配套文档**：`RELEASE_AUDIT.md`（全流程）、`TEST_REPORT.md`（测试明细）、`RELEASE_CHECKLIST.md`（放行清单）
**安全结论**：**代码与依赖层面通过；部署层面 6 项待目标环境验证（BLOCKING）**

---

## 1. 威胁模型

### 1.1 资产

| 资产 | 敏感性 | 保护目标 |
|---|---|---|
| 管理员口令哈希 | 高 | 不可逆存储、离线破解成本高 |
| JWT 签名密钥 | 高 | 不可泄露、不可弱于 256-bit |
| 数据库连接串与口令 | 高 | 不可硬编码、不可日志泄漏 |
| 通知渠道凭证（`bot_token`、`webhook_key`、SMTP 口令） | 高 | 编辑往返不丢失、读取不返回明文 |
| 爬取到的第三方内容 | 中（不可信） | 不得作为代码执行、不得发起内网请求 |
| 活动数据（Event / RawDocument） | 中 | 写操作需认证、有审计留痕 |
| 运维数据（磁盘占用、任务队列、日志） | 中 | 仅认证用户可见 |

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

**关键认识**：Worker 容器是**唯一真正发起出网请求的进程**，而 API 容器的 `lifespan()` 只在 uvicorn 下执行——这正是本次发现 P0 缺陷的根源（见 §3.1）。

### 1.3 攻击者能力假设

- 外部攻击者：可完全控制自己提交的数据源配置、注册的目标站点内容、通知规则中的 URL
- 内部攻击者（低权限管理员）：可读取运维端点
- 网络中间人：仅在 HTTP 场景（TLS 由 nginx 终止）

---

## 2. 本次修复的安全缺陷

| # | 严重度 | 类别 | 缺陷 | 攻击路径 | 修复 |
|---|---|---|---|---|---|
| S1 | **P0** | SSRF | Worker 进程未安装 DNS 级守卫 | 恶意/被控站点在解析阶段返回私网地址 → Worker 直连内网（含云元数据 `169.254.169.254`） | `worker.main()` 内 `install_guarded_resolver()` |
| S2 | **P0** | SSRF | 守卫卸载后恢复的是**可替换接缝** | 某次卸载把测试替身永久装入进程全局 → 后续所有 SSRF 防护静默失效 | 引入不可变 `_NATIVE_GETADDRINFO`，卸载恢复原生实现 |
| S3 | **P1** | SSRF | Webhook 回调 URL 未过策略 | 运营误配/被诱导配置 `http://169.254.169.254/` → API 容器代打内网 | 发送前 `validate_public_url()` |
| S4 | **P1** | 越权 | 9 个运维端点无认证 | 未认证访问者读取磁盘占用、调度器状态、爬取日志、全部数据源与通知规则 | 全部加 `Depends(current_user)` |
| S5 | **P1** | XSS | `javascript:` 协议 URL 存储 + 渲染 | 恶意 feed 的 `<link>javascript:…</link>` 入库 → 前端 `<a href>` 渲染 → 在本站源执行脚本 | 后端 `sanitize_url()` 源头阻断 + 前端 `safeHref()` 兜底 |
| S6 | **P1** | 凭证 | 通知规则编辑销毁已存密钥 | 编辑规则时前端回传掩码 `"***"` → 服务端原样覆盖 → `bot_token` 等永久失效 | `_merge_preserving_secrets()`，掩码语义化为"未修改" |
| S7 | **P1** | 认证 | 登录无暴力破解防护 | 自动化尝试弱口令/字典口令 | `(IP, 用户名)` 滑动窗口：5 次失败锁 300 s；失败写审计日志 |
| S8 | **P1** | 配置 | 全局守卫误杀合法内网端点 | 运维配置的内部 SMTP / LLM 网关无法连接（可用性事故） | `trusted_internal_hosts()` + `TRUSTED_INTERNAL_HOSTS` 显式豁免 |
| S9 | **P2** | 密码学 | 口令比对非常量时间 | 时序侧信道推断口令 | `hmac.compare_digest` |
| S10 | **P2** | 供应链 | 依赖未固定版本 | 漂移 / 投毒 / 不可复现构建 | `requirements.txt` 全部固定到实测版本 |
| S11 | **P2** | 性能/可用 | nginx 缺静态资源缓存 | 每次加载重下 JS/CSS，弱网下体验劣化、成本上升 | `expires 1y` + `Cache-Control: public, immutable` |

---

## 3. SSRF 纵深防御

### 3.1 三层结构

| 层 | 实现 | 拦截的威胁 |
|---|---|---|
| L1 策略校验 | `validate_public_url()` | 协议白名单、私网/环回/链路本地/保留地址黑名单、重定向前预检 |
| L2 解析层守卫 | `install_guarded_resolver()` 包裹 `socket.getaddrinfo` | **DNS rebinding**：策略校验通过后、实际连接前，域名解析结果被改为私网 |
| L3 逐跳复验 | `safe_get()` 每跳重新执行 L1 + L2 | 重定向到内网（`Location: http://10.0.0.1/`） |

L2 是关键：单纯做 URL 字符串校验存在 **TOCTOU 窗口**（校验时解析到公网，连接时解析到私网）。全局守卫在**实际解析的那一刻**判定地址是否合法，窗口被关闭。

### 3.2 地址分类

显式 IPv4/IPv6 黑名单 + `ipaddress.is_global` 作为兜底，覆盖：

- IPv4：`0.0.0.0/8`、`10/8`、`100.64/10`（CGNAT）、`127/8`、`169.254/16`（含云元数据）、`172.16/12`、`192.168/16`、`224/4`
- IPv6：`::`、`::1`、`fc00::/7`、`fe80::/10`、`ff00::/8`
- 映射形式：IPv4-mapped IPv6、6to4（`2002::/16`）、Teredo（`2001::/32`）、NAT64（`64:ff9b::/96`）——均先还原出内嵌 IPv4 再判定

### 3.3 豁免机制及其风险控制

`trusted_internal_hosts()` 收集三类主机：

1. `TRUSTED_INTERNAL_HOSTS` 显式配置（逗号分隔）
2. `SMTP_HOST` 自动加入
3. `AI_BASE_URL` 的 host 自动加入

**风险控制**：豁免是**按主机精确匹配**的，不是按网段。`trusted_internal_hosts()` 不接受 CIDR，通配符或网段形式不会被展开为整个内网。已由测试断言：信任主机解析成功、非信任主机仍抛 `SSRFError`。

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

## 4. 认证与授权

### 4.1 口令存储

| 项 | 值 |
|---|---|
| 算法 | PBKDF2-HMAC-SHA256 |
| 迭代次数 | **120,000**（断言下限 100,000） |
| 比对 | `hmac.compare_digest`（常量时间） |
| 生成 | `secrets` 模块（CSPRNG） |

### 4.2 JWT

| 项 | 值 |
|---|---|
| 算法 | HS256 |
| 有效期 | 12 小时 |
| 密钥强度 | 生产环境强制 ≥32 字符（`validate_production_config`） |
| 校验 | 签名、过期、`alg` 字段均校验；错误密钥签发的 token 被拒 |

### 4.3 认证边界（审计后）

**公开（产品面，无敏感数据）**：

```
GET /api/events           GET /api/events/{id}      GET /api/calendar
GET /api/stats/dashboard  GET /api/stats/analysis
GET /api/status           GET /api/health
GET /health/live          GET /health/ready         GET /health/scheduler
```

**需认证（运维面 + 全部写操作）**：

```
GET  /api/sources              GET /api/sources/{id}
GET  /api/crawl-tasks           GET /api/crawl-tasks/running
GET  /api/crawl-tasks/{id}      GET /api/crawl-logs
GET  /api/monitor               GET /api/notification-rules
GET  /api/settings
POST /api/notification-rules/{id}/test     ← 会真实外发
```

前端无需改动：`api()` 已自动附加 Bearer token；所有运维页面位于 `RequireAuth` 之后。

### 4.4 暴力破解防护

```
LOGIN_MAX_ATTEMPTS   = 5      # 窗口内失败次数阈值
LOGIN_WINDOW_SECONDS = 300    # 计数窗口
LOGIN_LOCKOUT_SECONDS= 300    # 锁定时长
```

- 计数键：`(客户端 IP, 用户名小写)` —— 防止用一个 IP 锁死所有账号，也防止一个账号被多 IP 无限尝试
- 客户端 IP：优先取 `X-Forwarded-For` 首段（nginx 是唯一入口并覆写该头），缺失时回退 socket peer
- 错误信息对"用户不存在"与"口令错误"**完全一致**，不泄露账号存在性
- 成功登录清零计数
- 失败登录写 `AuditLog`（`action="login_failed"`），可集中观测
- **已知限制**：计数器在进程内存中。多 API 副本时按副本生效——失败尝试仍可通过审计日志集中发现，但锁定不跨副本。如需全局锁定需引入共享存储（Redis 等）。

### 4.5 管理员重置令牌

使用 `secrets.compare_digest` 做常量时间比较（避免令牌比较的时序侧信道）。

---

## 5. 输入与输出安全

### 5.1 存储型 XSS 路径（已修复）

**原路径**：
```
恶意 feed <link>javascript:alert(1)</link>
  → collectors/rss_sitemap.py 存入 EventSource.url
  → services/dedup.py 写入
  → frontend <a href={s.url}> 渲染
  → 浏览器执行脚本（在本站源下）
```

React 18 会转义**文本内容**，但**不会**过滤 `href` 的协议——这是常见的误判点。

**修复（双端）**：

```python
# 后端 app/security.py —— 源头阻断
SAFE_URL_SCHEMES = ("http://", "https://")

def sanitize_url(url: str, max_len: int = 2000) -> str:
    cleaned = "".join(c for c in url
                      if c not in "\r\n\t\x00" and ord(c) >= 32).strip()
    lowered = cleaned.lower()
    if not lowered.startswith(SAFE_URL_SCHEMES):
        head = cleaned.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if ":" in head:      # 携带 scheme 但不在白名单 → 拒绝
            return ""
    return cleaned[:max_len]
```

```ts
// 前端 frontend/src/api.ts —— 渲染兜底
export function safeHref(url: string | null | undefined): string {
  if (!url) return ''
  const cleaned = url.replace(/[\r\n\t\0]/g, '').trim()
  if (!cleaned) return ''
  const lower = cleaned.toLowerCase()
  if (lower.startsWith('http://') || lower.startsWith('https://')) return cleaned
  if (!/^[a-z][a-z0-9+.-]*:/i.test(cleaned)) return cleaned  // 无 scheme 的相对路径
  return ''
}
```

两者均处理大小写混淆（`JaVaScRiPt:`）与控制字符绕过（`java\nscript:`）。已在 `EventDetail.tsx` 与 `SourceDetail.tsx` 两处渲染点接入。

### 5.2 前端 XSS 面整体评估

| 检查项 | 结果 |
|---|---|
| `dangerouslySetInnerHTML` | **未使用** |
| `innerHTML` | **未使用** |
| `eval(` / `new Function` | **未使用** |
| 文本渲染 | React 默认转义 |
| `href={...}` 外部 URL | 已加 `safeHref()` 兜底 |

### 5.3 服务端输入处理

| 输入 | 处理 |
|---|---|
| 爬取正文 | `sanitize_text()` —— 去控制字符、长度上限 20000 |
| 标题 | 上限 500 |
| URL | 上限 2000 + 协议白名单 |
| 通知规则配置 | 密钥字段掩码返回；`SECRET_KEY_HINTS` 识别 password/token/key/secret/webhook_url/bot_token/wecom_webhook_key |
| 错误信息 | 截断至 300 字符，防信息泄漏 |

---

## 6. CORS 与响应头

### 6.1 CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"])

def allowed_origins() -> list[str]:
    if settings.ALLOWED_ORIGINS.strip():
        return [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    if is_production(settings.ENV):
        raise SystemExit("⛔ 生产环境必须显式配置 ALLOWED_ORIGINS（逗号分隔），拒绝默认通配")
    return DEV_ORIGINS
```

- 生产环境**未配置 `ALLOWED_ORIGINS` 时进程直接退出**，不会静默回落到通配符
- `allow_credentials=True` 配合 `allow_origins=["*"]` 是 Starlette 的已知危险组合；本项目生产环境不可能取到 `*`（上条已阻断）
- 已由测试断言源码中不存在 `allow_origins=["*"]`

### 6.2 响应头

| 头 | nginx.prod.conf | nginx.tls.conf |
|---|---|---|
| `X-Content-Type-Options: nosniff` | ✅ | ✅ |
| `X-Frame-Options: DENY` | ✅ | ✅ |
| `Referrer-Policy: strict-origin-when-cross-origin` | ✅ | ✅ |
| `Strict-Transport-Security: max-age=31536000` | — | ✅ |

**缺口（如实记录）**：未设置 `Content-Security-Policy`。本项目为纯 SPA 且无 `innerHTML`，当前风险低，但建议后续补 `default-src 'self'` 策略。

**缺口**：应用层未启用 `TrustedHostMiddleware`（Host 头校验）。nginx 是唯一入口时影响有限，但若 API 端口被直接暴露则建议补上。

---

## 7. 依赖漏洞

### 7.1 Python — pip-audit 2.10.1（实际运行）

```
$ pip_audit -r requirements.txt --progress-spinner off
No known vulnerabilities found

$ pip_audit --local --progress-spinner off --skip-editable
初次：3 项 → 全部位于构建工具链（certifi / pip），不在应用依赖中
处置：pip install --upgrade "certifi>=2026.10.19" "pip>=26.2"
复扫：无已知漏洞
```

### 7.2 Python 依赖版本（已全部固定）

```
fastapi==0.146.0            sqlalchemy==2.1.5          pydantic==2.14.1
pydantic-settings==2.16.0  uvicorn==0.54.0            alembic==1.20.0
APScheduler==3.11.3         httpx==0.28.1              PyJWT==2.15.1
cryptography==50.0.2        psycopg[binary]==3.2.13    feedparser==6.0.14
beautifulsoup4==4.15.0      lxml==6.1.3                python-multipart==0.0.32
```

固定版本同时提供**可复现构建**与**供应链完整性**：任何一次安装得到的都是被本次审计验证过的那一组。

### 7.3 前端 — npm audit

```
$ npm audit --omit=dev --audit-level=high
2 moderate severity vulnerabilities
```

| 公告 | 描述 | 判定 | 依据 |
|---|---|---|---|
| GHSA-wrjc-x8rr-h8h6 | 反斜杠开放重定向 | **不可达** | 已逐一核查全部 30+ 处 `<Link to>` / `navigate()` 目标：均为静态字面量或 `/events/${e.id}` 形式的内部数字 ID。无用户可控的外部重定向目标 |
| GHSA-337j-9hxr-rhxg | `deserializeErrors()` 构造器注入 | **不可达** | 纯客户端 SPA（`HashRouter` + `createRoot`），无 SSR、无 hydration 路径 |

**处置**：修复需升级至 `react-router-dom@7.18.4`（破坏性大版本）。基于"风险不可达 + 破坏性升级回归成本更高"的权衡，**已记录为待办而非强制升级**。若后续引入 SSR 或允许用户配置跳转目标，必须立即升级。

---

## 8. 密钥与凭证管理

| 项 | 机制 |
|---|---|
| JWT 密钥 | 仅从环境变量读取；生产强制 ≥32 字符；不入库、不入日志 |
| 管理员口令 | 环境变量注入；生产拒绝默认值且 ≥12 位 |
| DB 口令 | 生产强制 PostgreSQL + ≥16 位口令 |
| `DATABASE_URL` 编码 | `encode_db_url()` 幂等单层百分号编码（避免双重编码导致连不上库） |
| 通知渠道密钥 | 存储时明文（数据库层信任边界内），**API 读取时掩码为 `***`**；编辑时掩码语义化为"未修改"，服务端保留原值 |
| 日志 | 审计详情不含 token；通知错误信息截断 300 字符 |
| 仓库 | `.env` 不入库（`.gitignore`）；`.env.example` 仅占位符 |

**已知缺口**：`DB_URL` / `JWT_SECRET` 等在生产环境应来自密钥管理服务（Vault / KMS / 云厂商 Secrets Manager）而非普通环境变量文件。当前实现接受任意来源的环境变量，运维责任是使用受控注入渠道。

---

## 9. 审计留痕

| 操作 | 记录 |
|---|---|
| 全部数据源增删改 | `AuditLog`（action 区分操作类型） |
| 通知规则增删改 | `AuditLog` |
| 密码变更 | `AuditLog` |
| 登录失败 | `AuditLog`（`action="login_failed"`） |
| 爬取任务与调度 | `CrawlLog` + `CrawlTask` 状态历史 |
| 活动字段变更 | `EventHistory`（记录 old → new） |

已由测试断言：写操作后必有审计记录；审计内容中不含 Bearer token。

---

## 10. 访问控制模型

系统为**单管理员**模型（`ADMIN_USERNAME` / `ADMIN_PASSWORD` 环境变量注入），无多角色 RBAC。

**这一设计的含义**：
- 所有通过认证的用户拥有**相同权限**（即管理员权限）
- 因此认证边界的价值在于"把运维面从公开互联网收回"，而非"区分用户"
- 若未来需要多角色（只读观察者、运营、超级管理员），需引入用户表 + 角色列 + 逐端点依赖

**当前授权依赖的完整性**：审计已核查全部 30+ 端点，除公开产品面外均已加 `Depends(current_user)`，并由 `test_release_hardening.py` 逐一断言（9 个端点无 token → 401）。

---

## 11. 剩余风险登记

| 风险 | 等级 | 影响 | 处置 |
|---|---|---|---|
| Docker / PostgreSQL / TLS / 24h 运行未验证 | **高（发布阻断）** | 生产行为未知 | `RELEASE_CHECKLIST.md` 中列为 BLOCKING，须在有 Docker 与域名的机器上执行 |
| 登录限流为进程内状态 | 中 | 多副本时限流按副本生效 | 失败登录已写审计日志可集中观测；全局限流需引入共享存储 |
| 无 CSP 头 | 中 | 当前无 XSS sink，风险低；引入 `innerHTML` 或第三方脚本后会放大 | 后续补充 `default-src 'self'` |
| 无 `TrustedHostMiddleware` | 低 | Host 头未校验 | API 端口不对外暴露时可接受；否则补上 |
| 密钥来自普通环境变量 | 中 | 容器/主机被攻破时密钥泄露 | 生产应接入 KMS / Secrets Manager |
| react-router 2 项 moderate | 低 | 已验证不可达 | 引入 SSR 或用户可控跳转目标时**必须**升级 v7.18.4 |
| 无 APM / 错误上报 | 中 | 生产问题定位依赖日志 | 建议接入 Sentry 等 |
| `TRUSTED_INTERNAL_HOSTS` 配错会削弱 SSRF 防护 | 中 | 过度信任可能引入内网暴露 | 配置项应最小化；变更需评审 |

---

## 12. 安全结论

**代码与依赖层面：通过。**

- 本次修复 **2 项 P0、7 项 P1、2 项 P2** 安全缺陷，全部有对应回归测试
- SSRF 为三层纵深防护（策略 + 解析层 + 逐跳），覆盖全部出网出口
- 认证边界完整，9 个此前公开的运维端点已收回
- 存储型 XSS 在存储层与渲染层双端阻断
- Python 依赖 **零已知漏洞**；前端 2 项 moderate 已逐一验证不可达并记录理由
- 审计留痕覆盖所有写操作与登录失败

**部署层面：待验证（BLOCKING）。**

Docker 编排、PostgreSQL 真实运行、TLS 证书链、24 小时连续运行这 6 类项目在当前环境**客观无法验证**，已全部标注 **NOT VERIFIED** 并在 `RELEASE_CHECKLIST.md` 中给出具体验证命令与通过标准。它们**未被计入"通过"**，必须由具备条件的一方补做后方可发布。
