/** 共享 UI 组件 —— 全部使用 design tokens（styles.css），不在组件内写死颜色 */
import { ReactNode, useEffect, useState } from 'react'

/* ---------- 统计卡 ---------- */
export function Stat(props: {
  label: string; value: ReactNode; unit?: string; desc?: ReactNode;
  accent?: 'blue' | 'cyan' | 'purple' | 'ok' | 'warn' | 'err'; icon?: string
}) {
  return (
    <div className={`stat accent-${props.accent || 'blue'}`}>
      <div className="k">{props.icon && <span>{props.icon}</span>}{props.label}</div>
      <div className="v">{props.value}{props.unit && <span className="u">{props.unit}</span>}</div>
      {props.desc && <div className="d">{props.desc}</div>}
    </div>
  )
}

/* ---------- 卡片 ---------- */
export function Card(props: { title?: ReactNode; hint?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`card ${props.className || ''}`}>
      {props.title && <div className="card-title">{props.title}{props.hint && <span className="hint">{props.hint}</span>}</div>}
      {props.children}
    </div>
  )
}

/* ---------- 状态徽章 ---------- */
export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'blue' | 'purple' | ''
export function Badge({ tone = '', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export const healthTone = (h: string): Tone =>
  ({ healthy: 'ok', failing: 'err', paused: '', never: 'warn' } as Record<string, Tone>)[h] ?? ''

export const healthLabel = (h: string) =>
  ({ healthy: '正常', failing: '连续失败', paused: '已暂停', never: '从未采集' } as Record<string, string>)[h] ?? h

export const taskTone = (s: string): Tone =>
  ({ success: 'ok', partial: 'warn', failed: 'err', running: 'info', pending: '' } as Record<string, Tone>)[s] ?? ''

export const taskLabel = (s: string) =>
  ({ success: '成功', partial: '部分成功', failed: '失败', running: '执行中', pending: '等待执行' } as Record<string, string>)[s] ?? s

export const triggerLabel = (t: string) =>
  ({ manual: '手动触发', scheduled: '定时调度', retry: '失败重试', seed: '种子初始化' } as Record<string, string>)[t] ?? t

/* ---------- 健康点 ---------- */
export function Dot({ tone }: { tone: 'ok' | 'warn' | 'err' | 'idle' }) {
  return <span className={`dot ${tone}`} />
}

/* ---------- 加载骨架屏 ---------- */
export function SkeletonRows({ n = 5 }: { n?: number }) {
  return <div style={{ padding: 8 }}>{Array.from({ length: n }).map((_, i) => (
    <div key={i} className="skeleton sk-row" style={{ width: `${92 - i * 4}%` }} />
  ))}</div>
}

export function SkeletonCards({ n = 4 }: { n?: number }) {
  return <div className="grid cols-4">{Array.from({ length: n }).map((_, i) => (
    <div key={i} className="skeleton sk-card" />
  ))}</div>
}

/* ---------- 空状态 / 错误状态 ---------- */
export function Empty({ icon = '🛰', title, hint, action }: { icon?: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="ico">{icon}</div>
      <div className="t">{title}</div>
      {hint && <div className="tiny muted" style={{ marginTop: 6 }}>{hint}</div>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  )
}

export function ErrorBox({ err, onRetry }: { err: string; onRetry?: () => void }) {
  return (
    <div className="error-box">
      <span>⚠️</span>
      <span style={{ flex: 1, minWidth: 200 }}>加载失败：{err}</span>
      {onRetry && <button className="btn sm" onClick={onRetry}>重试</button>}
    </div>
  )
}

/* ---------- Toast ---------- */
export function useToast() {
  const [msg, setMsg] = useState<{ text: string; kind: 'ok' | 'err' } | null>(null)
  useEffect(() => {
    if (!msg) return
    const t = setTimeout(() => setMsg(null), 2600)
    return () => clearTimeout(t)
  }, [msg])
  const node = msg ? <div className={`toast ${msg.kind}`}>{msg.text}</div> : null
  return { show: (text: string, kind: 'ok' | 'err' = 'ok') => setMsg({ text, kind }), node }
}

/* ---------- 弹窗 ---------- */
export function Modal({ title, onClose, children, footer }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="btn ghost sm x" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        {children}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

/* ---------- 确认弹窗 ---------- */
export function Confirm({ title, body, confirmText = '确认', danger, onOk, onCancel }: {
  title: string; body: ReactNode; confirmText?: string; danger?: boolean
  onOk: () => void; onCancel: () => void
}) {
  return (
    <Modal title={title} onClose={onCancel}
      footer={<>
        <button className="btn" onClick={onCancel}>取消</button>
        <button className={`btn ${danger ? 'danger' : 'primary'}`} onClick={onOk}>{confirmText}</button>
      </>}>
      <div style={{ fontSize: 13, color: 'var(--text-2)' }}>{body}</div>
    </Modal>
  )
}

/* ---------- 分页 ---------- */
export function Pager({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="pager">
      <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>← 上一页</button>
      <span className="info">第 {page} / {pages} 页 · 共 {total} 条</span>
      <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页 →</button>
    </div>
  )
}

/* ---------- 表单字段 ---------- */
export function Field({ label, required, error, hint, children }: { label: string; required?: boolean; error?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className={`field ${error ? 'invalid' : ''}`} style={{ marginBottom: 14 }}>
      <label>{label}{required && <span className="req">*</span>}{hint && <span className="tiny muted" style={{ marginLeft: 8 }}>{hint}</span>}</label>
      {children}
      {error && <span className="err-msg">{error}</span>}
    </div>
  )
}

/* ---------- 纯 CSS 条形图（真实数据驱动） ---------- */
export function Bars({ data, max }: { data: { label: string; value: number; failed?: number }[]; max?: number }) {
  const peak = Math.max(1, max ?? Math.max(...data.map(d => d.value), 1))
  return (
    <div className="bars">
      {data.map((d, i) => (
        <div className="bar-col" key={i} title={`${d.label}: ${d.value} 次${d.failed ? `（失败 ${d.failed}）` : ''}`}>
          <span className="val">{d.value || ''}</span>
          <div className="bar" style={{ height: `${Math.round(d.value / peak * 78)}%` }} />
          <span className="lbl">{d.label}</span>
        </div>
      ))}
    </div>
  )
}

/* ---------- 时间线 ---------- */
export function Timeline({ items }: { items: { at: string; tone?: 'ok' | 'err'; children: ReactNode }[] }) {
  return (
    <div className="timeline">
      {items.map((it, i) => (
        <div className={`tl-item ${it.tone || ''}`} key={i}>
          <div className="tl-t">{it.at}</div>
          <div className="tl-c">{it.children}</div>
        </div>
      ))}
    </div>
  )
}

/* ---------- 页面框架 ---------- */
export function PageHead({ title, desc, extra }: { title: ReactNode; desc?: ReactNode; extra?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
      <div style={{ flex: 1, minWidth: 220 }}>
        <h1 className="page-title">{title}</h1>
        {desc && <p className="page-desc">{desc}</p>}
      </div>
      {extra}
    </div>
  )
}
