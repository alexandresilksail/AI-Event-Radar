import { useCallback, useEffect, useState } from 'react'
import { api, fmt } from '../api'

export default function Logs() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [f, setF] = useState({ date: '', source_id: '', status: '' })
  const [page, setPage] = useState(1)

  const load = useCallback(() => {
    const p = new URLSearchParams({ page: String(page), page_size: '50' })
    Object.entries(f).forEach(([k, v]) => v && p.set(k, v))
    api(`/crawl-logs?${p}`).then(r => { setItems(r.items); setTotal(r.total) })
  }, [f, page])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <h1 className="page-title">采集任务日志（共 {total} 条）</h1>
      <div className="filters">
        <input type="date" value={f.date} onChange={e => { setPage(1); setF({ ...f, date: e.target.value }) }} />
        <input placeholder="数据源 ID" value={f.source_id} onChange={e => { setPage(1); setF({ ...f, source_id: e.target.value }) }} style={{ width: 110 }} />
        <select value={f.status} onChange={e => { setPage(1); setF({ ...f, status: e.target.value }) }}>
          <option value="">全部状态</option><option value="success">成功</option><option value="failed">失败</option>
        </select>
        <button onClick={() => setF({ date: '', source_id: '', status: '' })}>重置</button>
      </div>
      <table>
        <thead><tr>
          <th>日志 ID</th><th>任务 ID</th><th>数据源</th><th>开始</th><th>结束</th><th>结果</th>
          <th>新发现</th><th>更新</th><th>重复</th><th>重试</th><th>失败原因</th>
        </tr></thead>
        <tbody>
          {items.map(l => (
            <tr key={l.id}>
              <td>{l.id}</td><td>{l.task_id ?? '—'}</td><td>{l.source_name}</td>
              <td>{fmt(l.started_at)}</td><td>{fmt(l.finished_at)}</td>
              <td><span className={`badge ${l.status === 'success' ? 'ok' : 'fail'}`}>{l.status}</span></td>
              <td>{l.found_count}</td><td>{l.updated_count}</td><td>{l.duplicate_count}</td>
              <td>{l.retry_count}</td>
              <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={l.error}>{l.error}</td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={11} className="muted">暂无日志记录</td></tr>}
        </tbody>
      </table>
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span className="muted" style={{ padding: 6 }}>第 {page} 页</span>
        <button disabled={items.length < 50} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
    </div>
  )
}
