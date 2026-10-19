import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const EMPTY = { name: '', type: 'rss', url: '', country: '未知', language: 'zh', frequency_minutes: 60, compliance_note: '', method: {} as any }

export default function Sources() {
  const [items, setItems] = useState<any[]>([])
  const [editing, setEditing] = useState<any | null>(null)
  const [form, setForm] = useState<any>(EMPTY)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(0)

  const load = useCallback(() => { api('/sources').then(r => setItems(r.items)) }, [])
  useEffect(() => { load() }, [load])

  const act = async (path: string, ok: string) => {
    setMsg('')
    try { await api(path, { method: 'POST' }); setMsg(ok); load() } catch (e: any) { setMsg(e.message) }
  }
  const save = async () => {
    setMsg('')
    const body = JSON.stringify({ ...form, method: typeof form.method === 'string' ? safeJson(form.method) : form.method })
    try {
      if (editing) await api(`/sources/${editing}`, { method: 'PUT', body })
      else await api('/sources', { method: 'POST', body })
      setEditing(null); setForm(EMPTY); setMsg('✅ 已保存'); load()
    } catch (e: any) { setMsg('❌ ' + e.message) }
  }
  const trigger = async (id: number) => {
    setBusy(id); setMsg('采集中…（含自动重试）')
    try {
      const r = await api(`/sources/${id}/trigger`, { method: 'POST' })
      setMsg(r.status === 'success'
        ? `✅ 采集成功：抓取 ${r.found} 条，新活动 ${r.new_events}，更新 ${r.updated}，重复 ${r.duplicates}`
        : `❌ 采集失败：${r.error}`)
    } catch (e: any) { setMsg('❌ ' + e.message) }
    setBusy(0); load()
  }

  const statusBadge = (s: any) =>
    `badge ${s.status === 'active' ? 'ok' : s.status === 'failing' ? 'fail' : s.status === 'paused' ? 'paused' : 'candidate'}`

  return (
    <div>
      <h1 className="page-title">数据源监控
        <button className="primary" style={{ marginLeft: 12 }} onClick={() => { setEditing(null); setForm(EMPTY); setEditing(0) }}>＋ 新增数据源</button>
      </h1>
      {msg && <p style={{ marginBottom: 10, fontSize: 13 }}>{msg}</p>}
      <table>
        <thead><tr>
          <th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>频率</th><th>最近成功</th><th>连续失败</th>
          <th>最近抓取</th><th>累计活动</th><th>操作</th>
        </tr></thead>
        <tbody>
          {items.map(s => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td title={s.url}>{s.name}{s.status === 'candidate' && <div className="muted">候选：{s.discovery_path}</div>}</td>
              <td>{s.type}</td>
              <td><span className={statusBadge(s)}>{s.enabled ? s.status : 'paused'}</span></td>
              <td>{s.frequency_minutes}min</td>
              <td>{s.last_success_at ? new Date(s.last_success_at).toLocaleString('zh-CN', { hour12: false }) : '—'}</td>
              <td>{s.consecutive_failures > 0 ? <span className="badge fail">{s.consecutive_failures} 次</span> : '0'}
                {s.last_error && <div className="muted" title={s.last_error}>{s.last_error.slice(0, 40)}…</div>}</td>
              <td>{s.last_item_count}</td>
              <td>{s.total_events_found}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button onClick={() => trigger(s.id)} disabled={busy === s.id}>{busy === s.id ? '采集中' : '▶ 采集'}</button>{' '}
                <button onClick={() => api(`/sources/${s.id}/test`, { method: 'POST' }).then(r => setMsg(r.ok ? `✅ 连通性正常（${r.items} 条）` : `❌ ${r.error}`))}>测试</button>{' '}
                {s.enabled
                  ? <button onClick={() => act(`/sources/${s.id}/pause`, '已暂停')}>暂停</button>
                  : s.status === 'candidate'
                    ? <button onClick={() => act(`/sources/${s.id}/approve`, '已审核启用')}>✓ 审核启用</button>
                    : <button onClick={() => act(`/sources/${s.id}/resume`, '已恢复')}>恢复</button>}{' '}
                <button className="danger" onClick={() => { if (confirm(`确认删除数据源「${s.name}」？`)) api(`/sources/${s.id}`, { method: 'DELETE' }).then(load) }}>删除</button>{' '}
                <button onClick={() => { setEditing(s.id); setForm({ ...EMPTY, ...s, method: JSON.stringify(s.method || {}) }) }}>编辑</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing !== null && (
        <div className="modal" onClick={() => setEditing(null)}>
          <div className="box" onClick={e => e.stopPropagation()}>
            <h3 style={{ marginBottom: 14 }}>{editing ? '编辑数据源' : '新增数据源'}</h3>
            {([['name', '数据源名称'], ['url', '网站地址 (URL)'], ['country', '国家/地区'], ['language', '语言 (zh/en)'], ['frequency_minutes', '采集频率（分钟，最低 5）'], ['compliance_note', '合规及访问备注']] as any[]).map(([k, label]) => (
              <div className="form-row" key={k}>
                <label>{label}</label>
                <input value={form[k]} onChange={e => setForm({ ...form, [k]: k === 'frequency_minutes' ? Number(e.target.value) : e.target.value })} />
              </div>
            ))}
            <div className="form-row">
              <label>采集类型</label>
              <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
                <option value="rss">RSS / Atom 订阅</option><option value="html">HTML 网页解析</option>
                <option value="api">官方 API (JSON)</option><option value="sitemap">Sitemap</option>
                <option value="change_detection">页面变更检测</option><option value="search">搜索引擎发现</option>
              </select>
            </div>
            <div className="form-row">
              <label>解析配置 method（JSON，HTML 源可配 list_selector/title/link 等；可留空 {"{}"}）</label>
              <textarea rows={4} value={typeof form.method === 'string' ? form.method : JSON.stringify(form.method, null, 2)}
                onChange={e => setForm({ ...form, method: e.target.value })} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="primary" onClick={save}>保存</button>
              <button onClick={() => setEditing(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function safeJson(s: string) {
  try { return JSON.parse(s || '{}') } catch { return {} }
}
