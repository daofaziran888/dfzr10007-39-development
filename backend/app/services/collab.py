"""看板协作管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.snapshot import SnapshotConflictError, snapshot_service
from app.store import store

MODULE = "collab"
REQUIRED_FIELDS = ["看板编号", "看板名称", "协作成员"]
STATUS_ORDER = ["草稿", "协作中", "已发布", "已归档"]
ACTION_RULES = {"发布看板": "草稿", "归档看板": "协作中", "删除看板": "已发布"}
NEGATIVE_ACTIONS = []


class CollabService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("看板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_members(self, entry_id: int, members: Any) -> tuple[dict[str, Any] | None, str]:
        """调整活动看板的协作成员名册；历史共享快照不受影响，只能靠重新发布出新版本。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"协作看板 {entry_id} 不存在或已归档"
        try:
            return snapshot_service.update_members(entry, members), "协作成员名册已更新，历史共享版本保持不变"
        except ValueError as exc:
            return None, str(exc)

    def publish_snapshot(
        self,
        entry_id: int,
        *,
        operator: str | None = None,
        base_ref: str | None = None,
        members: Any = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, str, bool]:
        """发布共享版本：冻结看板编号、协作成员和发布结果，并生成同版本导出文件。

        最后一个返回值标记是否为并发引用冲突，供路由区分 409 与 400。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, None, False, f"协作看板 {entry_id} 不存在或已归档", False
        try:
            snapshot, export_row, created = snapshot_service.publish(
                entry, operator=operator, base_ref=base_ref, members_override=members
            )
        except SnapshotConflictError:
            return None, None, False, "协作成员名册已被其他编辑者调整，基准引用已失效，请刷新后基于最新版本发布", True
        except ValueError as exc:
            return None, None, False, str(exc), False
        if created:
            message = f"共享发布快照 {snapshot['ref']} 已冻结，同版本导出文件 {export_row['导出编号']} 已生成"
        else:
            message = f"发布内容与最新共享版本 {snapshot['ref']} 完全一致，已存在的首份快照被保留，未重复生成版本"
        return snapshot, export_row, created, message, False

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"协作看板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于看板协作管理可执行范围"
        # 发布动作改为产出不可回写的共享版本，而不是简单改个状态。
        if action == "发布看板":
            snapshot, _export, _created, message, _conflict = self.publish_snapshot(entry_id)
            if snapshot is None:
                return None, message
            return entry, message
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"协作看板已{action}"
