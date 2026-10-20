import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import { Bars, Card, Dot, Empty, ErrorBox, PageHead, SkeletonCards, SkeletonRows, Stat, useToast } from '../components/ui'

type Dash = {
  new_events_today: number; total_sources: number; healthy_sources: number
  failing_sources: number; pending_verify_events: number
  upcoming_7d: any[]; crawl_trend_24h: any[]; crawl_trend_7d: any[]
  today_crawls: { total: number; succeeded: number; success_rate: number | null; avg_duration_ms: number | null }
  source_health: Record<string, number>
  last_success_crawl_at: string | null; system: any
}

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null)
  const [err, setErr] = useState('')
  const [tasks, setTasks] = useState<any>(null)
  const [mon, setMon] = useState<any>(null)
  const toast = useToast()

  const load = useCallback(() => {
    setErr('')
    api<Dash>('/stats/dashboard').then(setD).catch(e => setErr(e.message))
  }, [])

  useEffect(() => {
    load()
    api('/crawl-tasks?page_size=8').then(setTasks).catch(() => setTasks(null))
    api('/monitor').then(setMon).catch(() => setMon(null))
  }, [load])

  const retry = () => {
    api<Dash>('/stats/dashboard').then(setD).catch(e => { setErr(e.message); toast.show('刷新失败', 'err') })
  }

  const trend7 = (d?.crawl_trend_7d || []).map((r: any) => ({ label: r.date.slice(5), value: r.tasks, failed: r.failed }))
  const runningNow = d?.system?.running_tasks?.length ?? 0

  return (
    <div>
      <PageHead title="📊 首页概览"
        desc="所有数字均来自后端真实统计；无数据时显示空状态，不会用演示数据填充。" />
      {err && <ErrorBox err={err} onRetry={retry} />}

      {!d && !err && <SkeletonCards />}

      {d && <>
        <div className="grid cols-4">
          <Stat icon="🎯" label="今日新增活动" value={d.new_events_today} accent="cyan"
            desc={`待核实 ${d.pending_verify_events} 条`} />
          <Stat icon="🛰" label="监控数据源" value={d.total_sources} accent="blue"
            desc={`正常 ${d.source_health.healthy ?? d.healthy_sources} · 失败 ${d.source_health.failing ?? 0} · 暂停 ${d.source_health.paused ?? 0}`} />
          <Stat icon="⚙️" label="今日采集任务" value={d.today_crawls.total} accent="purple"
            desc={`成功 ${d.today_crawls.succeeded}${runningNow ? ` · 运行中 ${runningNow}` : ''}`} />
          <Stat icon="✅" label="今日采集成功率"
            value={d.today_crawls.success_rate == null ? '—' : `${(d.today_crawls.success_rate * 100).toFixed(0)}%`}
            accent={d.today_crawls.success_rate == null ? 'blue' : d.today_crawls.success_rate >= 0.8 ? 'ok' : 'warn'}
            desc={d.today_crawls.avg_duration_ms ? `平均耗时 ${d.today_crawls.avg_duration_ms} ms` : '今日暂无已完成任务'} />
        </div>

        <div className="grid cols-2 section">
          <Card title="📈 最近 7 天采集趋势" hint="悬停查看失败数">
            {trend7.some(t => t.value > 0)
              ? <Bars data={trend7} />
              : <Empty icon="📉" title="近 7 天暂无采集记录" hint="添加数据源并触发采集后，这里会显示真实趋势。" />}
          </Card>
          <Card title="🗓 未来 7 天活动" hint="来自已采集的活动情报">
            {d.upcoming_7d.length === 0
              ? <Empty icon="🗓" title="暂无即将开始的活动" hint="采集到含未来日期的活动后自动出现在此。" />
              : <table className="data">
                <thead><tr><th>活动</th><th>时间</th><th>地点</th><th>可信度</th></tr></thead>
                <tbody>
                  {d.upcoming_7d.map((e: any) => (
                    <tr key={e.id}>
                      <td><Link to={`/events/${e.id}`}>{e.name}</Link></td>
                      <td className="tiny">{e.start_time ? fmt(e.start_time) : '待核实'}</td>
                      <td className="tiny muted">{e.country} {e.city}</td>
                      <td><span className={`badge ${e.confidence_level === 'A' ? 'a' : e.confidence_level === 'B' ? 'b' : e.confidence_level === 'C' ? 'c' : 'pending'}`}>{e.confidence_level}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>}
          </Card>
        </div>

        <div className="grid cols-2 section">
          <Card title="🧾 最近采集任务">
            {!tasks ? <SkeletonRows n={4} /> : tasks.items.length === 0
              ? <Empty icon="🧾" title="暂无采集任务" hint="在「数据源管理」中点击立即采集即可创建任务。"
                action={<Link className="btn primary sm" to="/sources">前往数据源管理</Link>} />
              : <table className="data">
                <thead><tr><th>ID</th><th>数据源</th><th>状态</th><th>触发</th><th>新增</th><th>开始时间</th></tr></thead>
                <tbody>
                  {tasks.items.map((t: any) => (
                    <tr key={t.id}>
                      <td><Link to={`/tasks/${t.id}`}>#{t.id}</Link></td>
                      <td>{t.source_name || `#${t.source_id}`}</td>
                      <td><span className={`badge ${t.status === 'success' ? 'ok' : t.status === 'partial' ? 'warn' : t.status === 'failed' ? 'err' : 'info'}`}>{t.status_label}</span></td>
                      <td className="tiny muted">{t.trigger_label}</td>
                      <td className="num">{t.new_count}</td>
                      <td className="tiny muted">{fmt(t.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>}
          </Card>

          <Card title="💓 系统运行状态">
            {!mon ? <SkeletonRows n={4} /> : <>
              <table className="kv">
                <tbody>
                  <tr><th>API 服务</th><td><Dot tone={mon.components.api.status === 'healthy' ? 'ok' : 'err'} />正常响应（本进程）</td></tr>
                  <tr><th>数据库</th><td><Dot tone={mon.components.database.status === 'healthy' ? 'ok' : 'err'} />{mon.components.database.status === 'healthy' ? '连接正常' : '连接异常'}</td></tr>
                  <tr><th>采集 Worker</th><td>
                    <Dot tone={mon.components.worker.status === 'healthy' ? 'ok' : mon.components.worker.status === 'not_started' ? 'err' : 'warn'} />
                    {mon.components.worker.status === 'healthy'
                      ? `健康（持有调度锁，心跳 ${mon.components.worker.heartbeat_age_seconds ?? 0}s 前）`
                      : mon.components.worker.status === 'not_started' ? '未启动（请检查 worker 容器）' : `锁已过期（${mon.components.worker.holder || '未知'}）`}
                  </td></tr>
                  <tr><th>调度器任务</th><td>{mon.scheduler.running ? `${mon.scheduler.job_count} 个定时任务` : '未运行'}</td></tr>
                  <tr><th>最近成功采集</th><td>{d.last_success_crawl_at ? fmt(d.last_success_crawl_at) : '暂无'}</td></tr>
                  <tr><th>本次连续运行</th><td>{Math.floor(mon.uptime_seconds / 60)} 分钟</td></tr>
                </tbody>
              </table>
              {mon.warnings.length > 0 && (
                <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {mon.warnings.map((w: any, i: number) => (
                    <div key={i} className={`inline-note ${w.level === 'critical' ? 'warn' : ''}`}>
                      {w.level === 'critical' ? '🔴' : '🟡'} {w.msg}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ marginTop: 12 }}><Link className="btn sm" to="/monitor">查看完整系统监控 →</Link></div>
            </>}
          </Card>
        </div>
      </>}
    </div>
  )
}
