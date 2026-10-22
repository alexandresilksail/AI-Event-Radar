# 已知限制与后续扩展（v1.4）

## 依赖外部条件才能启用（框架已就绪，绝不伪装已实现）

1. **AI 结构化抽取**：需配 OpenAI 兼容 `AI_BASE_URL/AI_API_KEY/AI_MODEL`；未配置时仅规则抽取并清晰标注当前模式。
2. **搜索引擎全网发现**：需配 `SEARCH_PROVIDER`（serper/tavily/bing）+ Key；未配置时明确提示。
3. **通知通道凭证**：邮件四项 / Telegram token+chat / 企业微信 key；未配置记 skipped 不崩溃。
4. **Eventbrite/Meetup 官方 API**：APICollector 框架可直接配置，需账号与配额。
5. **微信公众号**：受访问控制，只支持人工提交单条 URL，不批量抓取。
6. **内网端点访问**：SMTP 中继与内部 LLM 网关需加入 `TRUSTED_INTERNAL_HOSTS`（精确到主机，不接受网段）。

## 已知限制

- 去重为字面相似度+规则加权；语义级去重预留 LLM 增强钩子；策略为「合并不删除」。
- 取消/延期依赖重新抓到原文更新；自动「已结束」按时间判定。
- 调度为单机 DB 领导锁（无 Redis/Celery）——单机部署与小型集群够用；
  多机大规模场景建议后续引入 Redis 分布式锁。
- **登录限流为进程内状态**：多 API 副本时限流按副本生效；失败尝试已写审计日志可集中观测，
  但跨副本全局锁定需引入 Redis 等共享存储。
- **单管理员模型**：所有通过认证的用户拥有相同（管理员）权限，无多角色 RBAC。
- **密钥来自普通环境变量**：生产建议接入 KMS / Secrets Manager 等密钥管理服务。
- **未设置 `Content-Security-Policy`**：当前前端无 `innerHTML`/`eval`，风险低；
  引入第三方脚本或 `innerHTML` 后必须补齐。
- **未启用 `TrustedHostMiddleware`**：API 端口不对外暴露时可接受，否则建议补上。
- **真实网络 E2E 已隔离**：默认运行排除 `real_network` marker（上游故障不冒充应用缺陷）；
  需真实网络时用 `pytest -m real_network`。
- **24 小时稳定性验收未执行**（v1.4 发布验收环境无 Docker、无域名、会话时长受限）——
  方案与通过标准见 `RELEASE_CHECKLIST.md` §十一、`ACCEPTANCE.md`。
- HTTPS 由边缘层（Caddy/cloudflared/LB）负责，compose 内 Nginx 仅 80；
  `nginx.tls.conf` 已提供 TLS 配置模板，但**未经真实证书实测**。

## 后续扩展

- Alembic 增量迁移随字段演进入库。
- 日/韩/德/法/西语规则扩展。
- 前端 i18n 与组件库升级。
- `react-router-dom` 升级至 v7.18.4（当前 2 项 moderate 公告已验证不可达；
  引入 SSR 或用户可控跳转目标时必须升级）。
- 引入 Sentry / Prometheus 等 APM 与错误上报。

## 相关文档

- `RELEASE_AUDIT.md` —— v1.4 十阶段发布验收审计报告
- `RELEASE_CHECKLIST.md` —— 发布前逐项清单（含 BLOCKING 与 NOT VERIFIED 项）
- `TEST_REPORT.md` —— 逐文件测试明细与复现命令
- `SECURITY_AUDIT.md` —— 威胁建模、漏洞清单、依赖扫描原始输出

