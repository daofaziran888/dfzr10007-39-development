import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/export'
const columns = [
  "任务编号",
  "数据源名称",
  "导出格式",
  "字段选择",
  "筛选条件",
  "导出上限",
  "文件大小",
  "任务状态"
]
const actions = [
  "开始导出",
  "标记完成",
  "清理文件"
]

type Row = Record<string, string | number | null>
type SharedExport = {
  id: number
  导出编号: string
  快照编号: string
  版本号: number
  ref: string
  来源看板编号: string
  来源看板名称: string
  协作成员: string[]
  导出格式: string
  content_hash: string
  任务状态: string
  导出清单: {
    看板发布结果: Record<string, unknown>
    导出时间: string
  }
}

export default function ExportView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [sharedRows, setSharedRows] = useState<SharedExport[]>([])
  const [boardFilter, setBoardFilter] = useState('')
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('导出任务列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析导出中心列表读取失败')
    }
  }, [filters])

  const reloadShared = useCallback(async () => {
    try {
      const query = boardFilter ? `?board_no=${encodeURIComponent(boardFilter)}` : ''
      const response = await request(`${ENDPOINT}/shared${query}`)
      if (response.ok) {
        const payload = await response.json()
        setSharedRows(payload.items ?? [])
      }
    } catch {
      // 共享视图失败不影响常规任务列表
    }
  }, [boardFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadShared()
  }, [reloadShared])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('分析导出中心动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析导出中心操作失败')
    }
  }

  return (
    <section className="page" data-module="export">
      <header className="page-head">
        <div>
          <h2>分析导出中心</h2>
          <p className="page-desc">
            维护分析导出任务；下方「共享发布快照」为协作看板发布时冻结的同版本导出文件，
            按协作成员与看板发布结果排列，只读且不可回写。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分析导出中心清单
          </button>
        </div>
      </header>

      {errorMessage && <div className="notice-bar"><span className="error-text">{errorMessage}</span></div>}

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
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无分析导出中心数据，可先登记导出任务</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="snapshot-panel">
        <header className="page-head">
          <div>
            <h3>共享发布快照 · 同版本导出文件</h3>
            <p className="page-desc">
              仅展示来源明确、成员已冻结的发布结果；未冻结成员的看板不会出现在这里。
            </p>
          </div>
          <div className="page-actions">
            <input
              value={boardFilter}
              placeholder="按看板编号过滤，如 COLL-0002"
              onChange={(event) => setBoardFilter(event.target.value)}
            />
            <button className="btn" type="button" onClick={() => void reloadShared()}>刷新</button>
          </div>
        </header>
        <table className="data-table">
          <thead>
            <tr>
              <th>版本引用</th>
              <th>来源看板</th>
              <th>冻结协作成员</th>
              <th>看板发布结果</th>
              <th>导出时间</th>
              <th>内容指纹</th>
              <th>文件</th>
            </tr>
          </thead>
          <tbody>
            {sharedRows.map((item) => (
              <tr key={item.id}>
                <td>{item.ref}</td>
                <td>{item.来源看板编号}<br /><span className="muted-text">{item.来源看板名称}</span></td>
                <td>{item.协作成员.join('、')}</td>
                <td className="result-cell">
                  {Object.entries(item.导出清单.看板发布结果)
                    .filter(([key]) => key !== '发布时间')
                    .map(([key, value]) => (
                      <span key={key} className="result-tag">{key}：{Array.isArray(value) ? value.join('、') : String(value)}</span>
                    ))}
                </td>
                <td>{item.导出清单.导出时间}</td>
                <td className="hash-cell" title={item.content_hash}>{item.content_hash.slice(0, 12)}…</td>
                <td>
                  <button
                    className="link"
                    type="button"
                    onClick={() => window.open(`${ENDPOINT}/shared/${item.id}/download`, '_blank')}
                  >
                    下载 JSON
                  </button>
                </td>
              </tr>
            ))}
            {sharedRows.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={7}>暂无共享发布快照，去看板协作管理发布后在此生成同版本导出文件</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条导出任务 · {sharedRows.length} 份共享同版本导出文件</span>
      </footer>
    </section>
  )
}
