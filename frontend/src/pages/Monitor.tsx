import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import {
  Bars, Card, Dot, Empty, ErrorBox, PageHead, SkeletonRows, Stat,
  healthLabel, healthTone,
} from '../components/ui'

export default function Monitor() {
  const [m, setM] = useState<any>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setErr('')
    api('/monitor').then(setM).catch(e => setErr(e.message))
  }, [])
  useEffect(() => { load() }, [])

  const compTone = (s: string) =>
    ({ healthy: 'ok', degraded: 'warn', stale: 'warn', down: 'err', not_started: 'err' } as Record<string, any>)[s] || 'idle'

  return (
    <div>
      <PageHead title="💓 系统监控"
        desc="系统健康检查与业务任务成功率分开呈现；数据不可用时明确标注，不使用固定值伪装。"
        extra={<button className="btn sm" onClick={load}>刷新</button>} />

      {err && <ErrorBox err={err} onRetry={load} />}
      {!err && !m && <div className="card"><SkeletonRows n={8} /></div>}

      {m && <>
        <div className="grid cols-4">
          <Card>
            <div className="tiny muted">API 服务</div>
            <div style={{ marginTop: 6, fontWeight: 600 }}>
              <Dot tone={compTone(m.components.api.status)} />正常响应
            </div>
            <div className="tiny muted" style={{ marginTop: 4 }}>角色：{m.components.api.role}</div>
          </Card>
          <Card>
            <div className="tiny muted">采集 Worker</div>
            <div style={{ marginTop: 6, fontWeight: 600 }}>
              <Dot tone={compTone(m.components.worker.status)} />
              {m.components.worker.status === 'healthy' ? '健康（持有调度锁）'
                : m.components.worker.status === 'not_started' ? '未启动' : '锁已过期'}
            </div>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {m.components.worker.heartbeat_age_seconds != null
                ? `心跳 ${m.components.worker.heartbeat_age_seconds} 秒前 · ${m.components.worker.holder || ''}`
                : '尚无心跳记录'}
            </div>
          </Card>
          <Card>
            <div className="tiny muted">数据库</div>
            <div style={{ marginTop: 6, fontWeight: 600 }}>
              <Dot tone={m.components.database.status === 'healthy' ? 'ok' : 'err'} />
              {m.components.database.status === 'healthy' ? '连接正常' : '连接异常'}
            </div>
            <div className="tiny muted" style={{ marginTop: 4 }}>{m.components.database.error || 'PostgreSQL / SQLite'}</div>
          </Card>
          <Card>
            <div className="tiny muted">本次连续运行</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 6 }}>
              {Math.floor(m.uptime_seconds / 3600)} 小时 {Math.floor((m.uptime_seconds % 3600) / 60)} 分
            </div>
            <div className="tiny muted" style={{ marginTop: 4 }}>进程启动时间（重启后归零）</div>
          </Card>
        </div>

        {m.warnings.length > 0 && (
          <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {m.warnings.map((w: any, i: number) => (
              <div key={i} className={`inline-note ${w.level === 'critical' ? 'warn' : ''}`}>
                {w.level === 'critical' ? '🔴' : '🟡'} {w.msg}
              </div>
            ))}
          </div>
        )}

        <div className="grid cols-4 section">
          <Stat label="24 小时采集任务" value={m.crawl_24h.total} accent="blue"
            desc={`成功 ${m.crawl_24h.by_status.success} · 部分 ${m.crawl_24h.by_status.partial} · 失败 ${m.crawl_24h.by_status.failed}`} />
          <Stat label="24 小时采集成功率"
            value={m.crawl_24h.success_rate == null ? '—' : `${(m.crawl_24h.success_rate * 100).toFixed(0)}%`}
            accent={m.crawl_24h.success_rate == null ? 'blue' : m.crawl_24h.success_rate >= 0.8 ? 'ok' : 'warn'}
            desc="按已完成任务统计" />
          <Stat label="平均任务耗时"
            value={m.crawl_24h.avg_duration_ms == null ? '—' : m.crawl_24h.avg_duration_ms} unit={m.crawl_24h.avg_duration_ms == null ? '' : ' ms'}
            accent="purple" desc="24 小时内已完成任务均值" />
          <Stat label="当前运行中任务" value={m.crawl_24h.running_now} accent="cyan"
            desc={m.scheduler.running ? `调度器运行中（${m.scheduler.job_count} 个任务）` : '调度器未运行'} />
        </div>

        <div className="grid cols-2 section">
          <Card title="🛰 数据源健康分布" hint={`共 ${m.sources.total} 个启用中的数据源`}>
            <table className="kv">
              <tbody>
                {(['healthy', 'failing', 'paused', 'never'] as const).map(h => (
                  <tr key={h}><th>{healthLabel(h)}</th>
                    <td><span className={`badge ${healthTone(h)}`}>{m.sources.health[h]}</span></td></tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 10 }}><Link className="btn sm" to="/sources">管理数据源 →</Link></div>
          </Card>

          <Card title="💽 磁盘使用" hint="API 容器所在文件系统">
            {!m.disk ? <Empty icon="💽" title="无法获取磁盘信息" hint="当前环境不支持磁盘查询。" /> : (
              <>
                <Bars data={[
                  { label: `已用 ${m.disk.used_gb}G`, value: m.disk.used_percent },
                ]} max={100} />
                <table className="kv" style={{ marginTop: 10 }}>
                  <tbody>
                    <tr><th>总容量</th><td>{m.disk.total_gb} GB</td></tr>
                    <tr><th>已用</th><td>{m.disk.used_gb} GB（{m.disk.used_percent}%）</td></tr>
                    <tr><th>可用</th><td>{m.disk.free_gb} GB</td></tr>
                  </tbody>
                </table>
              </>
            )}
          </Card>
        </div>

        <div className="grid cols-2 section">
          <Card title="⚠️ 最近错误" hint="仅展示错误类型与摘要，不含敏感信息">
            {m.recent_errors.length === 0 ? <Empty icon="✅" title="暂无失败记录" /> : (
              <div className="table-wrap" style={{ boxShadow: 'none', border: 'none' }}>
                <table className="data" style={{ minWidth: 0 }}>
                  <thead><tr><th>时间</th><th>数据源</th><th>错误摘要</th></tr></thead>
                  <tbody>
                    {m.recent_errors.map((e: any) => (
                      <tr key={e.log_id}>
                        <td className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{fmt(e.at)}</td>
                        <td className="tiny">{e.source_name}</td>
                        <td className="tiny" style={{ color: 'var(--err)' }}>{e.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="🧭 运行状态摘要">
            <table className="kv">
              <tbody>
                <tr><th>最近成功采集</th><td>{m.last_success_crawl_at ? fmt(m.last_success_crawl_at) : '暂无'}</td></tr>
                <tr><th>距今</th><td>{m.hours_since_last_success != null ? `${m.hours_since_last_success} 小时` : '—'}</td></tr>
                <tr><th>调度任务数</th><td>{m.scheduler.job_count}</td></tr>
                <tr><th>采集执行方式</th><td className="tiny">{m.notes.queue}</td></tr>
                <tr><th>AI 结构化抽取</th><td className="tiny">
                  <Dot tone={m.notes.ai_extraction_configured ? 'ok' : 'idle'} />
                  {m.notes.ai_extraction_configured ? '已配置' : '未配置 —— 当前使用规则抽取'}</td></tr>
                <tr><th>搜索引擎发现</th><td className="tiny">
                  <Dot tone={m.notes.search_configured ? 'ok' : 'idle'} />
                  {m.notes.search_configured ? '已配置' : '未配置 —— 不会进行全网搜索'}</td></tr>
                <tr><th>环境 / 角色</th><td>{m.env} / {m.role}</td></tr>
              </tbody>
            </table>
          </Card>
        </div>
      </>}
    </div>
  )
}
