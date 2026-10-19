import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../api'

export default function EventDetail() {
  const { id } = useParams()
  const [e, setE] = useState<any>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api(`/events/${id}`).then(setE).catch(er => setErr(er.message))
  }, [id])

  if (err) return <p style={{ color: 'var(--red)' }}>{err}</p>
  if (!e) return <p className="muted">加载中…</p>

  const Row = ({ label, value }: { label: string; value: any }) => (
    <tr><th style={{ width: 140 }}>{label}</th><td>{value ?? '未知'}</td></tr>
  )

  return (
    <div>
      <h1 className="page-title">{e.name} <span className={`badge ${e.confidence_level === 'A' ? 'a' : e.confidence_level === 'B' ? 'b' : e.confidence_level === 'C' ? 'c' : 'pending'}`}>可信度 {e.confidence_level}</span></h1>
      <Link to="/events">← 返回列表</Link>
      <table style={{ marginTop: 14 }}>
        <tbody>
          <Row label="活动类型" value={e.type} />
          <Row label="开始时间" value={fmt(e.start_time)} />
          <Row label="结束时间" value={fmt(e.end_time)} />
          <Row label="报名截止" value={fmt(e.registration_deadline)} />
          <Row label="国家 / 城市" value={`${e.country} / ${e.city}`} />
          <Row label="具体地址" value={e.address} />
          <Row label="线上 / 线下" value={e.online === true ? '线上' : e.online === false ? '线下' : '待核实'} />
          <Row label="主办方" value={e.organizer} />
          <Row label="承办方" value={e.co_organizer} />
          <Row label="活动语言" value={e.language} />
          <Row label="适合人群" value={e.audience} />
          <Row label="主要议题" value={e.topics} />
          <Row label="参加条件" value={e.requirements} />
          <Row label="票价" value={e.price} />
          <Row label="是否免费" value={e.is_free === true ? '免费' : e.is_free === false ? '收费' : '待核实'} />
          <Row label="是否需报名" value={e.need_registration === null ? '待核实' : e.need_registration ? '需要' : '不需要'} />
          <Row label="活动状态" value={e.is_finished ? '已结束' : e.status} />
          <Row label="抽取方式" value={e.extraction_method === 'ai' ? `AI 结构化抽取（模型置信度 ${e.ai_confidence}）` : '规则抽取'} />
          <Row label="首次发现" value={fmt(e.first_seen_at)} />
          <Row label="最近确认" value={fmt(e.last_confirmed_at)} />
        </tbody>
      </table>

      {e.summary && (
        <div className="card section" style={{ marginTop: 16 }}>
          <h3>原文摘要</h3><p style={{ marginTop: 8, fontSize: 14 }}>{e.summary}</p>
        </div>
      )}

      <div className="section" style={{ marginTop: 16 }}>
        <h3 style={{ marginBottom: 8 }}>全部来源（重复发布合并保留，不删除）</h3>
        <table>
          <thead><tr><th>原文标题</th><th>原文链接</th><th>首次收录</th></tr></thead>
          <tbody>
            {e.sources.map((s: any, i: number) => (
              <tr key={i}><td>{s.title || '—'}</td>
                <td><a href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.url.slice(0, 80)}…</a></td>
                <td>{fmt(s.first_seen_at)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      {e.history?.length > 0 && (
        <div className="section">
          <h3 style={{ marginBottom: 8 }}>变更历史</h3>
          <table>
            <thead><tr><th>变更时间</th><th>变更内容</th></tr></thead>
            <tbody>
              {e.history.map((h: any, i: number) => (
                <tr key={i}><td>{fmt(h.recorded_at)}</td>
                  <td>{Object.entries(h.changed_fields).map(([k, v]: any) =>
                    <div key={k}><b>{k}</b>: {v.old || '—'} → {v.new}</div>)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
