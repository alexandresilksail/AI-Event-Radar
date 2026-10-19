import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearToken } from '../api'

const NAV = [
  { to: '/', label: '📊 仪表盘' },
  { to: '/events', label: '🔍 活动情报中心' },
  { to: '/calendar', label: '📅 日历视图' },
  { to: '/sources', label: '📡 数据源监控' },
  { to: '/logs', label: '📜 采集任务日志' },
  { to: '/notifications', label: '🔔 通知规则' },
  { to: '/settings', label: '⚙️ 系统设置' },
]

export default function Layout() {
  const nav = useNavigate()
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo">📡 AI Event <span>Radar</span></div>
        <nav>
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ padding: '16px 20px', borderTop: '1px solid #1e293b', marginTop: 12 }}>
          <button className="danger" style={{ width: '100%', background: 'transparent', border: '1px solid #334155', color: '#94a3b8' }}
            onClick={() => { clearToken(); nav('/login') }}>退出登录</button>
        </div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  )
}
