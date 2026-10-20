import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, clearToken } from '../api'

interface NavEntry { to: string; label: string; icon: string; end?: boolean }

const NAV: { group: string; items: NavEntry[] }[] = [
  {
    group: '情报工作台',
    items: [
      { to: '/', label: '首页概览', icon: '📊', end: true },
      { to: '/events', label: '活动情报中心', icon: '🎯' },
      { to: '/calendar', label: '日历视图', icon: '🗓' },
    ],
  },
  {
    group: '采集运维',
    items: [
      { to: '/sources', label: '数据源管理', icon: '🛰' },
      { to: '/tasks', label: '采集任务日志', icon: '🧾' },
      { to: '/monitor', label: '系统监控', icon: '💓' },
    ],
  },
  {
    group: '推送与配置',
    items: [
      { to: '/notifications', label: '通知规则', icon: '🔔' },
      { to: '/settings', label: '系统设置', icon: '⚙️' },
    ],
  },
]

const TITLES: Record<string, string> = Object.fromEntries(
  NAV.flatMap(g => g.items.map(i => [i.to, i.label])),
)

export default function Layout() {
  const [collapsed, setCollapsed] = useState(() => window.innerWidth <= 960)
  const [env, setEnv] = useState('')
  const loc = useLocation()

  useEffect(() => {
    api('/status').then(s => setEnv(`${s.env} · ${s.role}`)).catch(() => setEnv(''))
  }, [])

  // 路由切换后移动端自动收起
  useEffect(() => { if (window.innerWidth <= 960) setCollapsed(true) }, [loc.pathname])

  const title = TITLES[loc.pathname]
    || (loc.pathname.startsWith('/events/') ? '活动详情'
      : loc.pathname.startsWith('/sources/') ? '数据源详情'
        : loc.pathname.startsWith('/tasks/') ? '任务详情' : '')

  const logout = () => { clearToken(); window.location.hash = '#/login' }

  return (
    <div className="app">
      <div className={`scrim ${collapsed ? '' : 'show'}`} onClick={() => setCollapsed(true)} />
      <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
        <div className="brand">
          <div className="brand-logo">📡</div>
          <div>
            <div className="brand-name">AI Event Radar</div>
            <div className="brand-sub">Intelligence Console</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map(g => (
            <div key={g.group}>
              <div className="nav-group">{g.group}</div>
              {g.items.map(i => (
                <NavLink key={i.to} to={i.to} end={i.end}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                  <span className="ico">{i.icon}</span>
                  <span>{i.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          7×24 自动采集 · 数据全部来自真实来源<br />未知字段一律标注「待核实」，不编造
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="collapse-btn" onClick={() => setCollapsed(c => !c)}
            aria-label="切换导航">{collapsed ? '☰' : '✕'}</button>
          <div className="crumb">AI Event Radar / <b>{title}</b></div>
          <div className="topbar-right">
            {env && <span className="env-chip">{env}</span>}
            <span className="user-chip">
              <span className="avatar">A</span>admin
            </span>
            <button className="btn sm ghost" onClick={logout}>退出</button>
          </div>
        </header>
        <main className="content"><Outlet /></main>
      </div>
    </div>
  )
}
