"""PocketLedger 记账界面路由模块（陈常群模块）。

负责人：陈常群 102401318
对应《PocketLedger 现场编程与技术实施方案》第二节、第五节与第六节：
- GET、POST /entries/new        显示表单、创建账目，可带 copy_from 预填
- GET、POST /entries/<id>/edit  显示旧值、保存修改
- POST      /entries/<id>/delete 确认后删除

本模块是「记一笔」这条主流程的前端入口，只负责：
    收表单 → 交给账目服务（lotterytally.entries）校验并落库 → 成功跳转流水页，
    失败保留用户输入并在字段旁展示错误。

设计说明：
    entries（账目增删改服务）、validators（金额与字段校验）、categories（类别）、
    storage（JSON 读写）分属四位队友，现场并行开发期间可能尚未合入 main。
    因此本模块沿用团队统一约定——对依赖做防御式 import，未合入时使用等价后备实现，
    保证模块可独立运行、独立验收；依赖合入后自动切换到共享实现。

对外提供：
- entry_bp：Flask Blueprint（推荐 app.register_blueprint(entry_bp) 挂载）
- register_entry_routes(app)：幂等路由注册函数
- create_app(ledger_loader=None, ledger_saver=None, today=None)：独立自测用最小 Flask 应用工厂
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Callable, Mapping

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    redirect,
    render_template,
    request,
)
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

# ---------------------------------------------------------------------------
# 依赖的防御式导入：账目服务（佘博海）、字段校验（何慧渊）、类别（刘谦益）。
# 未合入 main 时置为 None，走本文件末尾的后备实现；合入后自动启用共享版本。
# ---------------------------------------------------------------------------
try:
    from .entries import create_entry as _shared_create_entry
    from .entries import delete_entry as _shared_delete_entry
    from .entries import update_entry as _shared_update_entry
except ModuleNotFoundError:
    _shared_create_entry = None
    _shared_update_entry = None
    _shared_delete_entry = None

try:
    from .validators import parse_amount_cents as _shared_parse_amount_cents
except ModuleNotFoundError:
    _shared_parse_amount_cents = None

try:
    from .categories import list_categories as _shared_list_categories
except ModuleNotFoundError:
    _shared_list_categories = None


__all__ = [
    "entry_bp",
    "register_entry_routes",
    "create_app",
]

# 默认类别：与方案第 5.2 节一致，仅在后备阶段使用；类别模块合入后走 list_categories。
DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "study", "name": "学习", "type": "expense", "active": True},
    {"id": "life", "name": "生活", "type": "expense", "active": True},
    {"id": "entertainment", "name": "娱乐", "type": "expense", "active": True},
    {"id": "medical", "name": "医疗", "type": "expense", "active": True},
    {"id": "other_expense", "name": "其他", "type": "expense", "active": True},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
    {"id": "scholarship", "name": "奖学金", "type": "income", "active": True},
    {"id": "parttime", "name": "兼职", "type": "income", "active": True},
    {"id": "other_income", "name": "其他", "type": "income", "active": True},
]

# 金额与日期规则（与方案 4.2 节、filters 模块保持一致）。
_AMOUNT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
VALID_TYPES = ("income", "expense")
NOTE_MAX_LENGTH = 200

entry_bp = Blueprint(
    "entry",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static/entry",
)


# ---------------------------------------------------------------------------
# 时间与金额的小工具
# ---------------------------------------------------------------------------
def _resolve_today() -> date:
    """当前账本基准日期：优先测试注入的 LEDGER_TODAY，其次本地真实日期。"""
    configured = current_app.config.get("LEDGER_TODAY")
    if isinstance(configured, date) and not isinstance(configured, datetime):
        return configured
    if isinstance(configured, str):
        return date.fromisoformat(configured)
    return datetime.now().astimezone().date()


def _now_iso() -> str:
    """带时区的录入/更新时间戳，满足 filters.sort_entries 的时区比较要求。"""
    configured = current_app.config.get("LEDGER_NOW")
    if isinstance(configured, str):
        return configured
    if isinstance(configured, datetime):
        if configured.tzinfo is None:
            configured = configured.replace(tzinfo=timezone.utc)
        return configured.isoformat()
    return datetime.now().astimezone().isoformat()


def _parse_amount_cents_local(text: Any) -> int:
    """后备金额转换：元文本 → 正整数分；非法输入抛 ValueError。

    规则与 filters 模块的后备实现完全一致：接受 45 / 45.5 / 45.50，
    拒绝 0、负数、超过两位小数、科学计数法、NaN、Infinity。
    """
    raw = "" if text is None else str(text)
    value = raw.strip()
    if not _AMOUNT_PATTERN.fullmatch(value):
        raise ValueError("金额须大于 0，最多两位小数，不接受科学计数法。")
    # 提高精度避免大金额被 Decimal 默认精度截断。
    with localcontext() as context:
        context.prec = max(28, len(value) + 2)
        cents = int(Decimal(value) * 100)
    if cents <= 0:
        raise ValueError("金额须大于 0。")
    return cents


def _format_cents(cents: int) -> str:
    """整数分转两位小数元字符串，全程整数运算，不经过 float。"""
    return "{}.{:02d}".format(cents // 100, cents % 100)


def _parse_amount(text: Any) -> int:
    if _shared_parse_amount_cents is not None:
        return _shared_parse_amount_cents(text)
    return _parse_amount_cents_local(text)


# ---------------------------------------------------------------------------
# 账本读取与持久化：适配队友 storage 模块，未合入时回退到文件/内存注入。
# ---------------------------------------------------------------------------
def _default_ledger() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "categories": [dict(c) for c in DEFAULT_CATEGORIES],
        "entries": [],
        "budgets": {},
    }


def _load_ledger() -> dict[str, Any]:
    """返回可读写的账本 dict。写入路径由 _persist 负责。"""
    loader: Callable[[], Mapping[str, Any]] | None = current_app.config.get(
        "LEDGER_LOADER"
    )
    if callable(loader):
        data = loader()
        return _coerce_ledger_dict(data)

    if "LEDGER_DATA" in current_app.config and isinstance(
        current_app.config["LEDGER_DATA"], Mapping
    ):
        return _coerce_ledger_dict(current_app.config["LEDGER_DATA"])

    data_file = current_app.config.get("DATA_FILE") or current_app.config.get("LEDGER_PATH")

    try:
        from . import storage  # type: ignore[attr-defined]

        load_fn = getattr(storage, "load_ledger", None)
        if callable(load_fn):
            try:
                loaded = load_fn(data_file) if data_file else load_fn()
            except TypeError:
                loaded = load_fn()
            return _coerce_ledger_dict(loaded)
    except ModuleNotFoundError:
        pass

    path = Path(data_file) if data_file else Path("data/ledger.json")
    if path.is_file():
        return _coerce_ledger_dict(json.loads(path.read_text(encoding="utf-8")))
    return _default_ledger()


def _coerce_ledger_dict(raw: Any) -> dict[str, Any]:
    """把 dict 或（未来的）模型对象统一成含 entries/categories 的字典。"""
    if isinstance(raw, Mapping):
        entries = raw.get("entries") or []
        categories = raw.get("categories") or []
        budgets = raw.get("budgets") or {}
    else:
        entries = getattr(raw, "entries", None) or []
        categories = getattr(raw, "categories", None) or []
        budgets = getattr(raw, "budgets", None) or {}

    return {
        "schema_version": 1,
        "categories": [dict(c) if isinstance(c, Mapping) else _obj_to_dict(c) for c in categories],
        "entries": [dict(e) if isinstance(e, Mapping) else _obj_to_dict(e) for e in entries],
        "budgets": dict(budgets),
    }


def _obj_to_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict") and callable(item.to_dict):
        return dict(item.to_dict())
    return {
        "id": getattr(item, "id", ""),
        "date": getattr(item, "date", ""),
        "type": getattr(item, "type", ""),
        "amount_cents": getattr(item, "amount_cents", 0),
        "category_id": getattr(item, "category_id", ""),
        "note": getattr(item, "note", ""),
        "created_at": getattr(item, "created_at", ""),
        "updated_at": getattr(item, "updated_at", ""),
    }


def _persist(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """在同一把锁内完成「读最新账本 → 应用修改 → 写回」。

    优先走 storage.update_ledger（它在写入锁内读最新账本、应用修改并保存），
    这是方案 6.1 节要求的正确做法，避免两个请求读旧账本、后写覆盖先写。
    """
    saver: Callable[[Mapping[str, Any]], None] | None = current_app.config.get("LEDGER_SAVER")
    if callable(saver):
        ledger = _load_ledger()
        change(ledger)
        saver(ledger)
        return ledger

    try:
        from . import storage  # type: ignore[attr-defined]

        update_fn = getattr(storage, "update_ledger", None)
        if callable(update_fn):
            result = update_fn(change)
            return _coerce_ledger_dict(result)
    except ModuleNotFoundError:
        pass

    # 后备：直接读文件、改内存、原子写回。
    ledger = _load_ledger()
    change(ledger)
    _write_ledger_file(ledger)
    return ledger


def _write_ledger_file(ledger: Mapping[str, Any]) -> None:
    data_file = current_app.config.get("DATA_FILE") or current_app.config.get("LEDGER_PATH")
    path = Path(data_file) if data_file else Path("data/ledger.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # os.replace 在 Windows 上原子替换，避免写一半损坏账本。
    import os

    os.replace(temp, path)


# ---------------------------------------------------------------------------
# 类别读取：优先 list_categories，未合入时退回默认类别。
# ---------------------------------------------------------------------------
def _list_categories(ledger: Mapping[str, Any], entry_type: str | None = None,
                     active_only: bool = False) -> list[dict[str, Any]]:
    if _shared_list_categories is not None:
        try:
            return list(_shared_list_categories(ledger, entry_type, active_only))
        except TypeError:
            return list(_shared_list_categories(ledger))
    categories = ledger.get("categories") or DEFAULT_CATEGORIES
    result = []
    for cat in categories:
        item = dict(cat) if isinstance(cat, Mapping) else _obj_to_dict(cat)
        if entry_type is not None and item.get("type") != entry_type:
            continue
        if active_only and item.get("active") is False:
            continue
        result.append(item)
    return result


def _find_category(categories: list[dict[str, Any]], category_id: str) -> dict[str, Any] | None:
    for cat in categories:
        if cat.get("id") == category_id:
            return cat
    return None


# ---------------------------------------------------------------------------
# 表单校验（后备）：返回 (errors, cleaned)，errors 为空表示通过。
# ---------------------------------------------------------------------------
def _validate_entry_form(form_data: Mapping[str, Any],
                         categories: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, Any]]:
    errors: dict[str, str] = {}

    date_text = str(form_data.get("date") or "").strip()
    if not date_text:
        errors["date"] = "请填写日期。"
    elif not _DATE_PATTERN.fullmatch(date_text):
        errors["date"] = "日期格式须为 YYYY-MM-DD。"
    else:
        try:
            date.fromisoformat(date_text)
        except ValueError:
            errors["date"] = "日期不存在，请填写真实日期。"

    entry_type = str(form_data.get("type") or "").strip()
    if entry_type not in VALID_TYPES:
        errors["type"] = "请选择收入或支出。"

    amount_text = str(form_data.get("amount") or "").strip()
    amount_cents: int | None = None
    if not amount_text:
        errors["amount"] = "请填写金额。"
    else:
        try:
            amount_cents = _parse_amount(amount_text)
        except ValueError as exc:
            errors["amount"] = str(exc)

    category_id = str(form_data.get("category_id") or "").strip()
    category = _find_category(categories, category_id) if category_id else None
    if not category_id:
        errors["category_id"] = "请选择类别。"
    elif category is None:
        errors["category_id"] = "所选类别不存在。"
    elif entry_type in VALID_TYPES and category.get("type") != entry_type:
        errors["category_id"] = "所选类别与收支类型不匹配。"

    note = str(form_data.get("note") or "").strip()
    if len(note) > NOTE_MAX_LENGTH:
        errors["note"] = "备注不能超过 {} 个字符。".format(NOTE_MAX_LENGTH)

    cleaned = {
        "date": date_text,
        "type": entry_type,
        "amount_cents": amount_cents,
        "category_id": category_id,
        "note": note,
    }
    return errors, cleaned


# ---------------------------------------------------------------------------
# 账目服务（后备）：方案 5.2 节的 create_entry / update_entry / delete_entry。
# entries 模块合入后，路由自动改走共享实现。
# ---------------------------------------------------------------------------
def _make_entry(cleaned: Mapping[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "date": cleaned["date"],
        "type": cleaned["type"],
        "amount_cents": cleaned["amount_cents"],
        "category_id": cleaned["category_id"],
        "note": cleaned["note"],
        "created_at": now,
        "updated_at": now,
    }


def _create_entry_local(cleaned: Mapping[str, Any]) -> dict[str, Any]:
    entry = _make_entry(cleaned)

    def change(ledger: dict[str, Any]) -> None:
        ledger.setdefault("entries", []).append(entry)

    _persist(change)
    return entry


def _update_entry_local(entry_id: str, cleaned: Mapping[str, Any]) -> dict[str, Any]:
    updated: dict[str, Any] = {}

    def change(ledger: dict[str, Any]) -> None:
        nonlocal updated
        for existing in ledger.get("entries", []):
            if existing.get("id") == entry_id:
                existing["date"] = cleaned["date"]
                existing["type"] = cleaned["type"]
                existing["amount_cents"] = cleaned["amount_cents"]
                existing["category_id"] = cleaned["category_id"]
                existing["note"] = cleaned["note"]
                # 保留原 ID 与 created_at，仅刷新 updated_at。
                existing["updated_at"] = _now_iso()
                updated = existing
                return
        raise KeyError(entry_id)

    _persist(change)
    return updated


def _delete_entry_local(entry_id: str) -> None:
    def change(ledger: dict[str, Any]) -> None:
        entries = ledger.get("entries", [])
        remaining = [e for e in entries if e.get("id") != entry_id]
        if len(remaining) == len(entries):
            raise KeyError(entry_id)
        ledger["entries"] = remaining

    _persist(change)


# ---------------------------------------------------------------------------
# 表单回显：把用户输入/旧值整理成模板变量，保证报错时保留输入。
# ---------------------------------------------------------------------------
def _form_state(args: Mapping[str, Any], entry: Mapping[str, Any] | None,
                entry_type: str) -> dict[str, str]:
    if entry is not None:
        return {
            "date": str(entry.get("date") or ""),
            "type": str(entry.get("type") or entry_type),
            "amount": _format_cents(int(entry.get("amount_cents") or 0)),
            "category_id": str(entry.get("category_id") or ""),
            "note": str(entry.get("note") or ""),
        }
    return {
        "date": str(args.get("date") or ""),
        "type": str(args.get("type") or entry_type),
        "amount": str(args.get("amount") or ""),
        "category_id": str(args.get("category_id") or ""),
        "note": str(args.get("note") or ""),
    }


def _entry_context(entry: Mapping[str, Any], categories: list[dict[str, Any]]) -> dict[str, Any]:
    category = _find_category(categories, entry.get("category_id") or "")
    return {
        "id": entry.get("id"),
        "date": entry.get("date"),
        "type": entry.get("type"),
        "amount_yuan": _format_cents(int(entry.get("amount_cents") or 0)),
        "category_name": (category or {}).get("name") or entry.get("category_id"),
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@entry_bp.route("/entries/new", methods=["GET", "POST"])
def new_entry():
    """新增账目：GET 显示空表单（可带 copy_from 预填），POST 校验并落库。"""
    today = _resolve_today()
    ledger = _load_ledger()

    # copy_from 预填：从既有账目复制，日期默认今天（方案 P2 快速记账）。
    source: dict[str, Any] | None = None
    copy_id = str(request.args.get("copy_from") or "").strip()
    if copy_id:
        for existing in ledger.get("entries", []):
            if existing.get("id") == copy_id:
                source = existing
                break

    entry_type = "expense"
    if source is not None:
        entry_type = str(source.get("type") or "expense")
    elif request.method == "POST":
        entry_type = str(request.form.get("type") or "expense")

    all_categories = _list_categories(ledger)
    form = _form_state(
        request.form if request.method == "POST" else request.args,
        None, entry_type,
    )

    # 快速复制：回显来源账目的金额/类别/备注，日期用今天。
    if request.method == "GET" and source is not None:
        form = {
            "date": today.isoformat(),
            "type": entry_type,
            "amount": _format_cents(int(source.get("amount_cents") or 0)),
            "category_id": str(source.get("category_id") or ""),
            "note": str(source.get("note") or ""),
        }

    if request.method == "POST":
        errors, cleaned = _validate_entry_form(request.form, all_categories)
        if not errors:
            entry = (_shared_create_entry(cleaned) if _shared_create_entry
                     else _create_entry_local(cleaned))
            flash("已记下这笔账。", "success")
            return redirect("/entries")

        active_categories = _list_categories(ledger, entry_type, active_only=True)
        return render_template(
            "entry_form.html",
            mode="new",
            title="记一笔",
            form=form,
            errors=errors,
            categories=all_categories,
            active_categories=active_categories,
            today=today.isoformat(),
            entry=None,
        ), 400

    active_categories = _list_categories(ledger, entry_type, active_only=True)
    return render_template(
        "entry_form.html",
        mode="new",
        title="记一笔",
        form=form,
        errors={},
        categories=all_categories,
        active_categories=active_categories,
        today=today.isoformat(),
        entry=None,
    )


@entry_bp.route("/entries/<entry_id>/edit", methods=["GET", "POST"])
def edit_entry(entry_id: str):
    """编辑账目：GET 显示旧值，POST 校验并保存修改。"""
    today = _resolve_today()
    ledger = _load_ledger()

    existing: dict[str, Any] | None = None
    for item in ledger.get("entries", []):
        if item.get("id") == entry_id:
            existing = item
            break
    if existing is None:
        return render_template(
            "entry_form.html", mode="edit", title="编辑账目",
            form={}, errors={}, categories=[], active_categories=[],
            today=today.isoformat(), entry=None, not_found=True,
        ), 404

    entry_type = str(existing.get("type") or "expense")
    all_categories = _list_categories(ledger)
    form = _form_state(request.form if request.method == "POST" else {}, existing, entry_type)

    if request.method == "POST":
        errors, cleaned = _validate_entry_form(request.form, all_categories)
        if not errors:
            if _shared_update_entry is not None:
                _shared_update_entry(entry_id, cleaned)
            else:
                _update_entry_local(entry_id, cleaned)
            flash("修改已保存。", "success")
            return redirect("/entries")

        active_categories = _list_categories(ledger, entry_type, active_only=True)
        return render_template(
            "entry_form.html",
            mode="edit",
            title="编辑账目",
            form=form,
            errors=errors,
            categories=all_categories,
            active_categories=active_categories,
            today=today.isoformat(),
            entry=_entry_context(existing, all_categories),
        ), 400

    active_categories = _list_categories(ledger, entry_type, active_only=True)
    return render_template(
        "entry_form.html",
        mode="edit",
        title="编辑账目",
        form=form,
        errors={},
        categories=all_categories,
        active_categories=active_categories,
        today=today.isoformat(),
        entry=_entry_context(existing, all_categories),
    )


@entry_bp.route("/entries/<entry_id>/delete", methods=["POST"])
def delete_entry(entry_id: str):
    """删除账目：POST 确认后删除，返回流水页。"""
    if _shared_delete_entry is not None:
        _shared_delete_entry(entry_id)
    else:
        _delete_entry_local(entry_id)
    flash("已删除这笔账。", "success")
    return redirect("/entries")


# ---------------------------------------------------------------------------
# 模板加载兜底：兼容仓库根 templates/ 与包内 lotterytally/templates/，
# 并提供 base.html 兜底，保证模块可独立渲染（方案目录建议存在两种布局）。
# ---------------------------------------------------------------------------
_FALLBACK_BASE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}PocketLedger 口袋账本{% endblock %}</title>
  <style>
    body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1e293b;background:#f1f5f9;}
    nav{background:#1e293b;color:#fff;padding:12px 24px;display:flex;gap:20px;align-items:center;flex-wrap:wrap;}
    nav strong{font-size:18px;}
    nav a{color:#e2e8f0;text-decoration:none;}
    nav a.active{color:#38bdf8;font-weight:bold;}
    main{max-width:860px;margin:24px auto;padding:0 16px;}
    .flash{background:#dcfce7;border:1px solid #86efac;color:#166534;padding:10px 14px;border-radius:8px;margin-bottom:16px;}
  </style>
</head>
<body>
  <nav>
    <strong>PocketLedger 口袋账本</strong>
    <a href="/">首页</a>
    <a href="/entries/new" class="active">记一笔</a>
    <a href="/entries">流水查询</a>
    <a href="/stats">收支统计</a>
    <a href="/budget">预算管理</a>
    <a href="/categories">类别管理</a>
    <a href="/backup">备份恢复</a>
  </nav>
  <main>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}<div class="flash">{% for category, message in messages %}{{ message }}{% endfor %}</div>{% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
  </main>
</body>
</html>
"""


