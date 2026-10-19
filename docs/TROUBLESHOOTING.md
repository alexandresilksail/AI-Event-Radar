# 常见故障排查手册

| 现象 | 原因 | 处理 |
|---|---|---|
| 采集失败 `SSRF 防护拦截` | 目标是内网/保留地址或 DNS 解析失败 | 确认 URL 为公网地址；企业内网源需自行评估后通过官方 API 接入 |
| 采集失败 `HTTP 403/429` | 目标站限流或拒绝爬虫 | 调大 frequency_minutes；更换官方 RSS/API；遵守 robots.txt，不要加大频率硬试 |
| 采集失败 `list_selector 未匹配` | 页面结构变化 | 编辑数据源更新选择器；先用「测试」按钮验证 |
| 调度器显示未运行 | `.env` 中 `SCHEDULER_ENABLED=false` | 置为 true 并重启 |
| 重启后任务丢失感 | 误判 | 任务由数据库恢复（启动时 reschedule_all），可在 /status 查看已排定任务数 |
| 活动时间全是「待核实」 | 原文未写明具体日期，规则抽取不编造 | 属预期行为；配置 AI_BASE_URL/AI_API_KEY/AI_MODEL 提升抽取能力 |
| 搜索发现报 `搜索引擎未配置` | 未配 SEARCH_API_KEY | 配置 serper/tavily/bing 任一 Key；未配置时系统不会伪装全网搜索 |
| 通知状态 `skipped_no_credentials` | 通道凭证不全 | 按错误提示补全 config（如 SMTP 四项/Telegram token+chat_id） |
| 通知重复/漏发 | 去重键冲突 | 去重键为 (rule, event, type)，同一活动同一类型只发一次；变更会以新 event_type 触发 |
| 事件页无数据 | 尚未采集或源内容非活动 | 新增源后点「▶ 采集」；确认内容与 AI 活动相关（非 AI 内容会被过滤） |
| 数据库锁错误（SQLite） | 并发写 | 生产环境切换 PostgreSQL（compose 已内置） |
| 前端 401 跳登录 | token 过期（12h） | 重新登录 |
