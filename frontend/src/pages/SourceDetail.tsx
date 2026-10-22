import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt, safeHref } from '../api'
import {
  Bars, Card, Dot, Empty, ErrorBox, PageHead, SkeletonRows, Timeline,
  healthLabel, healthTone, taskLabel, taskTone, triggerLabel,
} from '../components/ui'

export default function SourceDetail() {
  const { id } = useParams()
  const [d, setD] = useState<any>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(() => {
    setErr('')
    api(`/sources/${id}`).then(setD).catch(e => setErr(e.message))
  }, [id])
  useEffect(() => { load() }, [load])

  const act = async (path: string, label: string) => {
    setBusy(path)
    try {
      const r: any = await api(`/sources/${id}/${path}`, { method: 'POST' })
      if (path === 'test') alert(r.ok ? `测试通过：获取到 ${r.items} 条内容` : `测试失败：${r.error}`)
      else alert('操作成功')
      load()
    } catch (e: any) { alert(`${label}失败：${e.message}`) }
    finally { setBusy('') }
  }

  if (err) return <><Link to="/sources" className="tiny">← 返回数据源列表</Link>
    <div style={{ marginTop: 12 }}><ErrorBox err={err} onRetry={load} /></div></>
  if (!d) return <><Link to="/sources" className="tiny">← 返回数据源列表</Link>
    <div className="card section"><SkeletonRows n={10} /></div></>

  const s = d.source
  const st = d.stats
  const trend = d.trend_7d.map((t: any) => ({ label: t.date.slice(5), value: t.tasks, failed: t.failed }))

  return (
    <div>
      <PageHead
        title={<>{s.name} <span className={`badge ${healthTone(s.health)}`}>{healthLabel(s.health)}</span>
          {s.enabled ? <span className="badge ok">已启用</span> : <span className="badge">已暂停</span>}</>}
        desc={<span className="url-cell" style={{ display: 'inline-block' }}>{s.url}</span>}
        extra={<>
          <Link className="btn sm" to="/sources">← 返回列表</Link>
          <button className="btn sm" disabled={!!busy} onClick={() => act('test', '测试')}>{busy === 'test' ? '…' : '测试'}</button>
          <button className="btn sm cyan" disabled={!!busy} onClick={() => act('trigger', '采集')}>{busy === 'trigger' ? '…' : '立即采集'}</button>
          <button className="btn sm" onClick={() => act(s.enabled ? 'pause' : 'resume', '启停')}>{s.enabled ? '暂停' : '启用'}</button>
        </>} />

      <div className="grid cols-4">
        <Card>
          <div className="k tiny muted">总采集次数</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{st.total_crawls}</div>
          <div className="tiny muted" style={{ marginTop: 4 }}>
            成功 {st.by_status.success} · 部分 {st.by_status.partial} · 失败 {st.by_status.failed}
          </div>
        </Card>
        <Card>
          <div className="k tiny muted">近 24 小时</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{st.success_24h} / {st.total_24h}</div>
          <div className="tiny muted" style={{ marginTop: 4 }}>成功 / 总任务</div>
        </Card>
        <Card>
          <div className="k tiny muted">平均耗时（24h）</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>
            {st.avg_duration_ms_24h == null ? '—' : `${st.avg_duration_ms_24h} ms`}
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>基于已完成任务</div>
        </Card>
        <Card>
          <div className="k tiny muted">最近成功</div>
          <div style={{ fontSize: 15, fontWeight: 700, marginTop: 6 }}>
            {s.last_success_at ? fmt(s.last_success_at) : '从未成功'}
          </div>
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {s.consecutive_failures > 0 ? <span style={{ color: 'var(--err)' }}>连续失败 {s.consecutive_failures} 次</span> : '无连续失败'}
          </div>
        </Card>
      </div>

      {s.last_error && (
        <div className="error-box" style={{ marginTop: 14 }}>
          <span>⚠️</span><span style={{ flex: 1 }}>最近错误：{s.last_error}</span>
          <span className="tiny muted">{s.last_failure_at ? fmt(s.last_failure_at) : ''}</span>
        </div>
      )}

      <div className="grid cols-2 section">
        <Card title="📈 近 7 天采集趋势" hint="红色为当日失败任务数">
          {trend.some((t: any) => t.value > 0)
            ? <Bars data={trend} />
            : <Empty icon="📉" title="暂无采集记录" hint="立即采集或等待调度执行。" />}
        </Card>
        <Card title="⚙️ 调度配置">
          <table className="kv">
            <tbody>
              <tr><th>采集频率</th><td>每 {s.frequency_minutes} 分钟</td></tr>
              <tr><th>当前状态</th><td>{s.status}{d.schedule.backoff_active && ' （失败退避中）'}</td></tr>
              <tr><th>实际间隔</th><td>
                {d.schedule.backoff_active
                  ? `已指数退避至 ${d.schedule.effective_interval_minutes} 分钟`
                  : `${d.schedule.effective_interval_minutes} 分钟`}
              </td></tr>
              <tr><th>调度任务</th><td>
                {d.schedule.next_jobs.length
                  ? d.schedule.next_jobs.map((j: any, i: number) =>
                    <div key={i} className="tiny">下次执行：{j.next_run ? fmt(j.next_run) : '—'}</div>)
                  : <span className="muted tiny">当前未注册（暂停或未启用时不注册）</span>}
              </td></tr>
              <tr><th>时区 / 地区</th><td>{s.timezone || '—'}</td></tr>
              <tr><th>主题</th><td>{s.topics || '—'}</td></tr>
              <tr><th>备注</th><td>{s.note || '—'}</td></tr>
            </tbody>
          </table>
        </Card>
      </div>

      <div className="grid cols-2 section">
        <Card title="🧾 最近采集日志">
          {d.recent_logs.length === 0 ? <Empty icon="🧾" title="暂无日志" /> : (
            <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
              <table className="data" style={{ minWidth: 0 }}>
                <thead><tr><th>时间</th><th>状态</th><th className="num">获取</th><th className="num">新增</th><th className="num">重复</th></tr></thead>
                <tbody>
                  {d.recent_logs.map((l: any) => (
                    <tr key={l.id}>
                      <td className="tiny muted">{fmt(l.started_at)}</td>
                      <td><span className={`badge ${taskTone(l.status)}`}>{taskLabel(l.status)}</span></td>
                      <td className="num">{l.found_count}</td>
                      <td className="num">{l.new_count}</td>
                      <td className="num">{l.duplicate_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="🗂 最近发现的原始内容">
          {d.recent_documents.length === 0 ? <Empty icon="🗂" title="暂无内容" /> : (
            <Timeline items={d.recent_documents.map((doc: any) => ({
              at: fmt(doc.fetched_at),
              children: safeHref(doc.url) ? (
                <a href={safeHref(doc.url)} target="_blank" rel="noopener noreferrer nofollow">{doc.title || doc.url}</a>
              ) : (
                <span>{doc.title || doc.url || '—'}</span>
              ),
            }))} />
          )}
        </Card>
      </div>

      <div className="grid cols-2 section">
        <Card title="🎯 关联活动" hint="由该来源内容抽取入库">
          {d.related_events.length === 0 ? <Empty icon="🎯" title="暂无关联活动"
            hint="采集到含活动信息的内容并成功抽取后出现。" /> : (
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

        <Card title="🔍 内容变更检测" hint="指纹持久化，重启后仍有效">
          {d.change_records.length === 0
            ? <Empty icon="🔍" title="暂无变更检测记录"
              hint="页面变更检测型数据源在内容指纹变化时生成记录。" />
            : d.change_records.map((c: any, i: number) => (
              <div key={i} className="form-row">
                <div className="mono" style={{ wordBreak: 'break-all' }}>{c.content_hash}</div>
                <div className="tiny muted">更新于 {fmt(c.updated_at)} · {c.note}</div>
              </div>
            ))}
        </Card>
      </div>

      <Card title="🕘 最近手动 / 定时任务" className="section">
        {d.recent_tasks.length === 0 ? <Empty icon="🧾" title="暂无任务记录" /> : (
          <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
            <table className="data" style={{ minWidth: 0 }}>
              <thead><tr><th>任务</th><th>触发</th><th>状态</th><th className="num">耗时</th><th className="num">新增</th></tr></thead>
              <tbody>
                {d.recent_tasks.map((t: any) => (
                  <tr key={t.id}>
                    <td><Link to={`/tasks/${t.id}`}>#{t.id}</Link>
                      {t.retry_of && <span className="tiny muted">（重试自 #{t.retry_of}）</span>}</td>
                    <td className="tiny muted">{triggerLabel(t.trigger)}</td>
                    <td><span className={`badge ${taskTone(t.status)}`}>{taskLabel(t.status)}</span></td>
                    <td className="num">{t.duration_ms} ms</td>
                    <td className="num">{t.new_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="inline-note" style={{ marginTop: 16 }}>
        <Dot tone="ok" />状态说明：可访问（HTTP 可达）→ 采集成功（获取到内容）→ 抽取到有效内容 → 发现新内容 →
        活动通过核实。以上状态彼此独立记录，任一环节失败都会单独呈现，不会合并成模糊的「成功」。
      </div>
    </div>
  )
}
