"""统一账本模型（PocketLedger）。

本文件属于佘博海 102401137 的模块：集中定义 schema 常量、账本结构的创建与校验、
账目排序规则、月份换算和类别查找工具。

约定
----
1. 账本就是一个普通 dict，和 data/ledger.json 一一对应，storage 与备份模块可以原样搬运。
2. 金额一律用正整数“分”保存，避免浮点误差；本模块只做结构校验，
   用户输入的金额文本由 validators.parse_amount_cents() 负责转换。
3. entries / budget / stats / backup 等模块统一通过本模块访问账本字段，
   不要各自硬编码 "amount_cents"、"category_id" 之类的键名。
"""

from __future__ import annotations

import calendar
import copy
import re
import uuid
from datetime import date, datetime

SCHEMA_VERSION = 1

TYPE_INCOME = "income"
TYPE_EXPENSE = "expense"
ENTRY_TYPES = (TYPE_INCOME, TYPE_EXPENSE)
TYPE_LABELS = {TYPE_INCOME: "收入", TYPE_EXPENSE: "支出"}

DATE_FORMAT = "%Y-%m-%d"
MONTH_FORMAT = "%Y-%m"
NOTE_MAX_LENGTH = 200
CATEGORY_NAME_MAX_LENGTH = 20

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class LedgerFormatError(ValueError):
    """账本结构不符合 schema 约定。

    problems 保存全部问题，恢复页可以一次性列给用户，而不是只报第一条。
    """

    def __init__(self, problems):
        self.problems = list(problems)
        head = "；".join(self.problems[:3])
        more = "" if len(self.problems) <= 3 else f"（共 {len(self.problems)} 处问题）"
        super().__init__(f"账本格式不合法：{head}{more}")


# ---------------------------------------------------------------- 时间与日期


def now_iso(moment=None):
    """当前本地时间，带时区偏移，例如 2026-11-07T16:20:00+08:00。"""
    moment = moment or datetime.now().astimezone()
    return moment.isoformat(timespec="seconds")


def new_entry_id():
    """新增账目的 UUID 主键；编辑时保持不变。"""
    return str(uuid.uuid4())


def parse_date(text):
    """把 'YYYY-MM-DD' 解析成 date；格式或日历上不合法时返回 None。"""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, DATE_FORMAT).date()
    except ValueError:
        return None


def is_valid_date(text):
    return parse_date(text) is not None


def parse_timestamp(text):
    """解析带时区的 ISO 8601 时间；不合法时返回 None。"""
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_month(text):
    """校验 'YYYY-MM'；不合法时返回 None。"""
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text if MONTH_RE.match(text) else None


def month_of(value):
    """date / 'YYYY-MM-DD' → 'YYYY-MM'。"""
    if isinstance(value, datetime):
        return value.strftime(MONTH_FORMAT)
    if isinstance(value, date):
        return value.strftime(MONTH_FORMAT)
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"无法从 {value!r} 得到月份")
    return parsed.strftime(MONTH_FORMAT)


def entry_month(entry):
    """账目所属月份（预算归属口径）；日期不合法时返回 None。"""
    if not isinstance(entry, dict):
        return None
    value = entry.get("date")
    return value.strip()[:7] if isinstance(value, str) and is_valid_date(value) else None


def current_month(today=None):
    return month_of(today or date.today())


