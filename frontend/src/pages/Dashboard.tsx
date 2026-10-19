import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'

type Dash = {
  new_events_today: number; total_sources: number; healthy_sources: number
  failing_sources: number; pending_verify_events: number
  upcoming_7d: any[]; crawl_trend_24h: any[]
  last_success_crawl_at: string | null; system: any
}

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api<Dash>('/stats/dashboard').then(setD).catch(e => setErr(e.message))
  }, [])

  if (err) return <p style={{ color: 'var(--red)' }}>加载失败：{err}</p>
  if (!d) return <p className="muted">加载中…</p>

  return (
    <div>
      <h1 className="page-title">首页仪表盘</h1>
      <div className="grid-cards">
        <div className="card stat"><div className="num">{d.new_events_today}</div><div className="label">今日新增活动</div></div>
        <div className="card stat"><div className="num">{d.total_sources}</div><div className="label">监控数据源</div></div>
        <div className="card stat"><div className="num ok">{d.healthy_sources}</div><div className="label">正常运行数据源</div></div>
        <div className="card stat"><div className="num bad">{d.failing_sources}</div><div className="label">采集失败数据源</div></div>
        <div className="card stat"><div className="num warn">{d.pending_verify_events}</div><div className="label">待核实活动</div></div>
      </div>

      <div className="card section">
        <h3 style={{ marginBottom: 10 }}>系统运行状态（真实状态，非模拟）</h3>
        <p className="muted">
          调度器：{d.system.scheduler_running ? '✅ 运行中' : '⛔ 未运行'} ·
          已排定任务数：{d.system.job_count} ·
          正在执行采集的数据源：{d.system.running_tasks.length ? d.system.running_tasks.join(', ') : '无'} ·
          最近一次成功采集：{fmt(d.last_success_crawl_at)}
        </p>
      </div>

      <div className="section">
        <h3 style={{ marginBottom: 10 }}>未来 7 天即将开始的活动</h3>
        {d.upcoming_7d.length === 0 && <p className="muted">暂无未来 7 天内的已识别活动</p>}
        {d.upcoming_7d.map(e => (
          <div className="event-card" key={e.id}>
            <h3><Link to={`/events/${e.id}`}>{e.name}</Link></h3>
            <div className="meta">
              <span>🕒 {fmt(e.start_time)}</span>
              <span>📍 {e.country} {e.city}{e.online ? '（线上）' : ''}</span>
              <span>🏛 {e.organizer}</span>
              <span className={`badge ${e.confidence_level === 'A' ? 'a' : e.confidence_level === 'B' ? 'b' : e.confidence_level === 'C' ? 'c' : 'pending'}`}>可信度 {e.confidence_level}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="section">
        <h3 style={{ marginBottom: 10 }}>24 小时采集趋势</h3>
        <table>
          <thead><tr><th>日期</th><th>执行任务数</th><th>新发现活动数</th></tr></thead>
          <tbody>
            {d.crawl_trend_24h.length === 0 && <tr><td colSpan={3} className="muted">24 小时内暂无采集记录</td></tr>}
            {d.crawl_trend_24h.map(r => (
              <tr key={r.date}><td>{r.date}</td><td>{r.tasks}</td><td>{r.new_events}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
