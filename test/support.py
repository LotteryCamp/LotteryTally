"""单元测试支撑：存储与校验模块的契约替身。

背景：本模块（models / entries / budget）依赖李义东的 ``lotterytally/storage.py``
和何慧渊的 ``lotterytally/validators.py``，两者尚未合入 main。为了让本人的模块
能按实施方案“独立验收”，这里按 5.2 节的接口约定提供内存替身：

- ``storage.load_ledger()`` / ``update_ledger(change)`` / ``write_ledger(ledger)``
- ``validators.parse_amount_cents(text)`` / ``validate_entry_fields(form)``

替身只用于测试，不进入 ``lotterytally`` 包；真实模块合入后，把这里替换成
真实导入即可（联调步骤见 docs/budget.md）。
"""

from __future__ import annotations

import copy
import re
import importlib.machinery
import secrets
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import lotterytally  # noqa: E402
from lotterytally import models  # noqa: E402

#: 与类别模块（刘谦益）的 default_categories() 保持一致的测试夹具。
#: 真实默认类别由 categories.py 提供，替换后本常量可以删除。
DEFAULT_CATEGORIES = [
    {"id": "salary", "name": "工资", "type": "income", "active": True},
    {"id": "scholarship", "name": "奖学金", "type": "income", "active": True},
    {"id": "parttime", "name": "兼职", "type": "income", "active": True},
    {"id": "other_income", "name": "其他", "type": "income", "active": True},
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "transport", "name": "交通", "type": "expense", "active": True},
    {"id": "study", "name": "学习", "type": "expense", "active": True},
    {"id": "life", "name": "生活", "type": "expense", "active": True},
    {"id": "entertainment", "name": "娱乐", "type": "expense", "active": True},
    {"id": "medical", "name": "医疗", "type": "expense", "active": True},
    {"id": "other_expense", "name": "其他", "type": "expense", "active": True},
]

#: 预算页渲染测试用的最小父模板；真实 base.html 由布局模块提供。
#: 保留 title/head/navigation/content/scripts 五个入口，与 app-shell 的 base.html 对齐。
BASE_TEMPLATE_STUB = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title>"
    "{% block head %}{% endblock %}</head><body>"
    '<nav>{% for item in navigation %}<a href="{{ item.path }}">{{ item.label }}</a>{% endfor %}</nav>'
    "{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>"
)


# ---------------------------------------------------------------- storage 替身


class _MemoryStorage:
    """按实施方案 5.2 的 storage 接口实现的内存版账本。"""

    def __init__(self):
        self._ledger = None

    def configure(self, path=None):  # 测试不落盘，保留接口以便切换真实模块
        self._ledger = None

    def data_file(self):
        return REPO_ROOT / "data" / "ledger.json"

    def load_ledger(self, path=None):
        if self._ledger is None:
            raise RuntimeError("测试替身里还没有账本，请先写入初始账本")
        return copy.deepcopy(self._ledger)

    def update_ledger(self, change, path=None):
        working = copy.deepcopy(self.load_ledger())
        result = change(working)
        models.assert_valid_ledger(working)
        self._ledger = working
        return result

    def write_ledger(self, ledger, path=None):
        models.assert_valid_ledger(ledger)
        self._ledger = copy.deepcopy(ledger)
        return ledger


def _storage_module():
    backend = _MemoryStorage()
    module = types.ModuleType("lotterytally.storage")
    module.configure = backend.configure
    module.data_file = backend.data_file
    module.load_ledger = backend.load_ledger
    module.update_ledger = backend.update_ledger
    module.write_ledger = backend.write_ledger
    return module


# ---------------------------------------------------------------- validators 替身


_AMOUNT_PATTERN = re.compile(r"^\d+(?:\.\d{1,2})?$")


class _ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = dict(errors)
        super().__init__("；".join(self.errors.values()))


def _parse_amount_cents(text, field="amount"):
    raw = "" if text is None else str(text).strip()
    if not raw:
        raise _ValidationError({field: "请填写金额"})
    if raw.startswith("-"):
        raise _ValidationError({field: "金额必须大于 0"})
    if not _AMOUNT_PATTERN.match(raw):
        raise _ValidationError({field: "金额格式不正确，请填写数字，最多两位小数（如 45 或 45.50）"})
    cents = int((Decimal(raw) * 100).to_integral_value())
    if cents <= 0:
        raise _ValidationError({field: "金额必须大于 0"})
    return cents


