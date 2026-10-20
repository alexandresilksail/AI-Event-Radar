import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import { Empty, ErrorBox, Pager, PageHead, SkeletonRows } from '../components/ui'

export const confBadge = (lv: string) =>
  `badge ${lv === 'A' ? 'a' : lv === 'B' ? 'b' : lv === 'C' ? 'c' : 'pending'}`

export default function Events() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [f, setF] = useState({ q: '', country: '', city: '', type: '', confidence: '', finished: '', free: '' })
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const params = new URLSearchParams({ page: String(page), page_size: '20' })
    Object.entries(f).forEach(([k, v]) => v && params.set(k, v))
    api(`/events?${params}`)
      .then(r => { setItems(r.items); setTotal(r.total) })
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [f, page])

  useEffect(() => { load() }, [load])

  const set = (k: string, v: string) => { setPage(1); setF(p => ({ ...p, [k]: v })) }
  const pages = Math.max(1, Math.ceil(total / 20))

  return (
    <div>
      <PageHead title="🎯 活动情报中心"
        desc={`共 ${total} 条活动 · 全部来自真实采集与结构化抽取，未核实字段显示「待核实」`} />

      <div className="filters">
        <input className="grow" placeholder="🔍 搜索活动名称 / 主办方 / 关键词"
          value={f.q} onChange={e => set('q', e.target.value)} />
        <input placeholder="国家" style={{ width: 96 }} value={f.country} onChange={e => set('country', e.target.value)} />
        <input placeholder="城市" style={{ width: 96 }} value={f.city} onChange={e => set('city', e.target.value)} />
        <select value={f.confidence} onChange={e => set('confidence', e.target.value)}>
          <option value="">全部可信度</option><option>A</option><option>B</option><option>C</option><option>待核实</option>
        </select>
        <select value={f.finished} onChange={e => set('finished', e.target.value)}>
          <option value="">进行 / 已结束</option><option value="false">进行中</option><option value="true">已结束</option>
        </select>
        <select value={f.free} onChange={e => set('free', e.target.value)}>
          <option value="">免费 / 收费</option><option value="true">免费</option><option value="false">收费</option>
        </select>
        <button className="btn sm" onClick={() => setF({ q: '', country: '', city: '', type: '', confidence: '', finished: '', free: '' })}>重置</button>
      </div>

      {err && <ErrorBox err={err} onRetry={load} />}
      {loading && <div className="card"><SkeletonRows n={6} /></div>}

      {!loading && !err && items.length === 0 && (
        <Empty icon="🛰" title="暂无符合条件的数据"
          hint="系统不会生成模拟数据——请前往「数据源管理」添加数据源并触发采集。"
          action={<Link className="btn primary sm" to="/sources">去添加数据源</Link>} />
      )}

      {!loading && items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr>
              <th style={{ minWidth: 220 }}>活动</th><th>类型</th><th>时间</th><th>地点</th>
              <th>主办方</th><th>可信度</th><th>来源</th>
            </tr></thead>
            <tbody>
              {items.map((e: any) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/events/${e.id}`} style={{ fontWeight: 600 }}>{e.name}</Link>
                    <div style={{ marginTop: 3, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                      {e.is_finished && <span className="badge">已结束</span>}
                      {e.is_free === true && <span className="badge ok">免费</span>}
                      {e.online === true && <span className="badge info">线上</span>}
                    </div>
                  </td>
                  <td className="tiny">{e.type}</td>
                  <td className="tiny">{e.start_time ? fmt(e.start_time) : <span className="muted">待核实</span>}</td>
                  <td className="tiny muted">{e.country} {e.city}</td>
                  <td className="tiny muted">{e.organizer || '—'}</td>
                  <td><span className={confBadge(e.confidence_level)}>{e.confidence_level}</span></td>
                  <td className="num">×{e.sources.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && total > 0 && <Pager page={page} pages={pages} total={total} onPage={setPage} />}
    </div>
  )
}
