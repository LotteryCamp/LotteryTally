"""类别业务规则与管理页面，所有落盘修改通过统一存储接口完成。"""

from uuid import uuid4

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)

from .models import CATEGORY_NAME_MAX_LENGTH
from . import storage


categories_bp = Blueprint("categories", __name__, template_folder="templates")


# 使用不可变定义，每次初始化都生成独立的类别字典。
_DEFAULT_CATEGORIES = (
    ("salary", "工资", "income"),
    ("scholarship", "奖学金", "income"),
    ("part_time", "兼职", "income"),
    ("other_income", "其他", "income"),
    ("food", "饮食", "expense"),
    ("traffic", "交通", "expense"),
    ("study", "学习", "expense"),
    ("living", "生活", "expense"),
    ("entertainment", "娱乐", "expense"),
    ("medical", "医疗", "expense"),
    ("other_expense", "其他", "expense"),
)


class CategoryError(ValueError):
    """类别操作失败，field 用于页面定位错误字段。"""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def get_default_categories():
    """返回独立的默认类别列表，供首次初始化账本使用。"""
    return [
        {
            "id": category_id,
            "name": name,
            "type": entry_type,
            "active": True,
        }
        for category_id, name, entry_type in _DEFAULT_CATEGORIES
    ]


def _validate_type(entry_type):
    if entry_type not in ("income", "expense"):
        raise CategoryError("type", "收支类型只能是 income 或 expense")


def _normalize_name(name):
    if not isinstance(name, str) or not name.strip():
        raise CategoryError("name", "类别名称不能为空")
    name = name.strip()
    if len(name) > CATEGORY_NAME_MAX_LENGTH:
        raise CategoryError("name", f"类别名称最多 {CATEGORY_NAME_MAX_LENGTH} 个字符")
    return name


def _find_category(ledger, category_id):
    for category in ledger["categories"]:
        if category["id"] == category_id:
            return category
    raise CategoryError("category_id", "类别不存在")


def _check_unique_name(ledger, name, entry_type, exclude_id=None):
    # 停用类别也参与重名检查，避免恢复时出现同名选项。
    for category in ledger["categories"]:
        if (
            category["id"] != exclude_id
            and category["type"] == entry_type
            and category["name"].strip() == name
        ):
            raise CategoryError("name", "同一收支类型下的类别名称不能重复")


def list_categories(ledger, entry_type=None, active_only=False):
    """按类型和启用状态查询类别，保留账本中的原始顺序。

    默认包含停用类别，供历史流水和统计查询使用。
    新增账目的类别选项应传入 active_only=True。
    返回类别字典的副本，避免界面处理意外修改账本。
    """
    if entry_type is not None:
        _validate_type(entry_type)

    return [
        category.copy()
        for category in ledger["categories"]
        if (entry_type is None or category["type"] == entry_type)
        and (not active_only or category["active"])
    ]


def create_category(ledger, name, entry_type):
    """在内存账本中创建启用类别，返回新类别的副本。"""
    _validate_type(entry_type)
    name = _normalize_name(name)
    _check_unique_name(ledger, name, entry_type)

    category = {
        "id": str(uuid4()),
        "name": name,
        "type": entry_type,
        "active": True,
    }
    ledger["categories"].append(category)
    return category.copy()


def rename_category(ledger, category_id, name):
    """修改类别名称，保持 ID、类型和启用状态不变。"""
    category = _find_category(ledger, category_id)
    name = _normalize_name(name)
    _check_unique_name(
        ledger, name, category["type"], exclude_id=category_id
    )

    category["name"] = name
    return category.copy()


def set_category_active(ledger, category_id, active):
    """停用或恢复类别，保留历史账目和预算中的类别引用。"""
    if not isinstance(active, bool):
        raise CategoryError("active", "启用状态必须是布尔值")

    category = _find_category(ledger, category_id)
    category["active"] = active
    return category.copy()


def _data_file():
    return current_app.config.get("LEDGER_DATA_PATH")


def _render_categories(errors=None, form=None, editing_id=None):
    ledger = storage.load_ledger(data_file=_data_file())
    # 公共布局需要页面传入导航，只展示当前应用实际注册的入口。
    paths = {rule.rule for rule in current_app.url_map.iter_rules()}
    navigation = [
        {"label": label, "path": path}
        for label, path in (
            ("首页", "/"), ("记一笔", "/entries/new"),
            ("流水", "/entries"), ("统计", "/stats"),
            ("预算", "/budget"), ("类别", "/categories"),
            ("备份", "/backup"),
        ) if path == "/" or path in paths
    ]
    return render_template(
        "categories.html",
        categories=list_categories(ledger),
        errors=errors or {},
        form=form or {},
        editing_id=editing_id,
        name_max_length=CATEGORY_NAME_MAX_LENGTH,
        navigation=navigation,
    )


@categories_bp.route("/categories", methods=["GET", "POST"])
def categories_page():
    if request.method == "GET":
        return _render_categories()

    try:
        storage.update_ledger(
            lambda ledger: create_category(
                ledger, request.form.get("name"), request.form.get("type")
            ),
            data_file=_data_file(),
        )
    except CategoryError as error:
        return _render_categories(
            errors={error.field: str(error)}, form=request.form
        ), 400
    except (storage.StorageError, OSError):
        return _render_storage_error()

    flash("类别已创建。", "success")
    return redirect(url_for("categories.categories_page"))


@categories_bp.post("/categories/<category_id>/update")
def update_category_page(category_id):
    action = request.form.get("action")

    def change(ledger):
        if action == "rename":
            rename_category(ledger, category_id, request.form.get("name"))
        elif action in ("disable", "enable"):
            set_category_active(ledger, category_id, action == "enable")
        else:
            raise CategoryError("action", "请选择有效的类别操作")

    try:
        storage.update_ledger(change, data_file=_data_file())
    except CategoryError as error:
        status = 404 if error.field == "category_id" else 400
        return _render_categories(
            errors={error.field: str(error)}, form=request.form,
            editing_id=category_id,
        ), status
    except (storage.StorageError, OSError):
        return _render_storage_error()

    flash({"rename": "类别已改名。", "disable": "类别已停用。",
           "enable": "类别已重新启用。"}[action], "success")
    return redirect(url_for("categories.categories_page"))


def _render_storage_error():
    # 不再次加载账本：读取损坏或写入失败时仍应能呈现错误页面。
    return render_template(
        "base.html", page_title="账本暂时无法保存",
        content_message="请检查账本文件和目录权限后重试。",
        navigation=[{"label": "首页", "path": "/"}],
    ), 503


@categories_bp.errorhandler(OSError)
@categories_bp.errorhandler(storage.StorageError)
def handle_storage_error(error):
    return render_template(
        "base.html", page_title="账本暂时无法读取",
        content_message="请检查账本文件，必要时使用有效备份恢复。",
        navigation=[{"label": "首页", "path": "/"}],
    ), 503
