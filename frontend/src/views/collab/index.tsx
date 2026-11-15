import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/collab'
const columns = [
  "看板编号",
  "看板名称",
  "协作成员",
  "图表集合",
  "刷新频率",
  "评论开关",
  "创建时间",
  "看板状态"
]
const actions = [
  "归档看板",
  "删除看板"
]

type Row = Record<string, string | number | null>
type Snapshot = {
  id: number
  快照编号: string
  版本号: number
  ref: string
  base_ref: string | null
  看板编号: string
  看板名称: string
  协作成员: string[]
  看板发布结果: Record<string, unknown>
  content_hash: string
  操作者: string
  冻结时间: string
}
type PublishResponse = {
  ok: boolean
  message: string
  created: boolean
  latest_ref: string | null
  snapshot: Snapshot | null
}

export default function CollabView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [editingBoard, setEditingBoard] = useState<Row | null>(null)
  const [memberDraft, setMemberDraft] = useState('')
  const [publishing, setPublishing] = useState(false)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('协作看板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '看板协作管理列表读取失败')
    }
  }, [filters])

  const reloadSnapshots = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/snapshots`)
      if (response.ok) {
        const payload = await response.json()
        setSnapshots(payload.items ?? [])
      }
    } catch {
      // 版本区读取失败不阻塞主列表
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadSnapshots()
  }, [reloadSnapshots])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message ?? '看板协作管理动作未生效，请稍后重试')
      }
      setInfoMessage(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '看板协作管理操作失败')
    }
  }

  // 发布不可回写的共享版本：先冻结看板编号/协作成员/发布结果，再生成同版本导出文件。
  async function publishSharedVersion(row: Row) {
    setErrorMessage('')
    setInfoMessage('')
    setPublishing(true)
    try {
      const response = await request(`${ENDPOINT}/${row.id}/publish`, {
        method: 'POST',
        body: JSON.stringify({ operator: '值班管理员' }),
      })
      const payload: PublishResponse = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message ?? '共享版本发布失败')
      }
      setInfoMessage(payload.message)
      await reload()
      await reloadSnapshots()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '共享版本发布失败')
    } finally {
      setPublishing(false)
    }
  }

  function openMemberEditor(row: Row) {
    const current = typeof row['协作成员'] === 'string' ? String(row['协作成员']) : ''
    setEditingBoard(row)
    setMemberDraft(current.split('，').join(','))
    setErrorMessage('')
    setInfoMessage('')
  }

  async function saveMembers() {
    if (!editingBoard) return
    const members = memberDraft.split(',').map((item) => item.trim()).filter(Boolean)
    if (members.length === 0) {
      setErrorMessage('协作成员不能为空')
      return
    }
    try {
      const response = await request(`${ENDPOINT}/${editingBoard.id}/members`, {
        method: 'POST',
        body: JSON.stringify({ members }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message ?? '名册更新失败')
      }
      setInfoMessage('协作成员名册已更新；历史共享版本不会回写，需重新发布生成新版本')
      setEditingBoard(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '名册更新失败')
    }
  }

  return (
    <section className="page" data-module="collab">
      <header className="page-head">
        <div>
          <h2>看板协作管理</h2>
          <p className="page-desc">
            发布动作会冻结看板编号、协作成员与发布结果，生成不可回写的共享版本及同版本导出文件；
            名册变更只能创建新版本，不能改写历史版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出看板协作管理清单
          </button>
        </div>
      </header>

      {(infoMessage || errorMessage) && (
        <div className="notice-bar">
          {infoMessage && <span className="info-text">{infoMessage}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </div>
      )}

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>当前共享版本</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row['当前共享版本'] ? String(row['当前共享版本']) : '未发布'}</td>
              <td className="row-actions">
                <button className="link" type="button" disabled={publishing} onClick={() => void publishSharedVersion(row)}>
                  发布共享版本
                </button>
                <button className="link" type="button" onClick={() => openMemberEditor(row)}>
                  调整协作成员
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无看板协作管理数据</td>
            </tr>
          )}
        </tbody>
      </table>

      {editingBoard && (
        <div className="modal-mask" role="dialog" aria-modal="true">
          <div className="modal-card">
            <h3>调整协作成员 · {String(editingBoard['看板编号'])}</h3>
            <p className="page-desc">用英文逗号分隔多名成员；保存仅影响活动看板，历史共享快照保持不变。</p>
            <textarea
              rows={4}
              value={memberDraft}
              placeholder="例如：陈昊, 刘雨桐, 赵一鸣"
              onChange={(event) => setMemberDraft(event.target.value)}
            />
            <div className="modal-actions">
              <button className="btn ghost" type="button" onClick={() => setEditingBoard(null)}>取消</button>
              <button className="btn primary" type="button" onClick={() => void saveMembers()}>保存名册</button>
            </div>
          </div>
        </div>
      )}

      <section className="snapshot-panel">
        <header className="page-head">
          <div>
            <h3>共享发布快照（只读 · 不可回写）</h3>
            <p className="page-desc">按看板编号与版本号排列；每份快照都绑定同版本导出文件，可凭内容指纹复核。</p>
          </div>
          <button className="btn" type="button" onClick={() => void reloadSnapshots()}>刷新版本</button>
        </header>
        <table className="data-table">
          <thead>
            <tr>
              <th>版本引用</th>
              <th>看板编号</th>
              <th>看板名称</th>
              <th>冻结协作成员</th>
              <th>操作者</th>
              <th>冻结时间</th>
              <th>内容指纹</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snap) => (
              <tr key={snap.id}>
                <td>{snap.ref}</td>
                <td>{snap.看板编号}</td>
                <td>{snap.看板名称}</td>
                <td>{snap.协作成员.join('、')}</td>
                <td>{snap.操作者}</td>
                <td>{snap.冻结时间}</td>
                <td className="hash-cell" title={snap.content_hash}>{snap.content_hash.slice(0, 12)}…</td>
              </tr>
            ))}
            {snapshots.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={7}>尚无共享发布快照，发布看板后在此生成冻结版本</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条看板协作管理记录 · {snapshots.length} 份共享发布快照</span>
      </footer>
    </section>
  )
}
