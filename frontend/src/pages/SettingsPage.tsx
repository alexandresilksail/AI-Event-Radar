import { useEffect, useState } from 'react'
import { api } from '../api'

export default function SettingsPage() {
  const [s, setS] = useState<any>(null)
  const [status, setStatus] = useState<any>(null)
  const [key, setKey] = useState('')
  const [value, setValue] = useState('{}')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api('/settings').then(setS)
    api('/status').then(setStatus)
  }, [])

  const put = async () => {
    try {
      await api(`/settings/system_setting?key=${encodeURIComponent(key)}`, {
        method: 'PUT', body: value,
      })
      setMsg('✅ 已保存')
    } catch (e: any) { setMsg('❌ ' + e.message) }
  }

  if (!s) return <p className="muted">加载中…</p>
  return (
    <div>
      <h1 className="page-title">系统设置</h1>
      <table style={{ marginBottom: 24 }}>
        <tbody>
          <tr><th style={{ width: 240 }}>运行环境</th><td>{status?.env}</td></tr>
          <tr><th>调度器</th><td>{status?.scheduler.scheduler_running ? `✅ 运行中（${status.scheduler.job_count} 个任务）` : '⛔ 未运行'}</td></tr>
          <tr><th>采集超时</th><td>{s.crawl_timeout_seconds} 秒</td></tr>
          <tr><th>最大并发采集</th><td>{s.max_concurrent_crawls}</td></tr>
          <tr><th>AI 结构化抽取</th><td>{s.ai_configured ? '✅ 已配置（OpenAI 兼容接口）' : '⚠️ 未配置 —— 使用规则抽取，配置 AI_BASE_URL / AI_API_KEY / AI_MODEL 后启用'}</td></tr>
          <tr><th>搜索引擎发现</th><td>{s.search_provider_configured ? '✅ 已配置' : '⚠️ 未配置 —— 需要设置 SEARCH_PROVIDER (serper|tavily|bing) 与 SEARCH_API_KEY，未配置时不会伪装全网搜索'}</td></tr>
          <tr><th>时区</th><td>{s.timezone}（内部统一 UTC 存储）</td></tr>
        </tbody>
      </table>

      <div className="card section" style={{ maxWidth: 640 }}>
        <h3 style={{ marginBottom: 12 }}>写入非敏感系统配置（system_settings 表）</h3>
        <div className="form-row"><label>配置键（敏感凭证如 jwt_secret/smtp_password 已被禁止写入）</label>
          <input value={key} onChange={e => setKey(e.target.value)} placeholder="例如 default_timezone" /></div>
        <div className="form-row"><label>值（JSON）</label>
          <textarea rows={2} value={value} onChange={e => setValue(e.target.value)} /></div>
        <button className="primary" onClick={put}>保存</button>
        {msg && <span style={{ marginLeft: 10, fontSize: 13 }}>{msg}</span>}
      </div>

      <p className="muted" style={{ marginTop: 20, maxWidth: 720 }}>
        安全说明：所有敏感凭证（JWT 密钥、SMTP 密码、AI/搜索 API Key、通知 token）仅保存在服务端 .env
        环境变量中，绝不进入数据库、浏览器或普通日志；本页面接口对凭证字段自动打码（***）。
      </p>
    </div>
  )
}
