"""共享发布快照端到端校验。

运行（在 backend 目录下）：
    .venv/bin/python -m app.tests.test_snapshots
也兼容 pytest：.venv/bin/pytest app/tests/test_snapshots.py

每个用例独立重置内存库，避免用例之间因执行顺序产生隐式依赖。
"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.services.snapshot import normalize_members
from app.store import store

client = TestClient(app)


def reset_store() -> None:
    """每个用例前把内存库恢复成种子状态，并清空快照内部表。"""
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}


def _collab_entry(board_no: str) -> dict:
    for row in store.rows("collab"):
        if row.get("看板编号") == board_no:
            return row
    raise AssertionError(f"种子数据缺少 {board_no}")


def _publish(entry_id: int, /, **payload: object) -> dict:
    resp = client.post(f"/api/collab/{entry_id}/publish", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_normalize_members() -> None:
    assert normalize_members("陈昊, 刘雨桐，陈昊； 赵一鸣") == ["刘雨桐", "赵一鸣", "陈昊"]
    assert normalize_members([" 孙莉", "孙莉", "陈昊"]) == ["孙莉", "陈昊"]
    assert normalize_members(None) == []


def test_publish_freezes_and_creates_same_version_export() -> None:
    entry = _collab_entry("COLL-0002")
    body = _publish(2, operator="编辑者甲")
    assert body["ok"] is True and body["created"] is True
    snap = body["snapshot"]
    export_file = body["export_file"]

    # 冻结三要素与看板当前值一致，成员已规范化排序
    assert snap["看板编号"] == "COLL-0002"
    assert snap["看板名称"] == "会员留存分析看板"
    assert snap["协作成员"] == ["刘雨桐", "孙莉", "赵一鸣", "陈昊"]
    assert snap["版本号"] == 1 and snap["ref"] == "COLL-0002/v1"
    assert snap["frozen"] is True
    # 同版本导出文件：引用与指纹都与快照一致
    assert export_file["ref"] == snap["ref"]
    assert export_file["content_hash"] == snap["content_hash"]
    assert export_file["导出清单"]["看板发布结果"]["看板编号"] == "COLL-0002"
    # 看板进入已发布态并记录当前共享版本
    assert entry["status"] == "已发布" and entry["当前共享版本"] == "COLL-0002/v1"


def test_second_save_of_same_snapshot_keeps_first() -> None:
    first = _publish(2, operator="编辑者甲")
    assert first["created"] is True
    resp1 = client.post("/api/collab/2/publish", json={"operator": "编辑者甲"})
    resp2 = client.post("/api/collab/2/publish", json={"operator": "编辑者乙"})
    assert resp1.json()["created"] is False and resp2.json()["created"] is False
    assert resp1.json()["snapshot"]["id"] == resp2.json()["snapshot"]["id"] == first["snapshot"]["id"]

    snaps = client.get("/api/collab/snapshots?board_no=COLL-0002").json()
    assert snaps["total"] == 1  # 连续保存两次只认首份，不产生新版本
    exports = client.get("/api/export/shared?board_no=COLL-0002").json()["items"]
    assert len(exports) == 1  # 也不重复生成导出文件


def test_member_change_creates_new_version_without_rewriting_old() -> None:
    v1 = _publish(2, operator="编辑者甲")["snapshot"]

    # 改名册：只动活动看板
    resp = client.post("/api/collab/2/members", json={"members": ["陈昊", "刘雨桐", "周启"]})
    assert resp.status_code == 200 and resp.json()["ok"] is True

    # 未带 base_ref 的发布可以直接出新版本
    new_resp = client.post("/api/collab/2/publish", json={"operator": "编辑者甲"})
    assert new_resp.json()["created"] is True
    new = new_resp.json()["snapshot"]
    assert new["版本号"] == 2 and new["ref"] == "COLL-0002/v2"
    assert new["协作成员"] == ["刘雨桐", "周启", "陈昊"]

    # 旧版本原样保留、不可回写
    old_refetched = client.get(f"/api/collab/snapshots/{v1['id']}").json()
    assert old_refetched["协作成员"] == ["刘雨桐", "孙莉", "赵一鸣", "陈昊"]
    assert old_refetched["content_hash"] == v1["content_hash"]
    # 两版指纹必然不同，导出文件各自绑定自己的快照
    assert v1["content_hash"] != new["content_hash"]
    shared = client.get("/api/export/shared?board_no=COLL-0002").json()["items"]
    assert {row["ref"] for row in shared} == {"COLL-0002/v1", "COLL-0002/v2"}
    assert {row["content_hash"] for row in shared} == {v1["content_hash"], new["content_hash"]}


def test_concurrent_member_edits_only_first_passes_ref_check() -> None:
    board = "COLL-0001"
    entry = _collab_entry(board)
    # 先发布 v1 作为两名编辑者共同基于的基准版本
    base_ref = _publish(1, operator="编辑者甲")["latest_ref"]

    # 两名编辑者拿着同一个 base_ref，各自提交调整后的名册并发发布
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []

    def editor(members: list[str]) -> None:
        barrier.wait()  # 尽量让两个发布请求同时进入临界区
        resp = client.post(
            "/api/collab/1/publish",
            json={"operator": "编辑者", "base_ref": base_ref, "members": members},
        )
        outcomes.append({"status": resp.status_code, "body": resp.json()})

    t1 = threading.Thread(target=editor, args=(["张敏", "李晓东", "新人A"],))
    t2 = threading.Thread(target=editor, args=(["张敏", "李晓东", "新人B"],))
    t1.start(); t2.start(); t1.join(); t2.join()

    statuses = sorted(item["status"] for item in outcomes)
    assert statuses == [200, 409], outcomes
    winner = next(item for item in outcomes if item["status"] == 200)["body"]
    loser = next(item for item in outcomes if item["status"] == 409)["body"]
    assert winner["created"] is True and winner["latest_ref"] == f"{board}/v2"
    assert loser["latest_ref"] == f"{board}/v2"  # 失败方被告知最新引用

    # 失败后上一完整共享版本仍完整可用，且没有产生半成品版本
    snaps = client.get(f"/api/collab/snapshots?board_no={board}").json()["items"]
    assert [row["版本号"] for row in snaps] == [1, 2]
    assert entry["当前共享版本"] == f"{board}/v2"
    # 赢的名册被冻结，输的名册没有污染任何版本
    frozen_v2 = snaps[1]["协作成员"]
    assert frozen_v2 in (["张敏", "新人A", "李晓东"], ["张敏", "新人B", "李晓东"])
    loser_name = "新人B" if "新人A" in frozen_v2 else "新人A"
    assert all(loser_name not in row["协作成员"] for row in snaps)


def test_stale_ref_after_member_change_is_rejected_but_keeps_last_version() -> None:
    board = "COLL-0003"
    v1_ref = _publish(3)["latest_ref"]
    # 先让 v2 正式发布，此时 v1 引用成为过期引用
    client.post("/api/collab/3/members", json={"members": "周凯, 吴婷, 冯远"})
    v2 = client.post("/api/collab/3/publish", json={}).json()
    assert v2["created"] is True and v2["latest_ref"] == f"{board}/v2"

    # 拿着旧引用 v1 再发布（模拟另一位编辑者基于过期版本调整名册）→ 409
    stale = client.post(
        "/api/collab/3/publish",
        json={"base_ref": v1_ref, "members": ["周凯", "新人C"]},
    )
    assert stale.status_code == 409
    assert stale.json()["latest_ref"] == f"{board}/v2"

    # 失败不产生半成品，上一完整共享版本 v2 原样保留
    snaps = client.get(f"/api/collab/snapshots?board_no={board}").json()["items"]
    assert [row["ref"] for row in snaps] == [f"{board}/v1", f"{board}/v2"]
    assert all("新人C" not in row["协作成员"] for row in snaps)

    # 刷新到最新引用 v2 后即可发布 v3
    refreshed = client.post(
        "/api/collab/3/publish",
        json={"base_ref": f"{board}/v2", "members": ["周凯", "吴婷", "冯远", "新人C"]},
    )
    assert refreshed.status_code == 200 and refreshed.json()["created"] is True
    assert refreshed.json()["latest_ref"] == f"{board}/v3"
    snaps_after = client.get(f"/api/collab/snapshots?board_no={board}").json()["items"]
    assert [row["ref"] for row in snaps_after] == [f"{board}/v1", f"{board}/v2", f"{board}/v3"]


def test_empty_members_are_rejected_without_touching_versions() -> None:
    _publish(3)
    resp = client.post("/api/collab/3/publish", json={"members": ["  ", ""]})
    assert resp.status_code == 400
    snaps = client.get("/api/collab/snapshots?board_no=COLL-0003").json()["items"]
    assert len(snaps) == 1  # 空名册没有产生任何版本


def test_export_manifest_verifies_against_coll_0002() -> None:
    v1 = _publish(2, operator="编辑者甲")["snapshot"]
    client.post("/api/collab/2/members", json={"members": ["陈昊", "刘雨桐", "周启"]})
    v2 = client.post("/api/collab/2/publish", json={}).json()["snapshot"]

    # 用 COLL-0002 的看板名称、协作成员和发布结果核对每一版导出清单
    snaps = client.get("/api/collab/snapshots?board_no=COLL-0002").json()["items"]
    assert [row["版本号"] for row in snaps] == [1, 2]
    for snap in snaps:
        result = client.post(f"/api/collab/snapshots/{snap['id']}/verify").json()
        assert result["ok"] is True, result
        assert result["checks"]["导出看板名称一致"] is True
        assert result["checks"]["导出协作成员一致"] is True
        assert result["checks"]["导出发布结果一致"] is True

    # 下载接口返回的清单与 v1 快照冻结内容逐项对得上
    shared = client.get("/api/export/shared?board_no=COLL-0002").json()["items"]
    target = next(row for row in shared if row["ref"] == "COLL-0002/v1")
    dl = client.get(f"/api/export/shared/{target['id']}/download")
    assert dl.status_code == 200
    manifest = dl.json()
    assert manifest["看板编号"] == "COLL-0002"
    assert manifest["看板名称"] == "会员留存分析看板"
    assert manifest["协作成员"] == v1["协作成员"]
    assert manifest["看板发布结果"] == v1["看板发布结果"]
    assert manifest["content_hash"] == v1["content_hash"]
    assert manifest["ref"] == v1["ref"]
    # v2 的指纹与 v1 不同，各自绑定自己的导出文件
    other = next(row for row in shared if row["ref"] == "COLL-0002/v2")
    assert other["content_hash"] == v2["content_hash"] != v1["content_hash"]


def test_overview_only_shows_frozen_publish_results() -> None:
    # 未发布前：概览里没有任何共享发布结果
    empty = client.get("/api/overview/shared-publish").json()
    assert empty["shared_version_total"] == 0 and empty["items"] == []

    _publish(2, operator="编辑者甲")
    data = client.get("/api/overview/shared-publish").json()
    assert data["shared_version_total"] == 1 and data["board_total"] == 1
    item = data["items"][0]
    assert item["看板编号"] == "COLL-0002"
    assert item["发布结果来源"] == "协作看板共享发布快照（已冻结）"
    assert isinstance(item["协作成员"], list) and item["协作成员"]

    # 内部快照表不出现在运营概览的业务模块里，未冻结看板不显示成员
    overview = client.get("/api/overview").json()
    names = {module["name"] for module in overview["modules"]}
    assert "collab_snapshots" not in names and "shared_exports" not in names


def test_snapshots_have_no_mutation_endpoint() -> None:
    snap_id = _publish(2)["snapshot"]["id"]
    snaps_before = client.get("/api/collab/snapshots").json()["items"]
    # 不存在 PATCH/PUT/DELETE 路由：框架应返回 405 而不是改写数据
    for method in ("put", "patch", "delete"):
        resp = getattr(client, method)(f"/api/collab/snapshots/{snap_id}")
        assert resp.status_code == 405, method
    # 数据确实没变
    again = client.get(f"/api/collab/snapshots/{snap_id}").json()
    assert again["协作成员"] == snaps_before[0]["协作成员"]
    # 导出文件同样没有改写入口
    export_id = client.get("/api/export/shared").json()["items"][0]["id"]
    for method in ("put", "patch", "delete"):
        resp = getattr(client, method)(f"/api/export/shared/{export_id}")
        assert resp.status_code in (405, 404), method


def main() -> None:
    tests = [obj for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]
    for test in tests:
        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        test()
        print(f"PASS {test.__name__}")
    print(f"\n全部 {len(tests)} 项共享发布快照校验通过")


if __name__ == "__main__":
    main()
