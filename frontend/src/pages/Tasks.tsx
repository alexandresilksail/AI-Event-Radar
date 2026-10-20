import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import {
  Empty, ErrorBox, PageHead, Pager, SkeletonRows,
  taskLabel, taskTone, triggerLabel, useToast,
} from '../components/ui'

const STATUS = [
  { v: '', l: '全部状态' }, { v: 'running', l: '执行中' }, { v: 'success', l: '成功' },
  { v: 'partial', l: '部分成功' }, { v: 'failed', l: '失败' },
]
const TRIGGERS = [
  { v: '', l: '全部触发方式' }, { v: 'manual', l: '手动触发' },
  { v: 'scheduled', l: '定时调度' }, { v: 'retry', l: '失败重试' },
]

export default function Tasks() {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')
  const [status, setStatus] = useState('')
  const [trigger, setTrigger] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [sources, setSources] = useState<any[]>([])
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setErr('')
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (status) p.set('status', status)
    if (trigger) p.set('trigger', trigger)
    if (sourceId) p.set('source_id', sourceId)
    api(`/crawl-tasks?${p}`).then(setData).catch(e => setErr(e.message))
  }, [status, trigger, sourceId, page, pageSize])

  useEffect(() => {
    load()
    api('/sources?page_size=200').then((r: any) => setSources(r.items || [])).catch(() => setSources([]))
  }, [load])

  const retry = async (id: number) => {
    setBusy(true)
    try {
      const r: any = await api(`/crawl-tasks/${id}/retry`, { method: 'POST' })
      toast.show(r.status === 'success' || r.status === 'partial'
        ? `重试完成：新增活动 ${r.new_events}` : `重试仍失败：${r.error}`, r.status === 'failed' ? 'err' : 'ok')
      load()
    } catch (e: any) { toast.show('重试失败：' + e.message, 'err') }
    finally { setBusy(false) }
  }

  const pages = Math.max(1, Math.ceil((data?.total || 0) / pageSize))

  return (
    <div>
      <PageHead title="🧾 采集任务日志中心"
        desc="每次采集都是一个可追踪的任务：触发方式、耗时、获取/新增/重复数量、失败原因全记录。"
        extra={<button className="btn sm" onClick={load}>刷新</button>} />

      <div className="filters">
        <select value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}>
          {STATUS.map(s => <option key={s.v} value={s.v}>{s.l}</option>)}
        </select>
        <select value={trigger} onChange={e => { setTrigger(e.target.value); setPage(1) }}>
          {TRIGGERS.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
        </select>
        <select value={sourceId} onChange={e => { setSourceId(e.target.value); setPage(1) }}>
          <option value="">全部数据源</option>
          {sources.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <button className="btn sm" onClick={() => { setStatus(''); setTrigger(''); setSourceId(''); setPage(1) }}>重置</button>
      </div>

      {err && <ErrorBox err={err} onRetry={load} />}
      {!err && !data && <div className="card"><SkeletonRows n={8} /></div>}

      {data && data.items.length === 0 && (
        <Empty icon="🧾" title="暂无采集任务"
          hint="在数据源管理中点击「采集」，或等待调度器按频率自动执行。"
          action={<Link className="btn primary sm" to="/sources">前往数据源管理</Link>} />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead><tr>
                <th>ID</th><th style={{ minWidth: 150 }}>数据源</th><th>任务类型</th><th>触发方式</th>
                <th>开始时间</th><th>耗时</th><th className="num">获取</th><th className="num">新增</th>
                <th>失败原因</th><th>状态</th><th>操作</th>
              </tr></thead>
              <tbody>
                {data.items.map((t: any) => (
                  <tr key={t.id}>
                    <td><Link to={`/tasks/${t.id}`}>#{t.id}</Link></td>
                    <td>{t.source_id ? <Link to={`/sources/${t.source_id}`}>{t.source_name || `#${t.source_id}`}</Link> : '—'}</td>
                    <td className="tiny muted">{t.retry_of ? `失败重试（原 #${t.retry_of}）` : '采集任务'}</td>
                    <td className="tiny muted">{triggerLabel(t.trigger)}</td>
                    <td className="tiny muted">{fmt(t.started_at)}</td>
                    <td className="tiny num">{t.duration_ms} ms</td>
                    <td className="num">{t.found_count}</td>
                    <td className="num">{t.new_count}</td>
                    <td className="tiny" style={{ maxWidth: 200, color: t.error ? 'var(--err)' : undefined }}>
                      {t.error ? <span title={t.error}>{t.error.slice(0, 60)}{t.error.length > 60 ? '…' : ''}</span> : '—'}
                    </td>
                    <td><span className={`badge ${taskTone(t.status)}`}>{taskLabel(t.status)}</span></td>
                    <td className="ops">
                      {t.status === 'failed' && (
                        <button className="btn sm" disabled={busy} onClick={() => retry(t.id)}>重试</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pages={pages} total={data.total} onPage={setPage} />
        </>
      )}
      {toast.node}
    </div>
  )
}
