# 已知限制与后续扩展（v1.1）

## 依赖外部条件才能启用（框架已就绪，绝不伪装已实现）

1. **AI 结构化抽取**：需配 OpenAI 兼容 `AI_BASE_URL/AI_API_KEY/AI_MODEL`；未配置时仅规则抽取并清晰标注当前模式。
2. **搜索引擎全网发现**：需配 `SEARCH_PROVIDER`（serper/tavily/bing）+ Key；未配置时明确提示。
3. **通知通道凭证**：邮件四项 / Telegram token+chat / 企业微信 key；未配置记 skipped 不崩溃。
4. **Eventbrite/Meetup 官方 API**：APICollector 框架可直接配置，需账号与配额。
5. **微信公众号**：受访问控制，只支持人工提交单条 URL，不批量抓取。

## 已知限制

- 去重为字面相似度+规则加权；语义级去重预留 LLM 增强钩子；策略为「合并不删除」。
- 取消/延期依赖重新抓到原文更新；自动「已结束」按时间判定。
- 调度为单机 DB 领导锁（无 Redis/Celery）——单机部署与小型集群够用；
  多机大规模场景建议后续引入 Redis 分布式锁。
- 24 小时稳定性验收**未执行**（方案见 ACCEPTANCE.md，需部署后真实运行）。
- HTTPS 由边缘层（Caddy/cloudflared/LB）负责，compose 内 Nginx 仅 80。

## 后续扩展

- Alembic 增量迁移随字段演进入库。
- 日/韩/德/法/西语规则扩展。
- 前端 i18n 与组件库升级。
