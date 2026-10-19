import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export default function Login() {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    try {
      const r = await api<{ token: string }>('/auth/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      })
      setToken(r.token)
      nav('/')
    } catch (e: any) {
      setErr(e.message.includes('401') ? '用户名或密码错误' : e.message)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={submit}>
        <h2 style={{ marginBottom: 4 }}>📡 AI Event Radar</h2>
        <p className="muted" style={{ marginBottom: 20 }}>AI 活动情报监控平台 · 管理登录</p>
        <div className="form-row">
          <label>用户名</label>
          <input value={username} onChange={e => setUsername(e.target.value)} autoFocus />
        </div>
        <div className="form-row">
          <label>密码</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} />
        </div>
        {err && <p style={{ color: 'var(--red)', fontSize: 13, marginBottom: 10 }}>{err}</p>}
        <button className="primary" style={{ width: '100%' }} type="submit">登 录</button>
        <p className="muted" style={{ marginTop: 12 }}>默认账户 admin，密码见 .env ADMIN_PASSWORD</p>
      </form>
    </div>
  )
}