def _validate_entry_fields(form_data):
    errors = {}

    entry_type = (form_data.get("type") or "").strip()
    if entry_type not in models.ENTRY_TYPES:
        errors["type"] = "请选择收入或支出"

    amount_cents = None
    try:
        amount_cents = _parse_amount_cents(form_data.get("amount"))
    except _ValidationError as exc:
        errors.update(exc.errors)

    date_text = (form_data.get("date") or "").strip()
    if not date_text:
        errors["date"] = "请选择日期"
    elif not models.is_valid_date(date_text):
        errors["date"] = "日期必须是实际存在的日历日期，例如 2026-11-07"

    category_id = (form_data.get("category_id") or "").strip()
    if not category_id:
        errors["category_id"] = "请选择一个类别"

    note = (form_data.get("note") or "").strip()
    if len(note) > models.NOTE_MAX_LENGTH:
        errors["note"] = f"备注最多 {models.NOTE_MAX_LENGTH} 个字符"

    if errors:
        raise _ValidationError(errors)

    return {
        "date": date_text,
        "type": entry_type,
        "amount_cents": amount_cents,
        "category_id": category_id,
        "note": note,
    }


def _validators_module():
    module = types.ModuleType("lotterytally.validators")
    module.ValidationError = _ValidationError
    module.parse_amount_cents = _parse_amount_cents
    module.validate_entry_fields = _validate_entry_fields
    return module


def _install(name, module):
    """临时把替身装进 sys.modules，返回还原所需的信息。

    只在本人的用例执行期间生效：队友的用例（例如 app-shell 用
    ``importlib.util.find_spec("lotterytally.storage")`` 判断后端是否就绪）
    不能在测试进程里看到这些替身，否则会被误判成“存储模块已经合入”。
    """
    package_attr = name.rsplit(".", 1)[1]
    previous = (name, sys.modules.get(name), getattr(lotterytally, package_attr, None))
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    module.__loader__ = None
    module.__package__ = name.rsplit(".", 1)[0]
    sys.modules[name] = module
    setattr(lotterytally, package_attr, module)
    return previous


def _uninstall(previous):
    name, previous_module, previous_attr = previous
    if previous_module is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = previous_module

    package_attr = name.rsplit(".", 1)[1]
    if previous_attr is None:
        if hasattr(lotterytally, package_attr):
            delattr(lotterytally, package_attr)
    else:
        setattr(lotterytally, package_attr, previous_attr)


storage = _storage_module()
validators = _validators_module()


# ---------------------------------------------------------------- 测试基类


class LedgerTestCase(unittest.TestCase):
    """每个用例一份只含默认类别、没有任何账目的内存账本。"""

    def setUp(self):
        self._installed = [
            _install("lotterytally.storage", storage),
            _install("lotterytally.validators", validators),
        ]
        storage.write_ledger(models.new_ledger(DEFAULT_CATEGORIES))

    def tearDown(self):
        for previous in reversed(self._installed):
            _uninstall(previous)

    # ------------------------------------------------------------ 造数据

    def add_entry(
        self,
        entry_type="expense",
        amount="45",
        date="2025-03-05",
        category_id="food",
        note="",
    ):
        from lotterytally import entries as entry_service

        return entry_service.create_entry(
            {
                "type": entry_type,
                "amount": amount,
                "date": date,
                "category_id": category_id,
                "note": note,
            }
        )

    def set_budget(self, month, total_cents=None, category_limits=None):
        """金额单位是分：500 元写成 50000。"""
        from lotterytally import budget as budget_module

        return budget_module.save_budget(month, total_cents, category_limits or {})

    def load(self):
        return storage.load_ledger()

    def entries(self):
        return self.load()["entries"]

    def budgets(self):
        return self.load()["budgets"]

    def flask_client(self):
        """最小 Flask 应用：只注册 budget 蓝图，父模板用 stub。"""
        from flask import Flask
        from jinja2 import ChoiceLoader, DictLoader

        from lotterytally import budget as budget_module

        package_dir = REPO_ROOT / "lotterytally"
        app = Flask(
            __name__,
            template_folder=str(package_dir / "templates"),
            static_folder=str(package_dir / "static"),
        )
        # 每个测试应用实例动态生成签名密钥：flash/session 只在单实例内使用，
        # 不需要跨运行一致；也不用把密钥字面量提交进仓库
        app.secret_key = secrets.token_hex(32)
        app.register_blueprint(budget_module.budget_bp)
        app.jinja_loader = ChoiceLoader(
            [DictLoader({"base.html": BASE_TEMPLATE_STUB}), app.jinja_loader]
        )
        return app.test_client()