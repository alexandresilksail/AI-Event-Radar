import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'

export const confBadge = (lv: string) =>
  `badge ${lv === 'A' ? 'a' : lv === 'B' ? 'b' : lv === 'C' ? 'c' : 'pending'}`

export default function Events() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [f, setF] = useState({ q: '', country: '', city: '', type: '', confidence: '', finished: '', free: '' })
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '20' })
    Object.entries(f).forEach(([k, v]) => v && params.set(k, v))
    api(`/events?${params}`).then(r => { setItems(r.items); setTotal(r.total) }).catch(e => setErr(e.message))
  }, [f, page])

  useEffect(() => { load() }, [load])

  const set = (k: string, v: string) => { setPage(1); setF(p => ({ ...p, [k]: v })) }
  const pages = Math.max(1, Math.ceil(total / 20))

  return (
    <div>
      <h1 className="page-title">活动情报中心（共 {total} 条 · 全部来自真实采集数据）</h1>
      <div className="filters">
        <input placeholder="🔍 关键词" value={f.q} onChange={e => set('q', e.target.value)} style={{ width: 180 }} />
        <input placeholder="国家" value={f.country} onChange={e => set('country', e.target.value)} style={{ width: 90 }} />
        <input placeholder="城市" value={f.city} onChange={e => set('city', e.target.value)} style={{ width: 90 }} />
        <select value={f.confidence} onChange={e => set('confidence', e.target.value)}>
          <option value="">全部可信度</option><option>A</option><option>B</option><option>C</option><option>待核实</option>
        </select>
        <select value={f.finished} onChange={e => set('finished', e.target.value)}>
          <option value="">进行/已结束</option><option value="false">进行中</option><option value="true">已结束</option>
        </select>
        <select value={f.free} onChange={e => set('free', e.target.value)}>
          <option value="">免费/收费</option><option value="true">免费</option><option value="false">收费</option>
        </select>
        <button onClick={() => setF({ q: '', country: '', city: '', type: '', confidence: '', finished: '', free: '' })}>重置</button>
      </div>
      {err && <p style={{ color: 'var(--red)' }}>{err}</p>}
      {items.length === 0 && <p className="muted">暂无符合条件的数据。系统不会生成模拟数据——请添加数据源并触发采集。</p>}
      {items.map(e => (
        <div className="event-card" key={e.id}>
          <h3><Link to={`/events/${e.id}`}>{e.name}</Link> <span className={confBadge(e.confidence_level)}>可信度 {e.confidence_level}</span>
            {e.is_finished && <span className="badge paused">已结束</span>}
            {e.is_free === true && <span className="badge ok">免费</span>}</h3>
          <div className="meta">
            <span>🏷 {e.type}</span>
            <span>🕒 {e.start_time ? fmt(e.start_time) : '时间待核实'}</span>
            <span>📍 {e.country} {e.city}{e.online ? '（线上）' : ''}</span>
            <span>🏛 {e.organizer}</span>
            <span>🔗 来源 ×{e.sources.length}</span>
          </div>
        </div>
      ))}
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span className="muted" style={{ padding: 6 }}>第 {page} / {pages} 页</span>
        <button disabled={page >= pages} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
    </div>
  )
}
