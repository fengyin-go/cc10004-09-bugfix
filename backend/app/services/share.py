"""数据分享业务规则：链接生命周期、目标准入与授权裁决都收在这里。

生命周期：草稿 → 待生成 → 已生效 → 已失效（终态）。
- 草稿必须先「提交生成」：此时把目标引用解析并绑定到具体数据集，绑定不上就
  保留未解析原因，等重新绑定后再提交；
- 待生成才允许「生效分享」：与数据集管理的「废弃下线」共用同一把裁决锁，
  废弃目标不得再生成活链接，生成与废弃并发时落定同一次授权裁决；
- 「撤销链接」后链接进入终态，迟到的动作请求不能让它复活；撤销重放是幂等
  空操作，不产生新的授权记录；
- 已生效链接按「有效期天数」计算授权到期时间，到期后在读取或动作时懒结算
  为已失效，并留下可追溯的裁决记录。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "share"
DATASET_MODULE = "datastore"
REQUIRED_FIELDS = ["链接编号", "目标数据集", "访问密码"]
ENTRY_FIELDS = ["链接编号", "目标数据集", "访问密码", "有效期天数", "最大浏览次数", "下载开关", "水印内容", "过期自动清理"]
STATUS_ORDER = ["草稿", "待生成", "已生效", "已失效"]
ACTION_RULES = {"提交生成": "待生成", "生效分享": "已生效", "撤销链接": "已失效"}
# 每个动作允许的来源状态：生命周期只能顺序流转，已失效是终态
TRANSITIONS: dict[str, set[str]] = {
    "提交生成": {"草稿"},
    "生效分享": {"待生成"},
    "撤销链接": {"草稿", "待生成", "已生效"},
}
TERMINAL_STATUS = "已失效"
DEFAULT_VALID_DAYS = 30


def _now() -> datetime:
    return datetime.now()


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_days(raw: Any) -> int:
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_VALID_DAYS
    return days if days > 0 else DEFAULT_VALID_DAYS


def resolve_dataset(target: Any) -> tuple[dict[str, Any] | None, str | None]:
    """把分享链接上的目标引用解析成数据集记录。

    优先按数据集编号或记录 id 精确匹配；存量链接只有名称时，唯一匹配可兼容；
    重名或查无此项时返回未解析原因，由调用方保留在链接上供重新绑定。
    """
    key = str(target or "").strip()
    if not key:
        return None, "目标数据集为空，需重新绑定"
    rows = store.rows(DATASET_MODULE)
    for row in rows:
        if key in {str(row.get("数据集编号", "")), str(row.get("id", ""))}:
            return row, None
    named = [row for row in rows if key == str(row.get("数据集名称", ""))]
    if len(named) == 1:
        return named[0], None
    if len(named) > 1:
        codes = "、".join(str(row.get("数据集编号", "?")) for row in named)
        return None, f"目标数据集名称「{key}」命中 {len(named)} 个同名数据集（{codes}），需改用数据集编号重新绑定"
    return None, f"目标数据集「{key}」在数据集管理中不存在，需重新绑定"


def _next_decision_code() -> str:
    seq = 0
    for record in store.share_decisions:
        code = str(record.get("授权编号", ""))
        if code.startswith("AUTH-") and code[5:].isdigit():
            seq = max(seq, int(code[5:]))
    return f"AUTH-{seq + 1:04d}"


def record_decision(
    entry: dict[str, Any],
    dataset: dict[str, Any] | None,
    action: str,
    result: str,
    note: str,
    expires: str | None = None,
) -> dict[str, Any]:
    """往授权裁决台账追加一条记录；调用方需持有 store.decision_lock。"""
    record = {
        "授权编号": _next_decision_code(),
        "链接id": entry.get("id"),
        "链接编号": entry.get("链接编号"),
        "数据集id": dataset.get("id") if dataset else entry.get("目标数据集id"),
        "数据集编号": dataset.get("数据集编号") if dataset else entry.get("目标数据集编号"),
        "数据集名称": dataset.get("数据集名称") if dataset else entry.get("目标数据集名称快照"),
        "裁决动作": action,
        "裁决结果": result,
        "裁决时间": _stamp(_now()),
        "授权到期时间": expires,
        "说明": note,
    }
    store.share_decisions.append(record)
    return record


def decisions_for_link(entry_id: int) -> list[dict[str, Any]]:
    """分享详情入口：按链接追溯全部授权裁决。"""
    return [dict(record) for record in store.share_decisions if record.get("链接id") == entry_id]


def decisions_against_dataset(dataset_id: int) -> list[dict[str, Any]]:
    """数据集详情入口：按数据集追溯落在它身上的全部授权裁决。"""
    return [dict(record) for record in store.share_decisions if record.get("数据集id") == dataset_id]


def cascade_dataset_deprecated(dataset: dict[str, Any]) -> int:
    """数据集废弃下线时，把它名下仍存活的分享链接联动失效。

    调用方必须持有 store.decision_lock：与「生效分享」落定同一次授权裁决，
    不会出现废弃检查与链接生效互相穿越的窗口。
    """
    affected = 0
    for entry in store.rows(MODULE):
        if entry.get("status") in {"待生成", "已生效"} and entry.get("目标数据集id") == dataset.get("id"):
            entry["status"] = TERMINAL_STATUS
            entry["pending"] = False
            entry["abnormal"] = True
            entry["失效原因"] = "目标数据集已废弃"
            record_decision(
                entry,
                dataset,
                "目标废弃联动",
                TERMINAL_STATUS,
                f"目标数据集 {dataset.get('数据集编号')} 废弃下线，存活链接联动失效",
            )
            affected += 1
    return affected


class ShareService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for row in rows:
            self._settle_expired(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("链接编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        self._settle_expired(entry)
        detail = dict(entry)
        detail["授权轨迹"] = decisions_for_link(entry_id)
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        code = str(values.get("链接编号")).strip()
        rows = store.rows(MODULE)
        if any(str(row.get("链接编号", "")) == code for row in rows):
            return None, f"链接编号「{code}」已存在，请更换编号"
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, None

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, int]:
        """执行动作；返回 (记录, 消息, HTTP 状态码)，业务拒绝不再包装成成功。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分享链接 {entry_id} 不存在或已归档", 404
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据分享可执行范围", 400
        self._settle_expired(entry)
        status = str(entry.get("status", ""))
        if status == TERMINAL_STATUS:
            if action == "撤销链接":
                # 撤销重放：幂等空操作，不产生新的授权记录
                return entry, "链接已处于已失效状态，本次撤销按重放处理，未产生新的授权变更", 200
            reason = entry.get("失效原因") or "已撤销"
            return None, f"链接已失效（{reason}），迟到的「{action}」不能使其复活", 409
        if status not in TRANSITIONS[action]:
            return None, f"当前状态「{status}」不允许执行「{action}」：链接生命周期为 草稿→待生成→已生效→已失效", 409
        if action == "提交生成":
            return self._submit(entry, values or {})
        if action == "生效分享":
            return self._activate(entry)
        return self._revoke(entry)

    def _submit(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
        """提交生成：解析并绑定目标数据集，绑定不上就保留未解析原因。"""
        new_target = str(values.get("目标数据集") or "").strip()
        if new_target:
            # 重新绑定：提交时允许携带新的目标引用覆盖旧值
            entry["目标数据集"] = new_target
        dataset, reason = resolve_dataset(entry.get("目标数据集"))
        if dataset is None:
            entry["未解析原因"] = reason
            entry["abnormal"] = True
            return None, f"目标准入未通过：{reason}", 409
        if dataset.get("status") == "已废弃":
            return None, (
                f"目标准入未通过：数据集 {dataset.get('数据集编号')}（{dataset.get('数据集名称')}）已废弃，"
                "废弃目标不得再生成活链接"
            ), 409
        entry["目标数据集id"] = dataset.get("id")
        entry["目标数据集编号"] = dataset.get("数据集编号")
        entry["目标数据集名称快照"] = dataset.get("数据集名称")
        entry.pop("未解析原因", None)
        entry["status"] = "待生成"
        entry["pending"] = True
        entry["abnormal"] = False
        return entry, f"分享链接已提交生成，目标数据集绑定为 {dataset.get('数据集编号')}（{dataset.get('数据集名称')}）", 200

    def _activate(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
        """生效分享：在裁决锁内完成目标状态核验与授权落账。"""
        # 与数据集「废弃下线」共用同一把锁：生成与废弃并发时落定同一次授权裁决
        with store.decision_lock:
            dataset = store.find(DATASET_MODULE, int(entry.get("目标数据集id") or 0))
            if dataset is None:
                return None, "目标准入未通过：绑定的数据集已不存在，请撤销后重新登记", 409
            dataset_status = str(dataset.get("status", ""))
            if dataset_status == "已废弃":
                return None, f"目标准入未通过：数据集 {dataset.get('数据集编号')} 已废弃，废弃目标不得再生成活链接", 409
            if dataset_status != "已生效":
                return None, f"目标准入未通过：数据集 {dataset.get('数据集编号')} 当前为「{dataset_status}」，未生效的数据集不能生成活链接", 409
            days = _parse_days(entry.get("有效期天数"))
            expires = _stamp(_now() + timedelta(days=days))
            entry["status"] = "已生效"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["授权到期时间"] = expires
            record = record_decision(
                entry,
                dataset,
                "生效分享",
                "已生效",
                f"目标数据集 {dataset.get('数据集编号')} 处于已生效，授予 {days} 天访问授权",
                expires,
            )
            return entry, f"分享链接已生效，授权编号 {record['授权编号']}，有效期 {days} 天（至 {expires}）", 200

    def _revoke(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
        """撤销链接：进入终态并留下裁决记录；重放由 run_action 幂等拦截。"""
        with store.decision_lock:
            entry["status"] = TERMINAL_STATUS
            entry["pending"] = False
            entry["abnormal"] = True
            entry["失效原因"] = "手动撤销"
            record = record_decision(entry, None, "撤销链接", TERMINAL_STATUS, "手动撤销，授权即刻终止")
            return entry, f"分享链接已撤销，授权终止（{record['授权编号']}）", 200

    def _settle_expired(self, entry: dict[str, Any]) -> None:
        """授权过期的懒结算：读取或动作时发现已到期，就落定失效并留痕。"""
        if str(entry.get("status")) != "已生效":
            return
        raw = entry.get("授权到期时间")
        if not raw:
            return
        try:
            expires = datetime.fromisoformat(str(raw))
        except ValueError:
            return
        if expires > _now():
            return
        with store.decision_lock:
            if str(entry.get("status")) != "已生效":
                return
            entry["status"] = TERMINAL_STATUS
            entry["pending"] = False
            entry["abnormal"] = True
            entry["失效原因"] = "授权过期"
            record_decision(entry, None, "授权过期", TERMINAL_STATUS, f"授权于 {raw} 到期，自动失效")
