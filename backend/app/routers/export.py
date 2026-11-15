"""分析导出中心接口：维护导出任务，覆盖开始导出、标记完成、清理文件等动作。

另外提供「共享发布快照」独立视图：协作看板发布时冻结出的同版本导出文件在这里集中展示，
按协作成员与看板发布结果排列，概览只呈现来源明确、成员已冻结的发布结果。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.export import ExportService
from app.services.snapshot import snapshot_service

router = APIRouter(prefix="/api/export", tags=["分析导出中心"])

service = ExportService()

LIST_FIELDS = ["任务编号", "数据源名称", "导出格式", "字段选择", "筛选条件", "导出上限", "文件大小", "任务状态"]
STATUSES = ["草稿", "导出中", "已完成", "已过期"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、导出中、已完成、已过期"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤分析导出中心列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/shared")
def list_shared_exports(
    board_no: str | None = Query(default=None, description="按来源看板编号过滤"),
    keyword: str | None = Query(default=None, description="按看板编号/名称/协作成员检索"),
) -> dict[str, Any]:
    """共享发布快照独立视图：只展示冻结后的同版本导出文件，按协作成员与发布结果排列。"""
    snapshots = snapshot_service.list_snapshots(board_no=board_no, keyword=keyword)
    snapshot_ids = {int(row["id"]) for row in snapshots}
    items = [row for row in snapshot_service.list_exports() if int(row["snapshot_id"]) in snapshot_ids]
    return {"module": "collab-shared-export", "total": len(items), "items": items}


@router.get("/shared/{export_id}/download")
def download_shared_export(export_id: int) -> JSONResponse:
    """下载某份不可回写的同版本导出文件（冻结清单 + 内容指纹）。"""
    export_row = snapshot_service.get_export(export_id)
    if export_row is None:
        raise HTTPException(status_code=404, detail=f"共享导出文件 {export_id} 不存在")
    filename = f"{export_row['导出编号']}-{export_row['ref'].replace('/', '-')}.json"
    return JSONResponse(
        content=export_row["导出清单"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条导出任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条导出任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="导出任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条导出任务执行开始导出、标记完成、清理文件；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析导出中心清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "export", "total": total, "items": items}
