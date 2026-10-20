import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Empty, ErrorBox, PageHead } from '../components/ui'

export default function CalendarPage() {
  const [view, setView] = useState<'month' | 'week'>('month')
  const [anchor, setAnchor] = useState(() => new Date().toISOString().slice(0, 10))
  const [items, setItems] = useState<any[]>([])
  const [range, setRange] = useState<[string, string]>(['', ''])
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setErr('')
    api(`/calendar?view=${view}&date=${anchor}`)
      .then(r => { setItems(r.items); setRange(r.range) })
      .catch(e => setErr(e.message))
  }, [view, anchor])
  useEffect(() => { load() }, [load])

  // build grid
  const start = new Date(range[0] || anchor)
  const gridStart = new Date(start)
  if (view === 'month') gridStart.setDate(1 - ((start.getDay() + 6) % 7))
  const cells: { date: string; events: any[] }[] = []
  const days = view === 'month' ? 42 : 7
  for (let i = 0; i < days; i++) {
    const d = new Date(gridStart); d.setDate(gridStart.getDate() + i)
    const ds = d.toISOString().slice(0, 10)
    cells.push({ date: ds, events: items.filter(e => (e.start_time || '').slice(0, 10) === ds) })
  }

  const shift = (n: number) => {
    const d = new Date(anchor); d.setDate(d.getDate() + n * (view === 'month' ? 28 : 7))
    setAnchor(d.toISOString().slice(0, 10))
  }
  const today = new Date().toISOString().slice(0, 10)
  const monthOf = (range[1] || anchor).slice(0, 7)

  return (
    <div>
      <PageHead title="🗓 日历视图" desc={`范围：${range[0] || '—'} ~ ${range[1] || '—'} · 仅显示已采集到的活动`} />
      <div className="filters">
        <select value={view} onChange={e => setView(e.target.value as any)}>
          <option value="month">月视图</option><option value="week">周视图</option>
        </select>
        <button className="btn sm" onClick={() => shift(-1)}>← 上一{view === 'month' ? '月' : '周'}</button>
        <input type="date" value={anchor} onChange={e => setAnchor(e.target.value)} />
        <button className="btn sm" onClick={() => shift(1)}>下一{view === 'month' ? '月' : '周'} →</button>
        <button className="btn sm ghost" onClick={() => setAnchor(today)}>回到今天</button>
      </div>

      {err && <ErrorBox err={err} onRetry={load} />}

      <div className="cal-head">
        {['一', '二', '三', '四', '五', '六', '日'].map(w => <div key={w}>周{w}</div>)}
      </div>
      <div className="cal-grid">
        {cells.map(c => {
          const out = view === 'month' && !c.date.startsWith(monthOf)
          return (
            <div key={c.date}
              className={`cal-cell ${c.date === today ? 'today' : ''} ${out ? 'out' : ''}`}>
              <div className="day">{Number(c.date.slice(8))}</div>
              {c.events.map(e => (
                <Link key={e.id} to={`/events/${e.id}`} className={`cal-ev ${e.is_free ? 'free' : ''}`}
                  title={e.name}>{e.name}</Link>
              ))}
            </div>
          )
        })}
      </div>

      {items.length === 0 && (
        <div style={{ marginTop: 16 }}>
          <Empty icon="🗓" title="该时间范围内暂无活动"
            hint="采集到含日期的活动后会自动出现在日历中。" />
        </div>
      )}
    </div>
  )
}
