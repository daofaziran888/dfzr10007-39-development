"""协作看板共享发布快照：冻结、版本引用裁决与同版本导出。

设计目标：发布动作产出的是一份**不可回写**的共享版本——看板编号、协作成员和看板发布结果
在发布瞬间被冻结成快照，导出侧再生成同版本导出文件。后续成员名册变更只能创建新版本，
任何接口都不会修改或删除既有快照。

冲突裁决（成员名册 vs 发布结果）：
1. 先对协作成员做规范化（去空白、去重、按字典序排列），把"同一份名册"收敛成同一形态；
2. 对冻结内容（看板编号 + 看板名称 + 协作成员 + 发布结果）计算 SHA256 内容指纹；
3. 同一看板下指纹与最新版本相同 → 连续保存两次只认首份，幂等返回首份，不产生新版本/新文件；
4. 请求携带的基准引用 base_ref 与当前最新引用 ref 不一致 → 引用校验失败（409），
   两名编辑者并发调整名册时仅第一份通过；失败不写入任何数据，上一完整共享版本原样保留；
5. 导出文件携带与快照完全一致的指纹，可独立复核导出清单是否被篡改。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.store import store

SNAPSHOT_TABLE = "collab_snapshots"
EXPORT_TABLE = "shared_exports"


class SnapshotConflictError(Exception):
    """引用校验失败：基准引用已过期，调用方需要刷新后以新版本发布。"""

    def __init__(self, message: str, *, latest_ref: str | None) -> None:
        super().__init__(message)
        self.latest_ref = latest_ref


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_members(members: Any) -> list[str]:
    """把各种形态的成员名册规范化成有序、去重、无空白的列表。"""
    if members is None:
        raw: list[Any] = []
    elif isinstance(members, list):
        raw = members
    else:
        raw = str(members).replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    cleaned = [str(item).strip() for item in raw if str(item).strip()]
    return sorted(dict.fromkeys(cleaned))


def content_hash(
    board_no: str,
    board_name: str,
    members: list[str],
    publish_result: dict[str, Any],
) -> str:
    """冻结内容指纹：只由被冻结的业务内容决定，与操作者、冻结时间无关。

    「发布时间」是元数据而不是业务内容，刻意排除，否则同一份名册连续保存也会因为
    时间戳不同而算出不同指纹，破坏幂等规则。
    """
    business_result = {key: value for key, value in publish_result.items() if key != "发布时间"}
    payload = {
        "看板编号": board_no,
        "看板名称": board_name,
        "协作成员": members,
        "看板发布结果": business_result,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class SnapshotService:
    """共享发布快照的写入与读取；所有写操作在库锁内串行完成。"""

    # -- 读取侧 ---------------------------------------------------------------

    def _snapshots(self, board_no: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(SNAPSHOT_TABLE)
        if board_no is not None:
            rows = [row for row in rows if row.get("看板编号") == board_no]
        return rows

    def latest(self, board_no: str) -> dict[str, Any] | None:
        rows = self._snapshots(board_no)
        return rows[-1] if rows else None

    def list_snapshots(
        self,
        *,
        board_no: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        """按协作成员与看板发布结果排列版本：先看板编号，再版本号升序。"""
        rows = [copy.deepcopy(row) for row in self._snapshots(board_no)]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("看板编号", ""))
                or keyword in " ".join(row.get("协作成员", []))
                or keyword in str(row.get("看板名称", ""))
            ]
        return sorted(rows, key=lambda row: (str(row.get("看板编号", "")), int(row.get("版本号", 0))))

    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        for row in store.rows(SNAPSHOT_TABLE):
            if int(row.get("id", 0)) == snapshot_id:
                return copy.deepcopy(row)
        return None

    def list_exports(self, *, snapshot_id: int | None = None) -> list[dict[str, Any]]:
        rows = [copy.deepcopy(row) for row in store.rows(EXPORT_TABLE)]
        if snapshot_id is not None:
            rows = [row for row in rows if int(row.get("snapshot_id", 0)) == snapshot_id]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def get_export(self, export_id: int) -> dict[str, Any] | None:
        for row in store.rows(EXPORT_TABLE):
            if int(row.get("id", 0)) == export_id:
                return copy.deepcopy(row)
        return None

    # -- 写入侧 ---------------------------------------------------------------

    def publish(
        self,
        entry: dict[str, Any],
        *,
        operator: str | None = None,
        base_ref: str | None = None,
        members_override: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """冻结一块协作看板并生成同版本导出文件。

        返回 ``(快照, 导出文件, 是否新建)``；幂等命中时 ``是否新建=False``，只认首份。
        并发冲突抛出 :class:`SnapshotConflictError`，不会写入任何数据。

        ``members_override`` 用于并发裁决：编辑者把各自调整后的名册连同基准引用一起提交，
        服务端在临界区内决定谁通过；不传则冻结看板当前名册。
        """
        board_no = str(entry.get("看板编号", "")).strip()
        board_name = str(entry.get("看板名称", "")).strip()
        if members_override is None:
            members = normalize_members(entry.get("协作成员"))
        else:
            members = normalize_members(members_override)
        if not members:
            raise ValueError("协作成员不能为空，无法冻结共享版本")

        frozen_at = _now()
        # 看板发布结果：冻结发布瞬间的业务内容，后续编辑影响不到它。
        # 状态（草稿/已发布）属于流程字段、会随发布动作自身变化，不纳入冻结内容与指纹，
        # 否则同一份名册连续保存两次也会因为状态翻转而被误判成新版本。
        publish_result = {
            "看板编号": board_no,
            "看板名称": board_name,
            "协作成员": members,
            "图表集合": entry.get("图表集合"),
            "刷新频率": entry.get("刷新频率"),
            "评论开关": entry.get("评论开关"),
            "发布时间": frozen_at,
        }
        fingerprint = content_hash(board_no, board_name, members, publish_result)

        with store.transaction():
            latest = self.latest(board_no)

            # 规则 4：引用裁决（乐观并发控制）。
            # 基准引用落后于当前最新引用，说明编辑者基于的版本已被他人推进 → 冲突，
            # 两名编辑者并发调整名册时只有第一份能通过；失败不写入任何数据。
            # 基准引用等于最新引用、仅内容不同，属于正常的新版本编辑，放行到下面建新版本。
            # 该校验必须排在幂等判断之前。
            if base_ref is not None and latest is not None and base_ref != latest.get("ref"):
                raise SnapshotConflictError(
                    "协作成员名册已被其他编辑者调整，基准引用已失效，请刷新后基于最新版本发布",
                    latest_ref=str(latest.get("ref")),
                )

            # 规则 3：同一发布快照连续保存两次只认首份——引用有效且内容指纹一致即幂等。
            if latest is not None and latest.get("content_hash") == fingerprint:
                if members_override is not None:
                    entry["协作成员"] = "，".join(members)
                export_row = self._find_export(int(latest["id"]))
                assert export_row is not None  # 首份快照必有同版本导出文件
                return copy.deepcopy(latest), copy.deepcopy(export_row), False

            snapshot_rows = store.rows(SNAPSHOT_TABLE)
            export_rows = store.rows(EXPORT_TABLE)
            snapshot_id = max((int(row.get("id", 0)) for row in snapshot_rows), default=0) + 1
            version = (int(latest["版本号"]) + 1) if latest is not None else 1
            ref = f"{board_no}/v{version}"

            snapshot = {
                "id": snapshot_id,
                "快照编号": f"SNAP-{snapshot_id:04d}",
                "版本号": version,
                "ref": ref,
                "base_ref": base_ref or (latest.get("ref") if latest else None),
                "看板编号": board_no,
                "看板名称": board_name,
                "协作成员": members,
                "看板发布结果": copy.deepcopy(publish_result),
                "content_hash": fingerprint,
                "操作者": operator or "值班管理员",
                "冻结时间": publish_result["发布时间"],
                "frozen": True,
            }
            snapshot_rows.append(snapshot)

            export_id = max((int(row.get("id", 0)) for row in export_rows), default=0) + 1
            # 导出清单与快照同版本：指纹一致、引用一致，冻结内容原样落入文件载荷。
            export_record = {
                "id": export_id,
                "导出编号": f"SEXP-{export_id:04d}",
                "snapshot_id": snapshot_id,
                "快照编号": snapshot["快照编号"],
                "版本号": version,
                "ref": ref,
                "来源看板编号": board_no,
                "来源看板名称": board_name,
                "协作成员": copy.deepcopy(members),
                "导出格式": "JSON",
                "content_hash": fingerprint,
                "导出清单": {
                    "module": "collab-shared-snapshot",
                    "ref": ref,
                    "content_hash": fingerprint,
                    "看板编号": board_no,
                    "看板名称": board_name,
                    "协作成员": copy.deepcopy(members),
                    "看板发布结果": copy.deepcopy(publish_result),
                    "导出时间": frozen_at,
                },
                "任务状态": "已完成",
                "frozen": True,
            }
            export_rows.append(export_record)

            # 发布成功后看板进入已发布态，并记下当前共享版本引用（看板本身仍可继续编辑）。
            entry["status"] = "已发布"
            entry["pending"] = False
            entry["abnormal"] = False
            if members_override is not None:
                entry["协作成员"] = "，".join(members)
            entry["当前共享版本"] = ref

            return copy.deepcopy(snapshot), copy.deepcopy(export_record), True

    def update_members(self, entry: dict[str, Any], members: Any) -> dict[str, Any]:
        """调整协作成员名册。

        只改活动看板，绝不触碰历史快照；已发布看板改名册后需要重新发布生成新版本。
        """
        normalized = normalize_members(members)
        if not normalized:
            raise ValueError("协作成员不能为空")
        with store.transaction():
            entry["协作成员"] = "，".join(normalized)
            return copy.deepcopy(entry)

    # -- 校验侧 ---------------------------------------------------------------

    def verify_snapshot(self, snapshot_id: int) -> dict[str, Any]:
        """用 COLL-0002 等看板的冻结三要素复核导出清单：指纹对得上才算共享版本完整。"""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return {"snapshot_id": snapshot_id, "ok": False, "reason": "快照不存在"}
        export_row = self._find_export(snapshot_id)
        if export_row is None:
            raise AssertionError("快照缺少同版本导出文件")

        members = snapshot["协作成员"]
        recomputed = content_hash(
            snapshot["看板编号"],
            snapshot["看板名称"],
            members,
            snapshot["看板发布结果"],
        )
        manifest = export_row["导出清单"]
        checks = {
            "快照指纹可重算": recomputed == snapshot["content_hash"],
            "导出文件与快照同指纹": export_row["content_hash"] == snapshot["content_hash"],
            "导出清单与快照同指纹": manifest["content_hash"] == snapshot["content_hash"],
            "导出看板编号一致": manifest["看板编号"] == snapshot["看板编号"],
            "导出看板名称一致": manifest["看板名称"] == snapshot["看板名称"],
            "导出协作成员一致": manifest["协作成员"] == members,
            "导出发布结果一致": manifest["看板发布结果"] == snapshot["看板发布结果"],
            "引用一致": manifest["ref"] == snapshot["ref"] == export_row["ref"],
        }
        return {
            "ok": all(checks.values()),
            "snapshot_id": snapshot_id,
            "快照编号": snapshot["快照编号"],
            "ref": snapshot["ref"],
            "content_hash": snapshot["content_hash"],
            "checks": checks,
        }

    def _find_export(self, snapshot_id: int) -> dict[str, Any] | None:
        for row in store.rows(EXPORT_TABLE):
            if int(row.get("snapshot_id", 0)) == snapshot_id:
                return row
        return None


snapshot_service = SnapshotService()
