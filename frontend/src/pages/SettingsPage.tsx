import { useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Field, PageHead, useToast } from '../components/ui'

export default function SettingsPage() {
  const [s, setS] = useState<any>(null)
  const [status, setStatus] = useState<any>(null)
  const [key, setKey] = useState('')
  const [value, setValue] = useState('{}')
  const [msg, setMsg] = useState('')
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [newPwd2, setNewPwd2] = useState('')
  const [pwdMsg, setPwdMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  const load = () => {
    api('/settings').then(setS).catch(() => setS(null))
    api('/status').then(setStatus).catch(() => setStatus(null))
  }
  useEffect(load, [])

  const put = async () => {
    setSaving(true); setMsg('')
    try {
      await api(`/settings/system_setting?key=${encodeURIComponent(key)}`, { method: 'PUT', body: value })
      setMsg('✅ 已保存')
      toast.show('配置已保存')
    } catch (e: any) {
      setMsg('❌ ' + e.message)
      toast.show('保存失败：' + e.message, 'err')
    } finally { setSaving(false) }
  }

  const changePwd = async () => {
    if (newPwd !== newPwd2) { setPwdMsg('❌ 两次输入的新密码不一致'); return }
    if (newPwd.length < 10) { setPwdMsg('❌ 新密码至少 10 位'); return }
    setPwdMsg('提交中…')
    try {
      await api('/auth/change-password', { method: 'POST', body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }) })
      setPwdMsg('✅ 已修改，下次登录请使用新密码')
      toast.show('密码已修改')
      setOldPwd(''); setNewPwd(''); setNewPwd2('')
    } catch (e: any) {
      setPwdMsg('❌ ' + e.message)
      toast.show('修改失败：' + e.message, 'err')
    }
  }

  if (!s) return <p className="muted">加载中…</p>
  return (
    <div>
      <PageHead title="系统设置" desc="运行参数、能力开关与管理员安全设置。敏感凭证只保存在服务端 .env，接口一律脱敏返回。" />

      <Card title="运行状态" hint="来自 /status 与 /settings 实时接口" className="section">
        <div className="table-wrap"><table>
          <tbody>
            <tr><th style={{ width: 220 }}>运行环境</th><td><b>{status?.env}</b></td></tr>
            <tr><th>进程角色</th><td>{status?.role === 'api' ? 'API 服务（不启动调度器）' : status?.role === 'worker' ? 'Worker（调度执行）' : '单体模式'}</td></tr>
            <tr><th>调度器</th><td>{status?.scheduler?.scheduler_running
              ? <span className="badge ok">运行中 · {status.scheduler.job_count} 个任务</span>
              : <span className="badge err">未运行</span>}</td></tr>
            <tr><th>采集超时</th><td>{s.crawl_timeout_seconds} 秒</td></tr>
            <tr><th>最大并发采集</th><td>{s.max_concurrent_crawls}</td></tr>
            <tr><th>AI 结构化抽取</th><td>{s.ai_configured
              ? <span className="badge ok">已配置（OpenAI 兼容接口）</span>
              : <span className="badge warn">未配置 —— 当前使用规则抽取；设置 AI_BASE_URL / AI_API_KEY / AI_MODEL 后自动启用</span>}</td></tr>
            <tr><th>搜索引擎发现</th><td>{s.search_provider_configured
              ? <span className="badge ok">已配置</span>
              : <span className="badge warn">未配置 —— 需要 SEARCH_PROVIDER (serper|tavily|bing) 与 SEARCH_API_KEY；未配置时不会伪装全网搜索</span>}</td></tr>
            <tr><th>时区</th><td>{s.timezone}（内部统一 UTC 存储，展示时本地化）</td></tr>
          </tbody>
        </table></div>
      </Card>

      <div className="grid cols-2">
        <Card title="管理员密码" className="section" hint="生产环境请立即修改初始密码">
          <Field label="当前密码" required>
            <input type="password" value={oldPwd} onChange={e => setOldPwd(e.target.value)} autoComplete="current-password" />
          </Field>
          <Field label="新密码（至少 10 位）" required>
            <input type="password" value={newPwd} onChange={e => setNewPwd(e.target.value)} autoComplete="new-password" />
          </Field>
          <Field label="确认新密码" required>
            <input type="password" value={newPwd2} onChange={e => setNewPwd2(e.target.value)} autoComplete="new-password" />
          </Field>
          <button className="btn primary" onClick={changePwd} disabled={!oldPwd || !newPwd}>修改密码</button>
          {pwdMsg && <p className="tiny" style={{ marginTop: 10 }}>{pwdMsg}</p>}
          <p className="tiny muted" style={{ marginTop: 10 }}>
            忘记密码：在服务器执行 <code>docker compose run --rm api python -m app.reset_password</code> 或按 docs/SECURITY.md 的重置流程操作。
          </p>
        </Card>

        <Card title="写入非敏感系统配置" className="section" hint="system_settings 表；敏感键被禁止写入">
          <Field label="配置键">
            <input value={key} onChange={e => setKey(e.target.value)} placeholder="例如 default_timezone" />
          </Field>
          <Field label="值（JSON）">
            <textarea rows={3} value={value} onChange={e => setValue(e.target.value)} />
          </Field>
          <button className="btn primary" onClick={put} disabled={saving || !key}>{saving ? '保存中…' : '保存'}</button>
          {msg && <span className="tiny" style={{ marginLeft: 10 }}>{msg}</span>}
          <p className="tiny muted" style={{ marginTop: 10 }}>
            敏感键（jwt_secret、smtp_password、api_key、token 等）禁止写入数据库，如需修改请编辑服务器 .env 后重启。
          </p>
        </Card>
      </div>

      <p className="tiny muted" style={{ marginTop: 18 }}>
        安全说明：JWT 密钥、SMTP 密码、AI/搜索 API Key、通知 token 仅保存在服务端环境变量中，
        绝不进入数据库、浏览器或普通日志；所有接口对凭证字段自动打码（***）。
      </p>
    </div>
  )
}
