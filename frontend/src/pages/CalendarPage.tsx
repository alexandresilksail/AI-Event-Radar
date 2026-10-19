import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

export default function CalendarPage() {
  const [view, setView] = useState<'month' | 'week'>('month')
  const [anchor, setAnchor] = useState(() => new Date().toISOString().slice(0, 10))
  const [items, setItems] = useState<any[]>([])
  const [range, setRange] = useState<[string, string]>(['', ''])

  const load = useCallback(() => {
    api(`/calendar?view=${view}&date=${anchor}`).then(r => { setItems(r.items); setRange(r.range) })
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

  return (
    <div>
      <h1 className="page-title">日历视图</h1>
      <div className="filters">
        <select value={view} onChange={e => setView(e.target.value as any)}>
          <option value="month">月视图</option><option value="week">周视图</option>
        </select>
        <button onClick={() => shift(-1)}>← 上一{view === 'month' ? '月' : '周'}</button>
        <input type="date" value={anchor} onChange={e => setAnchor(e.target.value)} />
        <button onClick={() => shift(1)}>下一{view === 'month' ? '月' : '周'} →</button>
        <span className="muted" style={{ padding: 6 }}>范围：{range[0]} ~ {range[1]}</span>
      </div>
      <div className="calendar-grid">
        {['一', '二', '三', '四', '五', '六', '日'].map(w => (
          <div key={w} className="muted" style={{ textAlign: 'center', fontWeight: 700 }}>周{w}</div>
        ))}
        {cells.map(c => (
          <div className="cal-cell" key={c.date} style={{ opacity: c.date === new Date().toISOString().slice(0, 10) ? 1 : 0.92, border: c.date === anchor ? '2px solid var(--primary)' : undefined }}>
            <div className="day">{Number(c.date.slice(8))}</div>
            {c.events.map(e => (
              <Link key={e.id} to={`/events/${e.id}`} className="cal-ev" title={e.name}>{e.name}</Link>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
