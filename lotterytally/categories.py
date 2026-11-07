"""类别模块的独立业务逻辑。

不读写文件，不依赖 Flask。
修改函数操作调用方传入的账本字典，后续应在存储模块的
update_ledger(change) 回调中调用，统一完成校验和保存。
"""

from uuid import uuid4


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
    return name.strip()


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