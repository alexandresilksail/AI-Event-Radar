import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../api'
import {
  Card, Empty, ErrorBox, PageHead, SkeletonRows, Timeline,
  taskLabel, taskTone, triggerLabel, useToast,
} from '../components/ui'

export default function TaskDetail() {
  const { id } = useParams()
  const [d, setD] = useState<any>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setErr('')
    api(`/crawl-tasks/${id}`).then(setD).catch(e => setErr(e.message))
  }, [id])
  useEffect(() => { load() }, [load])

  const retry = async () => {
    setBusy(true)
    try {
      const r: any = await api(`/crawl-tasks/${id}/retry`, { method: 'POST' })
      toast.show(`已创建重试任务 #${r.new_task_id}`)
      load()
    } catch (e: any) { toast.show('重试失败：' + e.message, 'err') }
    finally { setBusy(false) }
  }

  if (err) return <><Link to="/tasks" className="tiny">← 返回任务列表</Link>
    <div style={{ marginTop: 12 }}><ErrorBox err={err} onRetry={load} /></div></>
  if (!d) return <><Link to="/tasks" className="tiny">← 返回任务列表</Link>
    <div className="card section"><SkeletonRows n={8} /></div></>

  const t = d.task

  return (
    <div>
      <PageHead
        title={<>任务 #{t.id} <span className={`badge ${taskTone(t.status)}`}>{taskLabel(t.status)}</span>
          <span className="badge blue">{triggerLabel(t.trigger)}</span></>}
        extra={<>
          <Link className="btn sm" to="/tasks">← 返回列表</Link>
          {t.status === 'failed' && <button className="btn sm cyan" disabled={busy} onClick={retry}>重试此任务</button>}
        </>} />

      <div className="grid cols-4">
        <Card>
          <div className="tiny muted">数据源</div>
          <div style={{ fontWeight: 600, marginTop: 4 }}>
            {d.source ? <Link to={`/sources/${d.source.id}`}>{d.source.name}</Link> : '—'}
          </div>
          <div className="url-cell" style={{ marginTop: 4 }}>{d.source?.url}</div>
        </Card>
        <Card>
          <div className="tiny muted">耗时</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{t.duration_ms} ms</div>
          <div className="tiny muted" style={{ marginTop: 4 }}>尝试 {t.attempts} 次</div>
        </Card>
        <Card>
          <div className="tiny muted">获取 / 新增 / 重复</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>
            {t.found_count} / <span style={{ color: 'var(--ok)' }}>{t.new_count}</span> / <span className="muted">{t.duplicate_count}</span>
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>更新 {t.updated_count} 条</div>
        </Card>
        <Card>
          <div className="tiny muted">开始 / 结束</div>
          <div className="tiny" style={{ marginTop: 6 }}>{fmt(t.started_at)}</div>
          <div className="tiny muted">{t.finished_at ? fmt(t.finished_at) : '—'}</div>
        </Card>
      </div>

      {t.error && (
        <div className="error-box" style={{ marginTop: 14 }}>
          <span>⚠️</span><span style={{ flex: 1 }}>{t.error}</span>
        </div>
      )}

      <div className="grid cols-2 section">
        <Card title="🕘 执行时间线" hint="每次尝试一条记录">
          {d.logs.length === 0 ? <Empty icon="🕘" title="暂无执行记录" /> : (
            <Timeline items={d.logs.map((l: any) => ({
              at: `${fmt(l.started_at)} → ${l.finished_at ? fmt(l.finished_at) : '进行中'} · ${l.duration_ms ?? 0} ms`,
              tone: l.status === 'success' ? 'ok' as const : l.status === 'failed' ? 'err' as const : undefined,
              children: <>
                <span className={`badge ${taskTone(l.status)}`}>{taskLabel(l.status)}</span>
                <span className="tiny muted" style={{ marginLeft: 8 }}>
                  获取 {l.found_count} · 新增 {l.new_count} · 重复 {l.duplicate_count}
                </span>
                {l.retry_count > 0 && <span className="tiny" style={{ marginLeft: 8, color: 'var(--warn)' }}>第 {l.retry_count} 次重试</span>}
                {l.error && <div className="tiny" style={{ color: 'var(--err)' }}>{l.error}</div>}
              </>,
            }))} />
          )}
        </Card>

        <Card title="🎯 关联活动记录" hint="任务执行窗口内由该来源发现">
          {d.related_events.length === 0 ? <Empty icon="🎯" title="该任务未关联新活动"
            hint="全部为重复内容或未抽取到有效活动信息时为空。" /> : (
            <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
              <table className="data" style={{ minWidth: 0 }}>
                <thead><tr><th>活动</th><th>时间</th><th>可信度</th></tr></thead>
                <tbody>
                  {d.related_events.map((e: any) => (
                    <tr key={e.id}>
                      <td><Link to={`/events/${e.id}`}>{e.name}</Link></td>
                      <td className="tiny muted">{e.start_time ? fmt(e.start_time) : '待核实'}</td>
                      <td><span className="badge blue">{e.confidence_level}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {d.retries.length > 0 && (
        <Card title="🔁 重试记录" className="section">
          <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>任务</th><th>状态</th><th>耗时</th><th>新增</th><th>开始时间</th></tr></thead>
              <tbody>
                {d.retries.map((r: any) => (
                  <tr key={r.id}>
                    <td><Link to={`/tasks/${r.id}`}>#{r.id}</Link></td>
                    <td><span className={`badge ${taskTone(r.status)}`}>{taskLabel(r.status)}</span></td>
                    <td className="num">{r.duration_ms} ms</td>
                    <td className="num">{r.new_count}</td>
                    <td className="tiny muted">{fmt(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {toast.node}
    </div>
  )
}
