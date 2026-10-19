import { useCallback, useEffect, useState } from 'react'
import { api, fmt } from '../api'

const EMPTY = { name: '', channel: 'webhook', config: {} as any, filters: {} as any, enabled: true }

export default function Notifications() {
  const [data, setData] = useState<any>({ rules: [], recent_logs: [] })
  const [editing, setEditing] = useState<any | null>(null)
  const [form, setForm] = useState<any>(EMPTY)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => { api('/notification-rules').then(setData) }, [])
  useEffect(() => { load() }, [load])

  const save = async () => {
    try {
      const body = JSON.stringify({
        name: form.name, channel: form.channel, enabled: form.enabled,
        config: typeof form.config === 'string' ? JSON.parse(form.config || '{}') : form.config,
        filters: typeof form.filters === 'string' ? JSON.parse(form.filters || '{}') : form.filters,
      })
      if (editing) await api(`/notification-rules/${editing}`, { method: 'PUT', body })
      else await api('/notification-rules', { method: 'POST', body })
      setEditing(null); setForm(EMPTY); setMsg('✅ 已保存'); load()
    } catch (e: any) { setMsg('❌ ' + e.message) }
  }

  const configHint: Record<string, string> = {
    webhook: '{"url": "https://你的接收地址/hook"}',
    email: '{"to": ["you@example.com"], "smtp_host": "", "smtp_user": "", "smtp_password": ""}',
    telegram: '{"bot_token": "123456:ABC", "chat_id": "你的chat_id"}',
    wecom: '{"wecom_webhook_key": "企业微信机器人 webhook 的 key"}',
  }
  const filterHint = '{"free_only": true, "country": "中国", "city": "上海", "keyword": "大模型", "within_days": 7, "official_only": true}'

  return (
    <div>
      <h1 className="page-title">通知规则
        <button className="primary" style={{ marginLeft: 12 }} onClick={() => { setEditing(0); setForm(EMPTY) }}>＋ 新增规则</button>
      </h1>
      {msg && <p style={{ marginBottom: 10 }}>{msg}</p>}
      <table style={{ marginBottom: 24 }}>
        <thead><tr><th>ID</th><th>名称</th><th>通道</th><th>过滤条件</th><th>启用</th><th>操作</th></tr></thead>
        <tbody>
          {data.rules.map((r: any) => (
            <tr key={r.id}>
              <td>{r.id}</td><td>{r.name}</td><td>{r.channel}</td>
              <td className="muted" style={{ maxWidth: 320 }}>{JSON.stringify(r.filters)}</td>
              <td>{r.enabled ? '✅' : '⏸'}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button onClick={() => api(`/notification-rules/${r.id}/test`, { method: 'POST' })
                  .then(t => setMsg(t.ok ? '✅ 测试通知已发送' : `❌ ${t.error}`))}>发送测试</button>{' '}
                <button onClick={() => { setEditing(r.id); setForm({ ...r, config: JSON.stringify(r.config, null, 2), filters: JSON.stringify(r.filters, null, 2) }) }}>编辑</button>{' '}
                <button className="danger" onClick={() => api(`/notification-rules/${r.id}`, { method: 'DELETE' }).then(load)}>删除</button>
              </td>
            </tr>
          ))}
          {data.rules.length === 0 && <tr><td colSpan={6} className="muted">暂无规则。未配置通知时，系统仍正常采集与展示数据。</td></tr>}
        </tbody>
      </table>

      <h3 style={{ marginBottom: 10 }}>最近通知记录（含去重与失败原因）</h3>
      <table>
        <thead><tr><th>时间</th><th>规则</th><th>事件 ID</th><th>类型</th><th>状态</th><th>错误</th></tr></thead>
        <tbody>
          {data.recent_logs.map((l: any) => (
            <tr key={l.id}><td>{fmt(l.sent_at)}</td><td>{l.rule_id}</td><td>{l.event_id ?? '—'}</td>
              <td>{l.event_type}</td>
              <td><span className={`badge ${l.status === 'sent' ? 'ok' : l.status === 'failed' ? 'fail' : 'paused'}`}>{l.status}</span></td>
              <td className="muted">{l.error}</td></tr>
          ))}
          {data.recent_logs.length === 0 && <tr><td colSpan={6} className="muted">暂无通知记录</td></tr>}
        </tbody>
      </table>

      {editing !== null && (
        <div className="modal" onClick={() => setEditing(null)}>
          <div className="box" onClick={e => e.stopPropagation()}>
            <h3 style={{ marginBottom: 14 }}>{editing ? '编辑规则' : '新增规则'}</h3>
            <div className="form-row"><label>规则名称</label>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
            <div className="form-row"><label>通知通道</label>
              <select value={form.channel} onChange={e => setForm({ ...form, channel: e.target.value })}>
                <option value="webhook">Webhook (通用 HTTP)</option><option value="email">邮件 (SMTP)</option>
                <option value="telegram">Telegram Bot</option><option value="wecom">企业微信机器人</option>
              </select></div>
            <div className="form-row"><label>通道配置（JSON，敏感凭证仅存服务端，API 返回时打码）示例：{configHint[form.channel]}</label>
              <textarea rows={3} value={form.config} onChange={e => setForm({ ...form, config: e.target.value })} /></div>
            <div className="form-row"><label>过滤条件（JSON，全部可选）示例：{filterHint}</label>
              <textarea rows={3} value={form.filters} onChange={e => setForm({ ...form, filters: e.target.value })} /></div>
            <label style={{ fontSize: 13 }}><input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} /> 启用</label>
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button className="primary" onClick={save}>保存</button>
              <button onClick={() => setEditing(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
