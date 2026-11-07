"""预算规则与预算页（/budget，GET + POST）。

负责人：佘博海 102401137
交付文件：lotterytally/budget.py、lotterytally/templates/budget.html、
         lotterytally/static/css/budget.css

口径（实施方案 4.3 节）
----------------------
1. 账本里的 ``budgets`` 只存“上限”，已用金额每次都从账目按【账目日期所属月份】实时汇总。
   因此新增、编辑、删除账目后，预算页、进度条和超支提示自动跟着变，不需要同步代码。
2. 收入不进入支出预算；分类预算与总预算各自独立计算，不要求加到总预算。
3. 状态全部按整数分比较：已用 < 上限 → 剩余；已用 == 上限 → 额度已用完；
   已用 > 上限 → 超支。进度条宽度封顶 100%，但旁边金额和百分比照实显示。

对外接口：``budget_status(ledger, month)``（实施方案 5.2 节锁定）。
蓝图：``budget_bp``（应用入口 ``app.py`` 通过它注册路由）。
页面路由：GET /budget 查看，POST /budget 保存选定月份的总预算与分类预算。

依赖 ``lotterytally.storage`` 与 ``lotterytally.validators``，两者由队友模块提供。
为配合应用入口“模块缺失时跳过”的策略，这里在函数内部延迟导入：
队友模块尚未合入时，应用仍能启动，只有访问预算页时才会报错。
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from lotterytally import models

__all__ = [
    "budget_bp",
    "bp",
    "BudgetError",
    "budget_status",
    "parse_budget_form",
    "save_budget",
]

#: 应用入口 app.py 按 ``budget_bp`` 这个名字注册蓝图；bp 保留为同义别名。
budget_bp = Blueprint("budget", __name__, template_folder="templates")
bp = budget_bp

STATUS_UNSET = "unset"
STATUS_OK = "ok"
STATUS_EXHAUSTED = "exhausted"
STATUS_OVER = "over"

TOTAL_FIELD = "total"
LIMIT_FIELD_PREFIX = "limit_"


class BudgetError(Exception):
    """预算表单错误。errors 为 {字段名: 提示}，字段名与页面输入框 name 一致。"""

    def __init__(self, errors):
        self.errors = dict(errors)
        super().__init__("；".join(self.errors.values()))


# ---------------------------------------------------------------- 依赖

# 队友模块延迟导入：缺失时应用仍能启动，访问预算页才报错（配合 app.py 的跳过策略）。


def _storage():
    from lotterytally import storage

    return storage


def _validators():
    from lotterytally import validators

    return validators


def _load_ledger():
    """读取账本快照。

    优先使用应用注入的 ``LEDGER_LOADER``（与统计模块 statistics 使用同一个配置键），
    否则调用 storage 模块在实施方案 5.2 中锁定的 ``load_ledger()``。
    """
    loader = current_app.config.get("LEDGER_LOADER")
    if callable(loader):
        return loader()
    return _storage().load_ledger()


# ---------------------------------------------------------------- 计算


def budget_status(ledger, month):
    """返回某月的总预算与分类预算状态（全部为整数分）。

    结构::

        {
          "month": "2026-11", "month_label": "2026 年 11 月",
          "total": {has_limit, limit_cents, used_cents, remaining_cents,
                    overspent_cents, status, status_text, percent, bar_percent},
          "categories": [ {category_id, name, active, ...同上...}, ... ],
          "expense_cents": 51500, "expense_count": 4,
          "budgeted_count": 2, "unbudgeted_count": 3, "unbudgeted_cents": 1200,
        }
    """
    month = models.parse_month(month) or models.current_month()

    config = (ledger.get("budgets") or {}).get(month) or {}
    total_limit = config.get("total_cents") if isinstance(config, dict) else None
    limits = (config.get("category_limits") or {}) if isinstance(config, dict) else {}

    used_total = 0
    used_by_category = {}
    expense_count = 0
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("type") != models.TYPE_EXPENSE:
            continue
        if models.entry_month(entry) != month:
            continue
        amount = entry.get("amount_cents")
        if not isinstance(amount, int) or isinstance(amount, bool):
            continue
        expense_count += 1
        used_total += amount
        category_id = entry.get("category_id")
        used_by_category[category_id] = used_by_category.get(category_id, 0) + amount

    categories = []
    budgeted_count = 0
    unbudgeted_count = 0
    unbudgeted_cents = 0
    for category_id, category in models.category_index(ledger).items():
        if category.get("type") != models.TYPE_EXPENSE:
            continue
        limit = limits.get(category_id)
        used = used_by_category.get(category_id, 0)
        line = _budget_line(limit, used)
        line["category_id"] = category_id
        line["name"] = category.get("name")
        line["active"] = bool(category.get("active"))
        if line["has_limit"]:
            budgeted_count += 1
        elif used:
            unbudgeted_count += 1
            unbudgeted_cents += used
        categories.append(line)

    # 已设置预算的分类排在前面，组内保持账本里的类别顺序
    categories.sort(key=lambda line: not line["has_limit"])

    return {
        "month": month,
        "month_label": models.month_label(month),
        "total": _budget_line(total_limit, used_total),
        "categories": categories,
        "expense_cents": used_total,
        "expense_count": expense_count,
        "budgeted_count": budgeted_count,
        "unbudgeted_count": unbudgeted_count,
        "unbudgeted_cents": unbudgeted_cents,
    }


def _budget_line(limit_cents, used_cents):
    """单条预算的状态。limit 为 None（或非正数）表示未设上限。"""
    if limit_cents is None or limit_cents <= 0:
        return {
            "has_limit": False,
            "limit_cents": None,
            "limit_text": "",
            "used_cents": used_cents,
            "used_text": models.format_cents(used_cents),
            "remaining_cents": 0,
            "overspent_cents": 0,
            "status": STATUS_UNSET,
            "status_text": "未设置预算",
            "percent": 0,
            "bar_percent": 0,
        }

    remaining = limit_cents - used_cents
    if remaining > 0:
        status = STATUS_OK
        status_text = f"剩余 {models.format_cents(remaining)} 元"
        remaining_cents, overspent_cents = remaining, 0
    elif remaining == 0:
        status = STATUS_EXHAUSTED
        status_text = "额度已用完"
        remaining_cents, overspent_cents = 0, 0
    else:
        status = STATUS_OVER
        status_text = f"超支 {models.format_cents(-remaining)} 元"
        remaining_cents, overspent_cents = 0, -remaining

    percent = used_cents * 100 // limit_cents
    return {
        "has_limit": True,
        "limit_cents": limit_cents,
        "limit_text": models.format_cents(limit_cents),
        "used_cents": used_cents,
        "used_text": models.format_cents(used_cents),
        "remaining_cents": remaining_cents,
        "overspent_cents": overspent_cents,
        "status": status,
        "status_text": status_text,
        "percent": percent,
        "bar_percent": min(percent, 100),
    }


# ---------------------------------------------------------------- 保存


def parse_budget_form(form_data, ledger):
    """校验预算表单，返回 (month, total_cents_or_None, {category_id: cents_or_None})。

    空字符串表示“清除该项”；0、负数和超两位小数都会被拒绝。
    """
    errors = {}

    month = models.parse_month(form_data.get("month"))
    if month is None:
        errors["month"] = "请选择要设置的月份（YYYY-MM）"

    total_cents = _parse_limit(form_data.get(TOTAL_FIELD), TOTAL_FIELD, errors)

    expense_categories = {
        category_id: category
        for category_id, category in models.category_index(ledger).items()
        if category.get("type") == models.TYPE_EXPENSE
    }
    category_limits = {}
    for key in form_data.keys():
        if not key.startswith(LIMIT_FIELD_PREFIX):
            continue
        category_id = key[len(LIMIT_FIELD_PREFIX):]
        if category_id not in expense_categories:
            errors[key] = "无效的支出类别"
            continue
        category_limits[category_id] = _parse_limit(form_data.get(key), key, errors)

    if errors:
        raise BudgetError(errors)
    return month, total_cents, category_limits


def save_budget(month, total_cents, category_limits):
    """写入某月预算，返回保存后的 budget_status。

    total_cents / 分类金额为 None 表示清除该项；某月两个上限都被清空时，
    整个月份键会从 budgets 中移除，保证账本里不留空壳。
    """
    if models.parse_month(month) is None:
        raise BudgetError({"month": "月份格式必须是 YYYY-MM"})

    snapshot = {}

    def change(ledger):
        budgets = ledger.setdefault("budgets", {})
        config = budgets.get(month)
        config = dict(config) if isinstance(config, dict) else {}

        if total_cents is None:
            config.pop("total_cents", None)
        else:
            config["total_cents"] = int(total_cents)

        limits = dict(config.get("category_limits") or {})
        for category_id, cents in category_limits.items():
            if cents is None:
                limits.pop(category_id, None)
            else:
                limits[category_id] = int(cents)
        if limits:
            config["category_limits"] = limits
        else:
            config.pop("category_limits", None)

        if config:
            budgets[month] = config
        else:
            budgets.pop(month, None)

        snapshot.clear()
        snapshot.update(budget_status(ledger, month))

    _storage().update_ledger(change)
    return dict(snapshot)


def _parse_limit(text, field, errors):
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None
    validators = _validators()
    try:
        return validators.parse_amount_cents(raw, field=field)
    except validators.ValidationError as exc:
        errors[field] = exc.errors.get(field, "预算金额格式不正确")
        return None


# ---------------------------------------------------------------- 页面


@bp.app_template_filter("yuan")
def _yuan_filter(cents):
    """模板里把整数分显示成元，例如 51500 → 515.00。"""
    return models.format_cents(cents)


@bp.get("/budget")
def budget_page():
    raw_month = request.args.get("month")
    month = models.parse_month(raw_month) or models.current_month()
    warning = None
    if raw_month and models.parse_month(raw_month) is None:
        warning = "月份格式应为 YYYY-MM，已显示本月预算。"
    ledger = _load_ledger()
    return _render(ledger, month, warning=warning)


@bp.post("/budget")
def save_budget_page():
    ledger = _load_ledger()
    try:
        month, total_cents, category_limits = parse_budget_form(request.form, ledger)
    except BudgetError as exc:
        month = models.parse_month(request.form.get("month")) or models.current_month()
        return _render(ledger, month, form_errors=exc.errors, form_values=request.form), 400

    save_budget(month, total_cents, category_limits)
    flash(f"{models.month_label(month)}的预算已保存。", "success")
    return redirect(url_for("budget.budget_page", month=month))


def _render(ledger, month, warning=None, form_errors=None, form_values=None):
    status = budget_status(ledger, month)
    form_errors = dict(form_errors or {})

    # 有的错误没有对应的输入框（例如手工构造请求给收入类别加预算），
    # 这类提示统一放到页面顶部，避免“提交失败却看不到原因”。
    known_fields = {"month", TOTAL_FIELD}
    known_fields.update(
        f"{LIMIT_FIELD_PREFIX}{item['category_id']}" for item in status["categories"]
    )
    general_errors = [message for field, message in form_errors.items() if field not in known_fields]

    return render_template(
        "budget.html",
        status=status,
        month=month,
        month_label=models.month_label(month),
        prev_month=models.shift_month(month, -1),
        next_month=models.shift_month(month, 1),
        current_month=models.current_month(),
        months=models.months_in_ledger(ledger),
        warning=warning,
        form_errors=form_errors,
        general_errors=general_errors,
        form_values=form_values,
        navigation=_navigation(),
    )


#: 顶部导航项，与应用入口 NAV_ITEMS 保持一致。
#: base.html 需要 navigation 变量，而应用入口目前只在首页传入；
#: 应用入口统一注入导航后，这段可以删除。
_NAV_ITEMS = (
    ("首页", "/"),
    ("记一笔", "/entries/new"),
    ("流水", "/entries"),
    ("统计", "/stats"),
    ("预算", "/budget"),
    ("类别", "/categories"),
    ("备份", "/backup"),
)


def _navigation():
    """只保留当前应用实际注册了的页面，避免链接到还不存在的路由。"""
    paths = {rule.rule for rule in current_app.url_map.iter_rules()}
    return [
        {"label": label, "path": path}
        for label, path in _NAV_ITEMS
        if path == "/" or path in paths
    ]