def shift_month(month, delta):
    """'2026-01' 减一个季度得到 '2025-10'；跨年自动进位。"""
    year, mon = (int(part) for part in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_bounds(month):
    """月份 → (该月第一天, 该月最后一天)。"""
    first = date.fromisoformat(f"{month}-01")
    last_day = calendar.monthrange(first.year, first.month)[1]
    return first, date(first.year, first.month, last_day)


def month_label(month):
    """'2026-11' → '2026 年 11 月'，供模板直接展示。"""
    year, mon = month.split("-")
    return f"{year} 年 {int(mon)} 月"


def months_in_ledger(ledger):
    """账本里出现过的月份（账目日期 + 预算），按时间倒序，供预算页快捷跳转。"""
    months = set()
    for entry in ledger.get("entries") or []:
        month = entry_month(entry)
        if month:
            months.add(month)
    for month in ledger.get("budgets") or {}:
        if parse_month(month):
            months.add(month)
    return sorted(months, reverse=True)


# ---------------------------------------------------------------- 账本与查找


def new_ledger(categories=None, entries=None, budgets=None):
    """构造一份空账本；默认类别由 categories.default_categories() 提供。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "categories": copy.deepcopy(list(categories or [])),
        "entries": copy.deepcopy(list(entries or [])),
        "budgets": copy.deepcopy(dict(budgets or {})),
    }


def category_index(ledger):
    """类别 ID → 类别 dict，保持 categories 数组顺序。"""
    index = {}
    for category in ledger.get("categories") or []:
        if isinstance(category, dict) and isinstance(category.get("id"), str):
            index.setdefault(category["id"], category)
    return index


def find_category(ledger, category_id):
    if not isinstance(category_id, str):
        return None
    for category in ledger.get("categories") or []:
        if isinstance(category, dict) and category.get("id") == category_id:
            return category
    return None


def category_name(ledger, category_id, fallback="（已删除类别）"):
    category = find_category(ledger, category_id)
    if category is None:
        return fallback
    return str(category.get("name") or fallback)


def find_entry(ledger, entry_id):
    if not isinstance(entry_id, str):
        return None
    for entry in ledger.get("entries") or []:
        if isinstance(entry, dict) and entry.get("id") == entry_id:
            return entry
    return None


def entry_sort_key(entry):
    """最近记录口径：日期倒序 → 录入时间倒序 → ID 倒序。"""
    return (
        str(entry.get("date", "")),
        str(entry.get("created_at", "")),
        str(entry.get("id", "")),
    )


def sort_entries_newest_first(entries):
    return sorted(entries, key=entry_sort_key, reverse=True)


def format_cents(cents):
    """整数分 → '45.00'（不带千分位，避免表单/CSV 解析歧义）。"""
    try:
        value = int(cents)
    except (TypeError, ValueError):
        value = 0
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


# ---------------------------------------------------------------- 结构校验


def _is_positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_ledger_payload(payload):
    """校验完整账本结构，返回问题列表；空列表表示通过。

    备份恢复（backup.restore）与存储写入（storage.update_ledger）共用这个函数，
    这样“页面能写进去的数据”和“备份能恢复的数据”是同一套规则。
    """
    problems = []

    def add(path, message):
        problems.append(f"{path}：{message}")

    if not isinstance(payload, dict):
        return ["账本根节点必须是 JSON 对象"]

    if isinstance(payload.get("schema_version"), bool) or payload.get("schema_version") != SCHEMA_VERSION:
        add("schema_version", f"必须是 {SCHEMA_VERSION}，实际为 {payload.get('schema_version')!r}")

    # -- 类别
    categories = payload.get("categories")
    known_categories = {}
    seen_names = {}
    if not isinstance(categories, list):
        add("categories", "必须是数组")
        categories = []
    for i, category in enumerate(categories):
        path = f"categories[{i}]"
        if not isinstance(category, dict):
            add(path, "必须是对象")
            continue
        category_id = category.get("id")
        if not isinstance(category_id, str) or not category_id.strip():
            add(f"{path}.id", "缺失或为空")
        elif category_id in known_categories:
            add(f"{path}.id", f"类别 ID {category_id!r} 重复")
        else:
            known_categories[category_id] = category

        name = category.get("name")
        if not isinstance(name, str) or not name.strip():
            add(f"{path}.name", "缺失或为空")
        elif len(name.strip()) > CATEGORY_NAME_MAX_LENGTH:
            add(f"{path}.name", f"超过 {CATEGORY_NAME_MAX_LENGTH} 个字符")

        category_type = category.get("type")
        if category_type not in ENTRY_TYPES:
            add(f"{path}.type", "必须是 income 或 expense")
        if not isinstance(category.get("active"), bool):
            add(f"{path}.active", "必须是 true 或 false")

        if isinstance(name, str) and name.strip() and category_type in ENTRY_TYPES:
            key = (category_type, name.strip())
            if key in seen_names:
                add(f"{path}.name", f"同一收支类型下类别名 {name.strip()!r} 重复")
            else:
                seen_names[key] = category_id

    # -- 账目
    entries = payload.get("entries")
    seen_entry_ids = set()
    if not isinstance(entries, list):
        add("entries", "必须是数组")
        entries = []
    for i, entry in enumerate(entries):
        path = f"entries[{i}]"
        if not isinstance(entry, dict):
            add(path, "必须是对象")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            add(f"{path}.id", "缺失或为空")
        elif entry_id in seen_entry_ids:
            add(f"{path}.id", f"账目 ID {entry_id!r} 重复")
        else:
            seen_entry_ids.add(entry_id)

        if not is_valid_date(entry.get("date")):
            add(f"{path}.date", "必须是实际存在的日期（YYYY-MM-DD）")

        entry_type = entry.get("type")
        if entry_type not in ENTRY_TYPES:
            add(f"{path}.type", "必须是 income 或 expense")

        if not _is_positive_int(entry.get("amount_cents")):
            add(f"{path}.amount_cents", "必须是正整数（单位：分），不能为 0 或负数")

        category_id = entry.get("category_id")
        category = known_categories.get(category_id) if isinstance(category_id, str) else None
        if category is None:
            add(f"{path}.category_id", f"引用了不存在的类别 {category_id!r}")
        elif entry_type in ENTRY_TYPES and category.get("type") != entry_type:
            add(
                f"{path}.category_id",
                f"类别类型 {category.get('type')!r} 与账目类型 {entry_type!r} 不一致",
            )

        note = entry.get("note", "")
        if not isinstance(note, str):
            add(f"{path}.note", "必须是字符串")
        elif len(note) > NOTE_MAX_LENGTH:
            add(f"{path}.note", f"超过 {NOTE_MAX_LENGTH} 个字符")

        for key in ("created_at", "updated_at"):
            if parse_timestamp(entry.get(key)) is None:
                add(f"{path}.{key}", "缺失或不是合法的 ISO 8601 时间")

    # -- 预算（只存上限，已用金额实时算）
    budgets = payload.get("budgets")
    if not isinstance(budgets, dict):
        add("budgets", "必须是对象")
        budgets = {}
    for month, config in budgets.items():
        path = f"budgets[{month!r}]"
        if parse_month(month) is None:
            add(path, "月份必须是 YYYY-MM")
            continue
        if not isinstance(config, dict):
            add(path, "必须是对象")
            continue
        total = config.get("total_cents")
        if total is not None and not _is_positive_int(total):
            add(f"{path}.total_cents", "必须是正整数（单位：分）；未设置时应省略该键")
        limits = config.get("category_limits", {})
        if not isinstance(limits, dict):
            add(f"{path}.category_limits", "必须是对象")
            continue
        for category_id, cents in limits.items():
            limit_path = f"{path}.category_limits[{category_id!r}]"
            category = known_categories.get(category_id) if isinstance(category_id, str) else None
            if category is None:
                add(limit_path, f"引用了不存在的类别 {category_id!r}")
            elif category.get("type") != TYPE_EXPENSE:
                add(limit_path, "只能给支出类别设置分类预算")
            if not _is_positive_int(cents):
                add(limit_path, "必须是正整数（单位：分）")

    return problems


def assert_valid_ledger(payload):
    problems = validate_ledger_payload(payload)
    if problems:
        raise LedgerFormatError(problems)
    return payload