def _ensure_fallback_base_template(app: Flask) -> None:
    if getattr(app, "_pocketledger_entry_loader_configured", False):
        return
    repo_root_templates = Path(__file__).resolve().parent.parent / "templates"
    pkg_templates = Path(__file__).resolve().parent / "templates"
    loaders: list[Any] = []
    if app.jinja_loader is not None:
        loaders.append(app.jinja_loader)
    loaders.append(FileSystemLoader([str(repo_root_templates), str(pkg_templates)]))
    loaders.append(DictLoader({"base.html": _FALLBACK_BASE_HTML}))
    app.jinja_loader = ChoiceLoader(loaders)
    setattr(app, "_pocketledger_entry_loader_configured", True)


@entry_bp.record_once
def _on_blueprint_registered(state: Any) -> None:
    _ensure_fallback_base_template(state.app)


def register_entry_routes(app: Flask) -> None:
    """将记账相关路由幂等注册到 Flask 应用实例。"""
    _ensure_fallback_base_template(app)
    if "entry" not in app.blueprints:
        app.register_blueprint(entry_bp)


def create_app(
    ledger_loader: Callable[[], Mapping[str, Any]] | None = None,
    ledger_saver: Callable[[Mapping[str, Any]], None] | None = None,
    today: date | str | None = None,
    now: str | None = None,
) -> Flask:
    """创建可独立运行的最小 Flask 应用，供记账模块自测与集成验证。

    静态资源由 entry_bp 的 blueprint 静态目录（lotterytally/static）自行挂载，
    不依赖应用层 static_folder 的配置。
    """
    app = Flask(__name__, template_folder="templates")
    app.secret_key = "pocketledger-entry-self-test"
    if ledger_loader is not None:
        app.config["LEDGER_LOADER"] = ledger_loader
    if ledger_saver is not None:
        app.config["LEDGER_SAVER"] = ledger_saver
    if today is not None:
        app.config["LEDGER_TODAY"] = today
    if now is not None:
        app.config["LEDGER_NOW"] = now
    register_entry_routes(app)
    return app
