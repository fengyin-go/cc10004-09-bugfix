"""数据分享接口：维护分享链接，覆盖提交生成、生效分享、撤销链接等动作。

动作契约：动作名放在请求体顶层（ActionPayload.action），补充字段放 values；
业务拒绝一律返回对应的 HTTP 错误码（400/404/409），不再包装成 200 成功。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.share import ShareService

router = APIRouter(prefix="/api/share", tags=["数据分享"])

service = ShareService()

LIST_FIELDS = ["链接编号", "目标数据集", "访问密码", "有效期天数", "最大浏览次数", "下载开关", "水印内容", "过期自动清理"]
STATUSES = ["草稿", "待生成", "已生效", "已失效"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按链接编号检索"),
    status: str | None = Query(default=None, description="草稿、待生成、已生效、已失效"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按链接编号与状态过滤数据分享列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据分享清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "share", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分享链接明细，附带授权轨迹；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分享链接 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分享链接，缺字段或编号重复时返回 400 并说明原因。"""
    entry, error = service.create_entry(payload.values)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return ActionResult(ok=True, message="分享链接已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条分享链接执行提交生成、生效分享、撤销链接。

    生命周期或目标准入不满足时返回 409 等业务错误，不再用 200 掩盖拒绝。
    """
    entry, message, status_code = service.run_action(entry_id, payload.action, payload.values)
    if entry is None:
        raise HTTPException(status_code=status_code, detail=message)
    return ActionResult(ok=True, message=message, entry=entry)
