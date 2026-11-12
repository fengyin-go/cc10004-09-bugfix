"""数据集管理接口：维护数据集定义，覆盖提交审核、生效发布、废弃下线等动作。

动作契约：动作名放在请求体顶层（ActionPayload.action）；
业务拒绝一律返回对应的 HTTP 错误码（400/404/409），不再包装成 200 成功。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.datastore import DatastoreService

router = APIRouter(prefix="/api/datastore", tags=["数据集管理"])

service = DatastoreService()

LIST_FIELDS = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
STATUSES = ["草稿", "待审核", "已生效", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按数据集编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按数据集编号与状态过滤数据集管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据集管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "datastore", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条数据集定义明细，附带关联分享授权；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据集定义 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条数据集定义，缺字段或编号重复时返回 400 并说明原因。"""
    entry, error = service.create_entry(payload.values)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return ActionResult(ok=True, message="数据集定义已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条数据集定义执行提交审核、生效发布、废弃下线。

    生命周期不满足时返回 409 等业务错误，不再用 200 掩盖拒绝。
    """
    entry, message, status_code = service.run_action(entry_id, payload.action)
    if entry is None:
        raise HTTPException(status_code=status_code, detail=message)
    return ActionResult(ok=True, message=message, entry=entry)
