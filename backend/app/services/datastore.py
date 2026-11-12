"""数据集管理业务规则：状态流转、字段校验与筛选口径都收在这里。

数据集管理同时是数据分享的目标准入方：「废弃下线」与分享链接的「生效分享」
共用同一把裁决锁，并把仍存活的分享链接联动失效，保证生成与废弃并发时
落定同一次授权裁决。
"""
from __future__ import annotations

from typing import Any

from app.services import share as share_service
from app.store import store

MODULE = "datastore"
REQUIRED_FIELDS = ["数据集编号", "数据集名称", "来源表名"]
ENTRY_FIELDS = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "废弃下线": "已废弃"}
# 每个动作允许的来源状态：已废弃是终态，任何迟到请求都不能复活
TRANSITIONS: dict[str, set[str]] = {
    "提交审核": {"草稿"},
    "生效发布": {"待审核"},
    "废弃下线": {"草稿", "待审核", "已生效"},
}
TERMINAL_STATUS = "已废弃"


class DatastoreService:
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
            rows = [row for row in rows if keyword in str(row.get("数据集编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        # 关联入口：落在该数据集上的全部分享授权裁决，可追到每条链接的生效依据
        detail["关联分享授权"] = share_service.decisions_against_dataset(entry_id)
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        code = str(values.get("数据集编号")).strip()
        rows = store.rows(MODULE)
        if any(str(row.get("数据集编号", "")) == code for row in rows):
            return None, f"数据集编号「{code}」已存在，请更换编号"
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, int]:
        """执行动作；返回 (记录, 消息, HTTP 状态码)，业务拒绝不再包装成成功。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据集定义 {entry_id} 不存在或已归档", 404
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据集管理可执行范围", 400
        status = str(entry.get("status", ""))
        if status == TERMINAL_STATUS:
            return None, f"数据集已废弃，迟到的「{action}」不能使其复活", 409
        if status not in TRANSITIONS[action]:
            return None, f"当前状态「{status}」不允许执行「{action}」：生命周期为 草稿→待审核→已生效→已废弃", 409
        if action == "废弃下线":
            # 与分享链接「生效分享」共用同一把裁决锁：废弃与生成并发时落定同一次裁决
            with store.decision_lock:
                entry["status"] = TERMINAL_STATUS
                entry["pending"] = False
                entry["abnormal"] = True
                cascaded = share_service.cascade_dataset_deprecated(entry)
            note = f"，{cascaded} 条存活分享链接已联动失效" if cascaded else ""
            return entry, f"数据集定义已废弃下线{note}", 200
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target in {"草稿", "待审核"}
        entry["abnormal"] = False
        return entry, f"数据集定义已{action}", 200
