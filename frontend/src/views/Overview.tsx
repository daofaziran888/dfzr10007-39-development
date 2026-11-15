import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

type SharedPublish = {
  快照编号: string
  ref: string
  版本号: number
  看板编号: string
  看板名称: string
  协作成员: string[]
  冻结时间: string
  操作者: string
  content_hash: string
  发布结果来源: string
}

type SharedOverview = {
  shared_version_total: number
  board_total: number
  items: SharedPublish[]
  latest_items: SharedPublish[]
}

const fallbackCards = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
const fallbackModules = [{"name": "数据集市构建", "created": 0, "pending": 0, "abnormal": 0}, {"name": "自助SQL查询", "created": 0, "pending": 0, "abnormal": 0}, {"name": "拖拽可视化", "created": 0, "pending": 0, "abnormal": 0}, {"name": "看板协作管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标异动归因", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析模型沉淀", "created": 0, "pending": 0, "abnormal": 0}, {"name": "查询优化建议", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据故事编排", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据准备流水线", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据目录检索", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据采样管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "同环比分析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析导出中心", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分析告警规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据定时刷新", "created": 0, "pending": 0, "abnormal": 0}, {"name": "查询审计日志", "created": 0, "pending": 0, "abnormal": 0}]

export default function Overview() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)
  const [shared, setShared] = useState<SharedOverview | null>(null)

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
      })
      .catch(() => undefined)
    fetchJson<SharedOverview>('/api/overview/shared-publish')
      .then(setShared)
      .catch(() => undefined)
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="snapshot-panel">
        <header className="page-head">
          <div>
            <h3>共享发布结果概览</h3>
            <p className="page-desc">
              只呈现来源明确、成员已冻结的发布快照（共 {shared?.shared_version_total ?? 0} 个版本 /
              {' '}{shared?.board_total ?? 0} 块看板）；未冻结成员的看板不在此显示。
            </p>
          </div>
        </header>
        <table className="data-table">
          <thead>
            <tr><th>版本引用</th><th>看板编号</th><th>看板名称</th><th>冻结协作成员</th><th>操作者</th><th>冻结时间</th><th>结果来源</th></tr>
          </thead>
          <tbody>
            {(shared?.items ?? []).map((item) => (
              <tr key={item.快照编号}>
                <td>{item.ref}</td>
                <td>{item.看板编号}</td>
                <td>{item.看板名称}</td>
                <td>{item.协作成员.join('、')}</td>
                <td>{item.操作者}</td>
                <td>{item.冻结时间}</td>
                <td><span className="source-tag">{item.发布结果来源}</span></td>
              </tr>
            ))}
            {(!shared || shared.items.length === 0) && (
              <tr>
                <td className="empty-state" colSpan={7}>暂无已冻结的共享发布结果</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </section>
  )
}
