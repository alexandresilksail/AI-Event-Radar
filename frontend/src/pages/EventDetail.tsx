import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt } from '../api'
import { Card, Empty, ErrorBox, PageHead, SkeletonRows } from '../components/ui'

export default function EventDetail() {
  const { id } = useParams()
  const [e, setE] = useState<any>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    setE(null); setErr('')
    api(`/events/${id}`).then(setE).catch(er => setErr(er.message))
  }, [id])

  if (err) return <><Link to="/events" className="tiny">← 返回列表</Link>
    <div style={{ marginTop: 12 }}><ErrorBox err={err} /></div></>
  if (!e) return <><Link to="/events" className="tiny">← 返回列表</Link>
    <div className="card section"><SkeletonRows n={8} /></div></>

  const Row = ({ label, value }: { label: string; value: any }) => (
    <tr><th>{label}</th><td>{value ?? <span className="muted">未知</span>}</td></tr>
  )
  const lv = e.confidence_level

  return (
    <div>
      <PageHead
        title={<>{e.name} <span className={`badge ${lv === 'A' ? 'a' : lv === 'B' ? 'b' : lv === 'C' ? 'c' : 'pending'}`}>可信度 {lv}</span>
          {e.is_finished && <span className="badge">已结束</span>}</>}
        extra={<Link className="btn sm" to="/events">← 返回列表</Link>} />

      <div className="kv-chips" style={{ marginBottom: 16 }}>
        <span className="chip">类型 <b>{e.type || '待核实'}</b></span>
        <span className="chip">时间 <b>{e.start_time ? fmt(e.start_time) : '待核实'}</b></span>
        <span className="chip">地点 <b>{e.country} {e.city}</b></span>
        <span className="chip">形式 <b>{e.online === true ? '线上' : e.online === false ? '线下' : '待核实'}</b></span>
        <span className="chip">费用 <b>{e.is_free === true ? '免费' : e.is_free === false ? '收费' : '待核实'}</b></span>
      </div>

      <Card title="📋 活动详情">
        <table className="kv">
          <tbody>
            <Row label="活动类型" value={e.type} />
            <Row label="开始时间" value={fmt(e.start_time)} />
            <Row label="结束时间" value={fmt(e.end_time)} />
            <Row label="报名截止" value={fmt(e.registration_deadline)} />
            <Row label="国家 / 城市" value={`${e.country} / ${e.city}`} />
            <Row label="具体地址" value={e.address} />
            <Row label="主办方" value={e.organizer} />
            <Row label="承办方" value={e.co_organizer} />
            <Row label="活动语言" value={e.language} />
            <Row label="适合人群" value={e.audience} />
            <Row label="主要议题" value={e.topics} />
            <Row label="参加条件" value={e.requirements} />
            <Row label="票价" value={e.price} />
            <Row label="是否需报名" value={e.need_registration === null ? '待核实' : e.need_registration ? '需要' : '不需要'} />
            <Row label="抽取方式" value={e.extraction_method === 'ai' ? `AI 结构化抽取（模型置信度 ${e.ai_confidence}）` : '规则抽取'} />
            <Row label="首次发现" value={fmt(e.first_seen_at)} />
            <Row label="最近确认" value={fmt(e.last_confirmed_at)} />
          </tbody>
        </table>
      </Card>

      {e.summary && (
        <Card title="📝 原文摘要" className="section">
          <p style={{ fontSize: 13.5 }}>{e.summary}</p>
        </Card>
      )}

      <Card title={`🔗 全部来源（${e.sources.length}）`} hint="重复发布合并保留，不删除" className="section">
        {e.sources.length === 0 ? <Empty icon="🔗" title="暂无来源记录" /> : (
          <div className="table-wrap" style={{ boxShadow: 'none' }}>
            <table className="data">
              <thead><tr><th>原文标题</th><th>原文链接</th><th>首次收录</th></tr></thead>
              <tbody>
                {e.sources.map((s: any, i: number) => (
                  <tr key={i}>
                    <td>{s.title || '—'}</td>
                    <td><a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="url-cell">{s.url}</a></td>
                    <td className="tiny muted">{fmt(s.first_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {e.history?.length > 0 && (
        <Card title="🕘 变更历史" className="section">
          <div className="table-wrap" style={{ boxShadow: 'none' }}>
            <table className="data">
              <thead><tr><th>变更时间</th><th>变更内容</th></tr></thead>
              <tbody>
                {e.history.map((h: any, i: number) => (
                  <tr key={i}>
                    <td className="tiny muted">{fmt(h.recorded_at)}</td>
                    <td>{Object.entries(h.changed_fields).map(([k, v]: any) =>
                      <div key={k} className="tiny"><b>{k}</b>: {v.old || '—'} → {v.new}</div>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
