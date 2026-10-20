import { useCallback, useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Badge, Card, Empty, ErrorBox, Modal, PageHead, useToast } from '../components/ui'

const EMPTY = { name: '', channel: 'webhook', config: '{}', filters: '{}', enabled: true }

const CHANNELS = [
  { v: 'webhook', label: 'Webhook（通用 HTTP）' },
  { v: 'email', label: '邮件（SMTP）' },
  { v: 'telegram', label: 'Telegram Bot' },
  { v: 'wecom', label: '企业微信机器人' },
]

const configHint: Record<string, string> = {
  webhook: '{"url": "https://你的接收地址/hook"}',
  email: '{"to": ["you@example.com"], "smtp_host": "", "smtp_user": "", "smtp_password": ""}',
  telegram: '{"bot_token": "123456:ABC", "chat_id": "你的chat_id"}',
  wecom: '{"wecom_webhook_key": "企业微信机器人 webhook 的 key"}',
}
const filterHint = '{"free_only": true, "country": "中国", "city": "上海", "keyword": "大模型", "within_days": 7, "official_only": true}'

export default function Notifications() {
  const [data, setData] = useState<any>({ rules: [], recent_logs: [] })
  const [editing, setEditing] = useState<number | null>(null)
  const [form, setForm] = useState<any>(EMPTY)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [dirty, setDirty] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setErr('')
    api('/notification-rules').then(setData).catch(e => setErr(e.message))
  }, [])
  useEffect(() => { load() }, [load])

  const patch = (k: string, v: any) => { setForm((f: any) => ({ ...f, [k]: v })); setDirty(true) }

  const save = async () => {
    if (!form.name.trim()) { setMsg('❌ 请填写规则名称'); return }
    let config: any, filters: any
    try { config = form.config?.trim() ? JSON.parse(form.config) : {} } catch { return setMsg('❌ 通道配置不是合法 JSON') }
    try { filters = form.filters?.trim() ? JSON.parse(form.filters) : {} } catch { return setMsg('❌ 过滤条件不是合法 JSON') }
    setSaving(true); setMsg('')
    try {
      const body = JSON.stringify({ name: form.name, channel: form.channel, enabled: form.enabled, config, filters })
      if (editing) await api(`/notification-rules/${editing}`, { method: 'PUT', body })
      else await api('/notification-rules', { method: 'POST', body })
      toast.show('已保存'); setEditing(null); setDirty(false); load()
    } catch (e: any) { setMsg('❌ ' + (e.message || '保存失败')) }
    finally { setSaving(false) }
  }

  const close = () => {
    if (dirty && !window.confirm('表单尚未保存，确定放弃修改吗？')) return
    setEditing(null); setMsg('')
  }

  const test = async (id: number) => {
    try {
      const t: any = await api(`/notification-rules/${id}/test`, { method: 'POST' })
      toast.show(t.ok ? '测试通知已发送' : `测试失败：${t.error}`, t.ok ? 'ok' : 'err')
    } catch (e: any) { toast.show('测试失败：' + e.message, 'err') }
  }

  return (
    <div>
      <PageHead title="🔔 通知规则"
        desc="配置新活动推送渠道与过滤条件。仅实际接通的通道才会显示发送成功；凭证只存服务端，接口返回打码。"
        extra={<button className="btn primary"
          onClick={() => { setEditing(0); setForm(EMPTY); setMsg(''); setDirty(false) }}>＋ 新增规则</button>} />

      {err && <ErrorBox err={err} onRetry={load} />}

      <Card title="推送规则" className={err ? 'section' : ''}>
        {data.rules.length === 0 ? (
          <Empty icon="🔔" title="暂无通知规则"
            hint="未配置通知时系统仍正常采集与展示数据，只是不会主动推送。"
            action={<button className="btn primary sm"
              onClick={() => { setEditing(0); setForm(EMPTY) }}>＋ 新增规则</button>} />
        ) : (
          <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>ID</th><th>名称</th><th>通道</th><th>过滤条件</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                {data.rules.map((r: any) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td style={{ fontWeight: 600 }}>{r.name}</td>
                    <td className="tiny">{CHANNELS.find(c => c.v === r.channel)?.label || r.channel}</td>
                    <td className="tiny muted" style={{ maxWidth: 260 }}>{JSON.stringify(r.filters)}</td>
                    <td><Badge tone={r.enabled ? 'ok' : ''}>{r.enabled ? '已启用' : '已停用'}</Badge></td>
                    <td className="ops">
                      <button className="btn sm" onClick={() => test(r.id)}>发送测试</button>
                      <button className="btn sm" onClick={() => {
                        setEditing(r.id)
                        setForm({ ...r, config: JSON.stringify(r.config, null, 2), filters: JSON.stringify(r.filters, null, 2) })
                        setDirty(false)
                      }}>编辑</button>
                      <button className="btn sm danger" onClick={() => {
                        if (!window.confirm(`确认删除规则「${r.name}」？`)) return
                        api(`/notification-rules/${r.id}`, { method: 'DELETE' }).then(() => { toast.show('已删除'); load() })
                      }}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="📜 最近通知记录" hint="含去重结果与失败原因" className="section">
        {data.recent_logs.length === 0 ? <Empty icon="📜" title="暂无通知记录" /> : (
          <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>时间</th><th>规则</th><th>活动</th><th>类型</th><th>状态</th><th>错误</th></tr></thead>
              <tbody>
                {data.recent_logs.map((l: any) => (
                  <tr key={l.id}>
                    <td className="tiny muted">{fmt(l.sent_at)}</td>
                    <td>#{l.rule_id}</td>
                    <td className="tiny">{l.event_id ?? '—'}</td>
                    <td className="tiny muted">{l.event_type}</td>
                    <td><Badge tone={l.status === 'sent' ? 'ok' : l.status === 'failed' ? 'err' : ''}>{l.status}</Badge></td>
                    <td className="tiny" style={{ color: l.error ? 'var(--err)' : undefined }}>{l.error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing !== null && (
        <Modal title={editing ? `编辑规则 #${editing}` : '＋ 新增通知规则'} onClose={close}
          footer={<>
            <button className="btn" onClick={close}>取消</button>
            <button className="btn primary" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存'}</button>
          </>}>
          {msg && <div className="error-box" style={{ marginBottom: 14 }}><span>⚠️</span><span>{msg}</span></div>}
          <div className="form-row"><label>规则名称<span className="req">*</span></label>
            <input value={form.name} onChange={e => patch('name', e.target.value)} placeholder="例如：上海免费 AI 活动推送" /></div>
          <div className="form-row"><label>通知通道</label>
            <select value={form.channel} onChange={e => patch('channel', e.target.value)}>
              {CHANNELS.map(c => <option key={c.v} value={c.v}>{c.label}</option>)}
            </select></div>
          <div className="form-row"><label>通道配置（JSON，敏感凭证仅存服务端，接口返回打码）</label>
            <textarea rows={3} className="mono" value={form.config} onChange={e => patch('config', e.target.value)} />
            <span className="tiny muted">示例：{configHint[form.channel]}</span></div>
          <div className="form-row"><label>过滤条件（JSON，全部可选）</label>
            <textarea rows={3} className="mono" value={form.filters} onChange={e => patch('filters', e.target.value)} />
            <span className="tiny muted">示例：{filterHint}</span></div>
          <label className="tiny" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" style={{ width: 16, height: 16 }} checked={form.enabled}
              onChange={e => patch('enabled', e.target.checked)} /> 启用此规则
          </label>
        </Modal>
      )}
      {toast.node}
    </div>
  )
}
