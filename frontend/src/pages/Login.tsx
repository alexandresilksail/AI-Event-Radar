import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export default function Login() {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      const r = await api('/auth/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      })
      setToken(r.token)
      nav('/')
    } catch (ex: any) {
      setErr(ex.message || '登录失败')
    } finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={submit}>
        <div className="brand">
          <div className="brand-logo">📡</div>
          <div>
            <div className="brand-name">AI Event Radar</div>
            <div className="brand-sub">Intelligence Console</div>
          </div>
        </div>
        <h2>登录控制台</h2>
        <p className="sub">全球 AI 活动实时监控与情报平台</p>
        {err && <div className="error-box" style={{ marginBottom: 14 }}><span>⚠️</span><span>{err}</span></div>}
        <div className="form-row"><label>用户名</label>
          <input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></div>
        <div className="form-row"><label>密码</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            autoComplete="current-password" placeholder="请输入管理员密码" /></div>
        <button className="btn primary lg" style={{ width: '100%', marginTop: 6 }}
          disabled={busy || !password}>{busy ? '登录中…' : '登 录'}</button>
      </form>
    </div>
  )
}
