"""PocketLedger 记账界面路由（陈常群模块）。

负责人：陈常群 102401318
对应《PocketLedger 现场编程与技术实施方案》第二节、第五节与第六节：
- GET、POST /entries/new        显示表单、创建账目，可带 copy_from 预填
- GET、POST /entries/<id>/edit  显示旧值、保存修改
- POST      /entries/<id>/delete 确认后删除

本模块是「记一笔」这条主流程的前端入口，只负责：
    收表单 → 交给账目服务（lotterytally.entries）校验并落库 → 成功跳转流水页，
    失败保留用户输入并在字段旁展示错误。

依赖约定（与实施方案 5.2 节一致）：
- 校验与落库交给 ``lotterytally.entries.create_entry / update_entry / delete_entry``，
  它们内部调用 ``validators.validate_entry_fields`` 与 ``storage.update_ledger``；
  失败抛 ``EntryValidationError``（errors 为 {字段: 中文提示}）或 ``EntryNotFound``。
- 类别交给 ``lotterytally.categories.list_categories``；账本读取交给 ``storage.load_ledger``。
- storage / validators 由队友模块提供，这里延迟导入：缺失时应用仍能启动，
  只有访问记账页时才会报错（配合 app.py 的“模块缺失时跳过”策略）。

对外提供：``entry_bp``（Blueprint），应用入口 app.py 通过它注册路由。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
)

from lotterytally import models
from lotterytally.categories import list_categories
from lotterytally.entries import (
    EntryNotFound,
    EntryValidationError,
    create_entry as _create_entry,
    delete_entry as _delete_entry,
    update_entry as _update_entry,
)

__all__ = ["entry_bp"]

entry_bp = Blueprint("entry", __name__, template_folder="templates")

# ---------------------------------------------------------------- 依赖


def _storage():
    from lotterytally import storage

    return storage


def _load_ledger():
    """读取账本快照。优先应用注入的 LEDGER_LOADER，否则走 storage.load_ledger()。"""
    loader = current_app.config.get("LEDGER_LOADER")
    if callable(loader):
        return loader()
    return _storage().load_ledger()


def _today_date() -> date:
    """当前账本基准日期：优先测试注入的 LEDGER_TODAY，其次本地真实日期。"""
    configured = current_app.config.get("LEDGER_TODAY")
    if isinstance(configured, date) and not isinstance(configured, datetime):
        return configured
    if isinstance(configured, str):
        return date.fromisoformat(configured)
    return datetime.now().astimezone().date()


def _safe_type(value: Any) -> str:
    """把表单/旧值里的收支类型归一化；非法值回退为支出，避免类别查询报错。"""
    value = str(value or "").strip()
    return value if value in models.ENTRY_TYPES else models.TYPE_EXPENSE


def _navigation():
    from lotterytally.navigation import navigation_items

    return navigation_items()


# ---------------------------------------------------------------- 表单回显


def _form_from_request(values: Any, entry_type: str) -> dict[str, str]:
    """从表单/查询参数整理出纯字符串字典，报错时保留用户输入。"""
    return {
        "date": str(values.get("date") or ""),
        "type": str(values.get("type") or entry_type),
        "amount": str(values.get("amount") or ""),
        "category_id": str(values.get("category_id") or ""),
        "note": str(values.get("note") or ""),
    }


def _form_from_source(source: dict[str, Any], today: date) -> dict[str, str]:
    """快速复制：金额 / 类别 / 备注来自来源账目，日期默认今天。"""
    return {
        "date": today.isoformat(),
        "type": _safe_type(source.get("type")),
        "amount": models.format_cents(source.get("amount_cents") or 0),
        "category_id": str(source.get("category_id") or ""),
        "note": str(source.get("note") or ""),
    }


def _form_from_entry(entry: dict[str, Any]) -> dict[str, str]:
    """编辑：用旧值回显，金额由分转回元。"""
    return {
        "date": str(entry.get("date") or ""),
        "type": _safe_type(entry.get("type")),
        "amount": models.format_cents(entry.get("amount_cents") or 0),
        "category_id": str(entry.get("category_id") or ""),
        "note": str(entry.get("note") or ""),
    }


def _entry_context(entry: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """编辑页删除表单与「已停用类别」回显需要的数据。"""
    return {
        "id": entry.get("id"),
        "date": entry.get("date"),
        "type": entry.get("type"),
        "amount_yuan": models.format_cents(entry.get("amount_cents") or 0),
        "category_name": models.category_name(ledger, entry.get("category_id") or ""),
    }


def _render(
    today: date,
    entry_type: str,
    ledger: dict[str, Any],
    *,
    mode: str,
    title: str,
    form: dict[str, str],
    errors: dict[str, str],
    entry: dict[str, Any] | None = None,
    not_found: bool = False,
):
    return render_template(
        "entry_form.html",
        mode=mode,
        title=title,
        form=form,
        errors=errors,
        categories=list_categories(ledger),
        active_categories=list_categories(ledger, entry_type, active_only=True),
        today=today.isoformat(),
        entry=entry,
        not_found=not_found,
        navigation=_navigation(),
    )


# ---------------------------------------------------------------- 路由


@entry_bp.route("/entries/new", methods=["GET", "POST"])
def new_entry():
    """新增账目：GET 显示空表单（可带 copy_from 预填），POST 校验并落库。"""
    today = _today_date()
    ledger = _load_ledger()

    copy_id = (request.args.get("copy_from") or "").strip()
    source = models.find_entry(ledger, copy_id) if copy_id else None

    if request.method == "POST":
        entry_type = _safe_type(request.form.get("type"))
        try:
            _create_entry(request.form)
        except EntryValidationError as exc:
            return _render(
                today, entry_type, ledger, mode="new", title="记一笔",
                form=_form_from_request(request.form, entry_type),
                errors=exc.errors,
            ), 400
        flash("已记下这笔账。", "success")
        return redirect("/entries")

    if source is not None:
        entry_type = _safe_type(source.get("type"))
        form = _form_from_source(source, today)
    else:
        entry_type = _safe_type(request.args.get("type"))
        form = _form_from_request(request.args, entry_type)
    return _render(
        today, entry_type, ledger, mode="new", title="记一笔", form=form, errors={}
    )


@entry_bp.route("/entries/<entry_id>/edit", methods=["GET", "POST"])
def edit_entry(entry_id: str):
    """编辑账目：GET 显示旧值，POST 校验并保存修改。"""
    today = _today_date()
    ledger = _load_ledger()
    existing = models.find_entry(ledger, entry_id)

    if request.method == "POST":
        entry_type = _safe_type(request.form.get("type"))
        try:
            _update_entry(entry_id, request.form)
        except EntryNotFound:
            return _render(
                today, entry_type, ledger, mode="edit", title="编辑账目",
                form=_form_from_request(request.form, entry_type),
                errors={}, not_found=True,
            ), 404
        except EntryValidationError as exc:
            return _render(
                today, entry_type, ledger, mode="edit", title="编辑账目",
                form=_form_from_request(request.form, entry_type),
                errors=exc.errors,
                entry=_entry_context(existing or {}, ledger),
            ), 400
        flash("修改已保存。", "success")
        return redirect("/entries")

    if existing is None:
        return _render(
            today, "expense", ledger, mode="edit", title="编辑账目",
            form={}, errors={}, not_found=True,
        ), 404

    entry_type = _safe_type(existing.get("type"))
    return _render(
        today, entry_type, ledger, mode="edit", title="编辑账目",
        form=_form_from_entry(existing), errors={},
        entry=_entry_context(existing, ledger),
    )


@entry_bp.route("/entries/<entry_id>/delete", methods=["POST"])
def delete_entry(entry_id: str):
    """删除账目：POST 确认后删除，返回流水页。"""
    try:
        _delete_entry(entry_id)
    except EntryNotFound:
        pass  # 已不存在，直接回到流水页即可。
    flash("已删除这笔账。", "success")
    return redirect("/entries")
