import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Events from './pages/Events'
import EventDetail from './pages/EventDetail'
import CalendarPage from './pages/CalendarPage'
import Sources from './pages/Sources'
import SourceDetail from './pages/SourceDetail'
import Tasks from './pages/Tasks'
import TaskDetail from './pages/TaskDetail'
import Monitor from './pages/Monitor'
import Notifications from './pages/Notifications'
import SettingsPage from './pages/SettingsPage'
import './styles.css'

function RequireAuth({ children }: { children: React.ReactElement }) {
  if (!localStorage.getItem('radar_token')) return <Navigate to="/login" replace />
  return children
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <HashRouter>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="events" element={<Events />} />
        <Route path="events/:id" element={<EventDetail />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="sources" element={<Sources />} />
        <Route path="sources/:id" element={<SourceDetail />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="tasks/:id" element={<TaskDetail />} />
        <Route path="monitor" element={<Monitor />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  </HashRouter>
)
