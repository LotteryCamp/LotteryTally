"""账目服务：新增、修改、删除与按 ID 查询。

负责人：佘博海 102401137
交付文件：lotterytally/entries.py

依赖约定
--------
- 字段格式（日期、金额、备注长度）交给 ``lotterytally.validators.validate_entry_fields()``；
  该模块尚未合入时，单元测试用 ``test/support.py`` 里的契约替身，不进生产代码。
- 落盘交给 ``lotterytally.storage.update_ledger()``：在同一把写锁内读取最新账本、
  执行 ``change(ledger)``、校验后保存。本模块不直接读写 JSON，也不依赖它的返回值，
  只要求它执行 change 并把修改保存下来。
- 组合筛选与排序在 ``filters.py``，流水分页在 ``history.py``；这里的
  ``list_entries`` / ``recent_entries`` 只给首页、快速复制等场景做薄封装。

错误约定：失败抛 ``EntryError`` 子类，``errors`` 是 ``{字段名: 中文提示}``，
页面可直接把提示放到对应输入框旁边。
"""

from __future__ import annotations

from lotterytally import models

__all__ = [
    "EntryError",
    "EntryValidationError",
    "EntryNotFound",
    "create_entry",
    "update_entry",
    "delete_entry",
    "get_entry",
    "list_entries",
    "recent_entries",
]

# 队友模块延迟导入：缺失时应用仍能启动，真正调用账目服务时才报错。


def _storage():
    from lotterytally import storage

    return storage


def _validators():
    from lotterytally import validators

    return validators


class EntryError(Exception):
    """账目业务错误基类。"""

    def __init__(self, errors):
        self.errors = dict(errors)
        super().__init__("；".join(self.errors.values()))


class EntryValidationError(EntryError):
    """字段或业务规则未通过，可以回填表单。"""


class EntryNotFound(EntryError):
    """账目 ID 不存在：可能已被删除，或链接来自旧页面。"""

    def __init__(self, entry_id):
        self.entry_id = entry_id
        super().__init__({"form": "没有找到这条账目，它可能已经被删除。"})


# ---------------------------------------------------------------- 写操作


def create_entry(form_data):
    """新增一笔账目，返回保存后的账目 dict。"""
    fields = _validated_fields(form_data)
    timestamp = models.now_iso()
    entry = {
        "id": models.new_entry_id(),
        "date": fields["date"],
        "type": fields["type"],
        "amount_cents": fields["amount_cents"],
        "category_id": fields["category_id"],
        "note": fields["note"],
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    def change(ledger):
        # 在写入锁内基于最新账本判断类别，避免刚停用的类别被并发写入
        _check_category(ledger, fields["type"], fields["category_id"], allow_inactive=False)
        ledger.setdefault("entries", []).append(dict(entry))

    _storage().update_ledger(change)
    return dict(entry)


def update_entry(entry_id, form_data):
    """修改账目：保留原 ID 与 created_at，只刷新 updated_at 和内容字段。"""
    fields = _validated_fields(form_data)
    snapshot = {}

    def change(ledger):
        entry = models.find_entry(ledger, entry_id)
        if entry is None:
            raise EntryNotFound(entry_id)
        # 编辑历史账目时允许保留它原来的停用类别；换成其他类别则必须是启用状态
        keep_current_category = entry.get("category_id") == fields["category_id"]
        _check_category(
            ledger, fields["type"], fields["category_id"],
            allow_inactive=keep_current_category,
        )

        entry["date"] = fields["date"]
        entry["type"] = fields["type"]
        entry["amount_cents"] = fields["amount_cents"]
        entry["category_id"] = fields["category_id"]
        entry["note"] = fields["note"]
        entry["updated_at"] = models.now_iso()

        snapshot.clear()
        snapshot.update(entry)

    _storage().update_ledger(change)
    return dict(snapshot)


def delete_entry(entry_id):
    """删除账目，返回被删除的那条记录（便于页面提示删掉了什么）。"""
    removed = {}

    def change(ledger):
        entry = models.find_entry(ledger, entry_id)
        if entry is None:
            raise EntryNotFound(entry_id)
        removed.update(entry)
        ledger["entries"] = [
            item for item in ledger.get("entries") or []
            if item.get("id") != entry_id
        ]

    _storage().update_ledger(change)
    return dict(removed)


# ---------------------------------------------------------------- 读操作


def get_entry(entry_id, ledger=None):
    """按 ID 取账目；不存在返回 None。"""
    ledger = _storage().load_ledger() if ledger is None else ledger
    return models.find_entry(ledger, entry_id)


def list_entries(ledger=None, newest_first=True):
    """全部账目（默认按最近记录口径倒序）。"""
    ledger = _storage().load_ledger() if ledger is None else ledger
    entries = [entry for entry in ledger.get("entries") or [] if isinstance(entry, dict)]
    return models.sort_entries_newest_first(entries) if newest_first else entries


def recent_entries(limit=10, ledger=None):
    """首页最近 N 笔：日期倒序 → 录入时间倒序 → ID 倒序。"""
    return list_entries(ledger=ledger)[: max(int(limit), 0)]


# ---------------------------------------------------------------- 内部工具


def _validated_fields(form_data):
    validators = _validators()
    try:
        return validators.validate_entry_fields(form_data)
    except validators.ValidationError as exc:
        raise EntryValidationError(exc.errors) from exc


def _check_category(ledger, entry_type, category_id, allow_inactive):
    category = models.find_category(ledger, category_id)
    if category is None:
        raise EntryValidationError({"category_id": "该类别不存在，请重新选择"})

    if category.get("type") != entry_type:
        category_type = models.TYPE_LABELS.get(category.get("type"), category.get("type"))
        entry_type_label = models.TYPE_LABELS.get(entry_type, entry_type)
        raise EntryValidationError(
            {"category_id": f"类别「{category.get('name')}」属于{category_type}，与这笔{entry_type_label}不一致"}
        )

    if not category.get("active") and not allow_inactive:
        raise EntryValidationError(
            {"category_id": f"类别「{category.get('name')}」已停用，请选择其他类别"}
        )