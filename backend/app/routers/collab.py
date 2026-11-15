"""看板协作管理接口：维护协作看板，覆盖发布看板、归档看板、删除看板等动作。

发布动作不再只是改状态，而是冻结出一份不可回写的共享发布快照，并在导出侧生成同版本文件。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas import (
    ActionResult,
    EntryPayload,
    MemberUpdatePayload,
    PageResult,
    SnapshotActionResult,
    SnapshotPublishPayload,
)
from app.services.collab import CollabService
from app.services.snapshot import snapshot_service

router = APIRouter(prefix="/api/collab", tags=["看板协作管理"])

service = CollabService()

LIST_FIELDS = ["看板编号", "看板名称", "协作成员", "图表集合", "刷新频率", "评论开关", "创建时间", "看板状态"]
STATUSES = ["草稿", "协作中", "已发布", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按看板编号检索"),
    status: str | None = Query(default=None, description="草稿、协作中、已发布、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按看板编号与状态过滤看板协作管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/snapshots")
def list_snapshots(
    board_no: str | None = Query(default=None, description="按看板编号过滤共享版本"),
    keyword: str | None = Query(default=None, description="按看板编号/名称/协作成员检索"),
) -> dict[str, Any]:
    """共享发布快照独立视图：按协作成员与看板发布结果排列版本，只含已冻结内容。"""
    items = snapshot_service.list_snapshots(board_no=board_no, keyword=keyword)
    return {"module": "collab-shared-snapshot", "total": len(items), "items": items}


@router.post("/snapshots/{snapshot_id}/verify")
def verify_snapshot(snapshot_id: int) -> dict[str, Any]:
    """复核某份共享版本：重算内容指纹并核对同版本导出清单。"""
    result = snapshot_service.verify_snapshot(snapshot_id)
    if not result["ok"] and "reason" in result:
        raise HTTPException(status_code=404, detail=result["reason"])
    return result


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int) -> dict[str, Any]:
    """读取单份冻结快照；快照不可编辑，接口只提供只读视图。"""
    snapshot = snapshot_service.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"共享发布快照 {snapshot_id} 不存在")
    return snapshot


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条协作看板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"协作看板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条协作看板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="协作看板已登记", entry=entry)


@router.post("/{entry_id}/members", response_model=ActionResult)
def update_members(entry_id: int, payload: MemberUpdatePayload) -> ActionResult:
    """调整协作成员名册；只影响活动看板，历史共享快照绝不回写。"""
    entry, message = service.update_members(entry_id, payload.members)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/publish", response_model=SnapshotActionResult)
def publish_snapshot(entry_id: int, payload: SnapshotPublishPayload | None = None) -> SnapshotActionResult:
    """发布不可回写的共享版本：先冻结看板编号/协作成员/发布结果，再生成同版本导出文件。

    并发裁决：携带 base_ref 时若与当前最新引用不一致，返回 409，保留上一完整共享版本。
    """
    payload = payload or SnapshotPublishPayload()
    snapshot, export_row, created, message, conflict = service.publish_snapshot(
        entry_id,
        operator=payload.operator,
        base_ref=payload.base_ref,
        members=payload.members,
    )
    if snapshot is None:
        latest = None
        entry = service.get_entry(entry_id)
        if entry is not None:
            latest_row = snapshot_service.latest(str(entry.get("看板编号", "")))
            latest = latest_row["ref"] if latest_row else None
        # 引用失效 → 409；看板不存在 → 404；其余入参问题（如空名册）→ 400。
        if entry is None:
            status_code = 404
        elif conflict:
            status_code = 409
        else:
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": False,
                "message": message,
                "created": False,
                "snapshot": None,
                "export_file": None,
                "entry": None,
                "latest_ref": latest,
            },
        )
    return SnapshotActionResult(
        ok=True,
        message=message,
        entry=service.get_entry(entry_id),
        snapshot=snapshot,
        export_file=export_row,
        created=created,
        latest_ref=snapshot["ref"],
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条协作看板执行发布看板、归档看板、删除看板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出看板协作管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "collab", "total": total, "items": items}
