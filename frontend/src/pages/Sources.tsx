import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import {
  Badge, Confirm, Empty, ErrorBox, Field, Modal, PageHead, Pager, SkeletonRows,
  healthLabel, healthTone, taskLabel, taskTone, triggerLabel, useToast,
} from '../components/ui'

const TYPES = [
  { v: 'rss', label: 'RSS / Atom' },
  { v: 'html', label: 'HTML 页面' },
  { v: 'sitemap', label: 'Sitemap' },
  { v: 'api', label: '官方公开 API' },
  { v: 'change_detection', label: '页面变更检测' },
]

const EMPTY_FORM = {
  name: '', type: 'rss', url: '', topics: '', timezone: '', note: '',
  frequency_minutes: 60, enabled: true, method: '{}', compliance_note: '',
}

export default function Sources() {
  const [rows, setRows] = useState<any>(null)
  const [err, setErr] = useState('')
  const [kw, setKw] = useState('')
  const [type, setType] = useState('')
  const [enabled, setEnabled] = useState('')
  const [health, setHealth] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [editing, setEditing] = useState<any | null>(null)   // null=关闭, 0=新增, id=编辑
  const [form, setForm] = useState<any>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [formErr, setFormErr] = useState('')
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [del, setDel] = useState<any | null>(null)
  const [dirty, setDirty] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    setErr('')
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (kw) p.set('keyword', kw)
    if (type) p.set('type', type)
    if (enabled) p.set('enabled', enabled)
    if (health) p.set('health', health)
    api(`/sources?${p}`).then(setRows).catch(e => setErr(e.message))
  }, [kw, type, enabled, health, page, pageSize])

  useEffect(() => { load() }, [load])

  const pages = Math.max(1, Math.ceil((rows?.total || 0) / pageSize))
  const mark = (id: number | string, v = true) => setBusy(b => ({ ...b, [id]: v }))

  /* ---------- 表单 ---------- */
  const openCreate = () => { setEditing(0); setForm(EMPTY_FORM); setFormErr(''); setDirty(false) }
  const openEdit = (s: any) => {
    setEditing(s.id)
    setForm({
      name: s.name, type: s.type, url: s.url, topics: s.topics || '',
      timezone: s.timezone || '', note: s.note || '', frequency_minutes: s.frequency_minutes,
      enabled: s.enabled, method: JSON.stringify(s.method || {}, null, 2),
      compliance_note: s.compliance_note || '',
    })
    setFormErr(''); setDirty(false)
  }
  const patch = (k: string, v: any) => { setForm((f: any) => ({ ...f, [k]: v })); setDirty(true) }

  const urlInvalid = (u: string) => !/^https?:\/\/[^\s]+\.[^\s]+/i.test(u.trim())

  const save = async () => {
    setFormErr('')
    if (!form.name.trim()) return setFormErr('请填写数据源名称')
    if (urlInvalid(form.url)) return setFormErr('URL 格式不正确（需以 http:// 或 https:// 开头）')
    if (form.frequency_minutes < 5) return setFormErr('采集频率不能低于 5 分钟')
    let method = {}
    try { method = form.method?.trim() ? JSON.parse(form.method) : {} } catch { return setFormErr('采集配置不是合法 JSON') }
    setSaving(true)
    try {
      const body = JSON.stringify({ ...form, method, topics: form.topics, note: form.note })
      if (editing === 0) await api('/sources', { method: 'POST', body })
      else await api(`/sources/${editing}`, { method: 'PUT', body })
      toast.show(editing === 0 ? '数据源已创建' : '数据源已更新')
      setEditing(null); setDirty(false); load()
    } catch (e: any) { setFormErr(e.message || '保存失败') }
    finally { setSaving(false) }
  }

  /* ---------- 操作 ---------- */
  const act = async (id: number, path: string, label: string, method = 'POST') => {
    mark(id)
    try {
      const r: any = await api(`/sources/${id}/${path}`, { method })
      if (path === 'test') toast.show(r.ok ? `测试通过：获取到 ${r.items} 条内容` : `测试失败：${r.error}`, r.ok ? 'ok' : 'err')
      else if (path === 'trigger') toast.show(r.status === 'success' || r.status === 'partial'
        ? `采集完成：获取 ${r.found} 条 · 新增活动 ${r.new_events}` : `采集失败：${r.error}`, r.status === 'failed' ? 'err' : 'ok')
      else toast.show(`${label}成功`)
      load()
    } catch (e: any) { toast.show(`${label}失败：${e.message}`, 'err') }
    finally { mark(id) }
  }

  const doDelete = async () => {
    mark(del.id)
    try {
      const permanent = !!del.permanent
      await api(`/sources/${del.id}?permanent=${permanent}`, { method: 'DELETE' })
      toast.show(permanent ? '已永久删除（采集日志一并移除）' : '已软删除（历史数据保留，可恢复）')
      load()
    } catch (e: any) { toast.show('删除失败：' + e.message, 'err') }
    finally { mark(del.id); setDel(null) }
  }

  const closeModal = () => {
    if (dirty && !window.confirm('表单尚未保存，确定放弃修改吗？')) return
    setEditing(null)
  }

  return (
    <div>
      <PageHead title="🛰 数据源管理中心"
        desc="管理全部采集来源：新增、编辑、启停、连通性测试、立即采集。删除默认软删除，历史活动与日志保留。"
        extra={<button className="btn primary" onClick={openCreate}>＋ 新增数据源</button>} />

      <div className="filters">
        <input className="grow" placeholder="🔍 搜索名称 / 网址 / 主题"
          value={kw} onChange={e => { setKw(e.target.value); setPage(1) }} />
        <select value={type} onChange={e => { setType(e.target.value); setPage(1) }}>
          <option value="">全部类型</option>
          {TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
        </select>
        <select value={enabled} onChange={e => { setEnabled(e.target.value); setPage(1) }}>
          <option value="">启用状态</option><option value="true">已启用</option><option value="false">已暂停</option>
        </select>
        <select value={health} onChange={e => { setHealth(e.target.value); setPage(1) }}>
          <option value="">健康状态</option><option value="healthy">正常</option>
          <option value="failing">连续失败</option><option value="paused">已暂停</option><option value="never">从未采集</option>
        </select>
        <button className="btn sm" onClick={() => { setKw(''); setType(''); setEnabled(''); setHealth(''); setPage(1) }}>重置</button>
      </div>

      {err && <ErrorBox err={err} onRetry={load} />}

      {!err && !rows && <div className="card"><SkeletonRows n={8} /></div>}

      {rows && rows.items.length === 0 && (
        <Empty icon="🛰" title="暂无数据源"
          hint="添加第一个数据源，系统将按设定频率自动采集。"
          action={<button className="btn primary sm" onClick={openCreate}>＋ 新增数据源</button>} />
      )}

      {rows && rows.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead><tr>
                <th style={{ minWidth: 170 }}>名称 / 网址</th><th>类型</th><th>主题</th>
                <th>频率</th><th>健康状态</th><th>最近采集</th><th className="num">失败</th>
                <th>最近发现</th><th style={{ minWidth: 210 }}>操作</th>
              </tr></thead>
              <tbody>
                {rows.items.map((s: any) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/sources/${s.id}`} style={{ fontWeight: 600 }}>{s.name}</Link>
                      <div className="url-cell">{s.url}</div>
                    </td>
                    <td className="tiny">{TYPES.find(t => t.v === s.type)?.label || s.type}</td>
                    <td className="tiny muted" style={{ maxWidth: 120 }}>{s.topics || '—'}</td>
                    <td className="tiny">{s.frequency_minutes} 分钟</td>
                    <td>
                      <span className={`badge ${healthTone(s.health)}`}>{healthLabel(s.health)}</span>
                    </td>
                    <td className="tiny">
                      {s.last_crawled_at ? fmt(s.last_crawled_at) : <span className="muted">从未</span>}
                      {s.last_crawl_status && <div className="muted">结果：{taskLabel(s.last_crawl_status)}</div>}
                    </td>
                    <td className="num">{s.consecutive_failures || '—'}</td>
                    <td className="tiny">
                      {s.last_item_count} 条内容
                      {s.total_events_found > 0 && <div className="muted">累计活动 {s.total_events_found}</div>}
                    </td>
                    <td className="ops">
                      <button className="btn sm" disabled={busy[s.id]}
                        onClick={() => act(s.id, 'test', '测试')}>{busy[s.id] ? '…' : '测试'}</button>
                      <button className="btn sm cyan" disabled={busy[s.id]}
                        onClick={() => act(s.id, 'trigger', '采集')}>{busy[s.id] ? '…' : '采集'}</button>
                      <button className="btn sm" onClick={() => openEdit(s)}>编辑</button>
                      <button className="btn sm" onClick={() => act(s.id, s.enabled ? 'pause' : 'resume', s.enabled ? '暂停' : '启用')}>
                        {s.enabled ? '暂停' : '启用'}</button>
                      <button className="btn sm danger" onClick={() => setDel(s)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pages={pages} total={rows.total} onPage={setPage} />
        </>
      )}

      {/* ---------- 新增 / 编辑弹窗 ---------- */}
      {editing !== null && (
        <Modal title={editing === 0 ? '＋ 新增数据源' : `✏️ 编辑数据源 #${editing}`}
          onClose={closeModal}
          footer={<>
            <button className="btn" onClick={closeModal}>取消</button>
            <button className="btn primary" disabled={saving} onClick={save}>
              {saving ? '保存中…' : '保存'}</button>
          </>}>
          {formErr && <div className="error-box" style={{ marginBottom: 14 }}><span>⚠️</span><span>{formErr}</span></div>}

          <Field label="数据源名称" required error={form.name === '' && formErr ? undefined : ''}>
            <input value={form.name} onChange={e => patch('name', e.target.value)}
              placeholder="例如：MIT AI News RSS" />
          </Field>

          <div className="form-grid">
            <Field label="数据源类型" required>
              <select value={form.type} onChange={e => patch('type', e.target.value)}>
                {TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
              </select>
            </Field>
            <Field label="采集频率（分钟，最小 5）" required>
              <input type="number" min={5} value={form.frequency_minutes}
                onChange={e => patch('frequency_minutes', Number(e.target.value))} />
            </Field>
          </div>

          <Field label="来源 URL" required
            error={form.url && urlInvalid(form.url) ? 'URL 需以 http:// 或 https:// 开头' : ''}>
            <input value={form.url} onChange={e => patch('url', e.target.value)}
              placeholder="https://example.com/feed" />
          </Field>

          <Field label="活动主题 / 关键词" hint="可选，逗号分隔">
            <input value={form.topics} onChange={e => patch('topics', e.target.value)}
              placeholder="例如：AI,大模型,峰会" />
          </Field>

          <div className="form-grid">
            <Field label="时区 / 地区" hint="可选">
              <input value={form.timezone} onChange={e => patch('timezone', e.target.value)}
                placeholder="例如：Asia/Shanghai" />
            </Field>
            <Field label="备注">
              <input value={form.note} onChange={e => patch('note', e.target.value)} />
            </Field>
          </div>

          {(form.type === 'html' || form.type === 'api') && (
            <Field label="采集配置（JSON）"
              hint={form.type === 'html'
                ? ' {"link_selector": "a", "title_selector": "h2"}'
                : ' {"path_to_array": "data", "url_field": "url", "title_field": "name"}'}>
              <textarea rows={4} className="mono" value={form.method}
                onChange={e => patch('method', e.target.value)} />
            </Field>
          )}

          <label className="tiny" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <input type="checkbox" style={{ width: 16, height: 16 }}
              checked={form.enabled} onChange={e => patch('enabled', e.target.checked)} />
            保存后立即启用（调度器将按频率自动采集）
          </label>
          <div className="inline-note">
            保存时后端会进行安全校验：仅允许公网 HTTP(S) 地址，拒绝内网、云元数据与非法协议目标。
            搜索引擎结果页、需登录或付费墙来源不在支持范围内。
          </div>
        </Modal>
      )}

      {/* ---------- 删除确认（软删除默认 / 永久删除需二次确认） ---------- */}
      {del && (
        <Modal title="🗑 删除数据源" onClose={() => setDel(null)}
          footer={<>
            <button className="btn" onClick={() => setDel(null)}>取消</button>
            <button className="btn danger" onClick={() => {
              if (!window.confirm('永久删除将移除该来源的全部采集日志且不可恢复。确定永久删除吗？')) return
              setDel({ ...del, permanent: true, confirmed: true })
            }}>永久删除…</button>
            <button className="btn primary" onClick={doDelete}>软删除（可恢复）</button>
          </>}>
          <p style={{ fontSize: 13 }}>确认删除数据源「<b>{del.name}</b>」？</p>
          <div className="inline-note" style={{ marginTop: 10 }}>
            软删除：暂停调度并标记删除，历史活动、采集日志完整保留，可联系管理员恢复。
          </div>
          <div className="inline-note warn">
            永久删除：同时移除该来源的全部采集日志，不可恢复（已关联的活动记录保留）。
            {del.total_events_found > 0 && <>该来源累计贡献过 <b>{del.total_events_found}</b> 个活动。</>}
          </div>
        </Modal>
      )}
      {toast.node}
    </div>
  )
}
