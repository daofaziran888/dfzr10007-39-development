"""数据洞察与自助分析平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services.snapshot import snapshot_service
from app.store import store

app = FastAPI(title="数据洞察与自助分析平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()


@app.get("/api/overview/shared-publish")
def shared_publish_overview() -> dict[str, object]:
    """共享发布结果概览：只呈现来源明确、成员已冻结的发布快照。

    未冻结的协作看板（草稿/协作中或从未发布）不会出现在这里，避免把活动名册误当成发布结果。
    """
    snapshots = snapshot_service.list_snapshots()
    items = [
        {
            "快照编号": row["快照编号"],
            "ref": row["ref"],
            "版本号": row["版本号"],
            "看板编号": row["看板编号"],
            "看板名称": row["看板名称"],
            "协作成员": row["协作成员"],
            "冻结时间": row["冻结时间"],
            "操作者": row["操作者"],
            "content_hash": row["content_hash"],
            "发布结果来源": "协作看板共享发布快照（已冻结）",
        }
        for row in snapshots
        if row.get("frozen") is True
    ]
    latest_per_board = {item["看板编号"]: item for item in items}
    return {
        "shared_version_total": len(items),
        "board_total": len(latest_per_board),
        "items": items,
        "latest_items": list(latest_per_board.values()),
    }
