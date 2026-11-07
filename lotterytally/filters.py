"""PocketLedger 查询参数、组合筛选和稳定排序（杜玉鹤模块）。

只处理内存中的统一账目，不读写文件、不分页、不依赖 Flask。
金额比较使用整数分；调用方将本地账本日期传给 parse_filters。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, TypedDict

from lotterytally.validators import ValidationError, parse_amount_range


__all__ = [
    "FilterCriteria", "FilterValidationError", "parse_filters",
    "filter_entries", "sort_entries", "filters_to_query",
]

SORT_ORDERS = ("date_desc", "date_asc", "amount_asc", "amount_desc")
DATE_PRESETS = ("today", "this_week", "this_month", "last_month")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class FilterCriteria(TypedDict):
    start_date: str | None
    end_date: str | None
    category_id: str | None
    type: str | None
    min_amount_cents: int | None
    max_amount_cents: int | None
    q: str
    sort: str
    preset: str


class FilterValidationError(ValueError):
    """字段名到中文提示的映射，可直接用于页面错误提示。"""

    def __init__(self, errors: Mapping[str, str]):
        self.errors = dict(errors)
        super().__init__("；".join(self.errors.values()))


def _query_text(query: Mapping[str, Any], key: str,
                errors: dict[str, str]) -> str:
    value = query.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        errors[key] = "查询参数必须为文本。"
        return ""
    return value.strip()


def _parse_date(text: str, field: str, errors: dict[str, str]) -> str | None:
    if not text:
        return None
    try:
        if not _DATE_PATTERN.fullmatch(text):
            raise ValueError
        return date.fromisoformat(text).isoformat()
    except ValueError:
        errors[field] = "请输入实际存在的日期，格式为 YYYY-MM-DD。"
        return None


def _preset_range(preset: str, today: date) -> tuple[str, str]:
    if preset == "today":
        start = end = today
    elif preset == "this_week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        start = today.replace(day=1)
        if preset == "last_month":
            start = (start - timedelta(days=1)).replace(day=1)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return start.isoformat(), end.isoformat()


def parse_filters(query: Mapping[str, Any], today: date) -> FilterCriteria:
    """解析 dict 或 Flask request.args；非法输入聚合为字段错误。

    任一显式日期非空时，整组快捷日期失效，另一端保持开放。
    page 和未知参数不属于筛选条件，忽略；不接收来自网页的整数分参数。
    """
    if type(today) is not date:
        raise TypeError("today 必须是本地账本的 datetime.date。")
    errors: dict[str, str] = {}
    values = {key: _query_text(query, key, errors) for key in (
        "start_date", "end_date", "category_id", "type", "min_amount",
        "max_amount", "q", "sort", "preset",
    )}
    start = _parse_date(values["start_date"], "start_date", errors)
    end = _parse_date(values["end_date"], "end_date", errors)
    preset = values["preset"]
    if values["start_date"] or values["end_date"]:
        preset = ""
    elif preset:
        if preset not in DATE_PRESETS:
            errors["preset"] = "请选择今天、本周、本月或上月。"
        else:
            try:
                start, end = _preset_range(preset, today)
            except (OverflowError, ValueError):
                errors["preset"] = "快捷日期范围超出支持的日历范围。"
    if start and end and start > end:
        errors["end_date"] = "结束日期不能早于起始日期。"

    entry_type = values["type"] or None
    if entry_type is not None and entry_type not in ("income", "expense"):
        errors["type"] = "收支类型须为 income 或 expense。"
    minimum = maximum = None
    try:
        minimum, maximum = parse_amount_range(values["min_amount"], values["max_amount"])
    except ValidationError as exc:
        errors.update(exc.errors)
    order = values["sort"] or "date_desc"
    if order not in SORT_ORDERS:
        errors["sort"] = "请选择日期升序、日期降序、金额升序或金额降序。"
    if errors:
        raise FilterValidationError(errors)
    return {
        "start_date": start, "end_date": end,
        "category_id": values["category_id"] or None, "type": entry_type,
        "min_amount_cents": minimum, "max_amount_cents": maximum,
        "q": values["q"], "sort": order, "preset": preset,
    }


def filter_entries(entries: Iterable[Mapping[str, Any]],
                   criteria: FilterCriteria) -> list[Mapping[str, Any]]:
    """对符合统一模型的账目执行 AND 筛选，保留原顺序与完整匹配集合。

    类别只匹配稳定 ID，因此停用类别的历史流水仍可查询。
    返回新列表，账目对象只读，调用方负责后续排序和分页。
    """
    start, end = criteria["start_date"], criteria["end_date"]
    category, entry_type = criteria["category_id"], criteria["type"]
    minimum, maximum = criteria["min_amount_cents"], criteria["max_amount_cents"]
    keyword = criteria["q"]
    return [entry for entry in entries if (
        (start is None or entry["date"] >= start)
        and (end is None or entry["date"] <= end)
        and (category is None or entry["category_id"] == category)
        and (entry_type is None or entry["type"] == entry_type)
        and (minimum is None or entry["amount_cents"] >= minimum)
        and (maximum is None or entry["amount_cents"] <= maximum)
        and (not keyword or keyword in entry.get("note", ""))
    )]


def _recent_key(entry: Mapping[str, Any]) -> tuple[str, datetime, str]:
    created = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("账目 created_at 必须包含时区。")
    return entry["date"], created, entry["id"]


def sort_entries(entries: Iterable[Mapping[str, Any]],
                 order: str = "date_desc") -> list[Mapping[str, Any]]:
    """默认日期、录入时刻、ID 倒序；金额相同使用同一顺序稳定决胜。"""
    if order not in SORT_ORDERS:
        raise FilterValidationError({"sort": "不支持的排序方式。"})
    recent = sorted(entries, key=_recent_key, reverse=True)
    if order == "date_asc":
        return sorted(recent, key=lambda entry: entry["date"])
    if order in ("amount_asc", "amount_desc"):
        return sorted(recent, key=lambda entry: entry["amount_cents"],
                      reverse=order == "amount_desc")
    return recent


def filters_to_query(criteria: FilterCriteria) -> dict[str, str]:
    """生成翻页、统计及导出共用的生效 GET 参数，快捷日期固化为日期。"""
    params = {key: criteria[key] for key in (
        "start_date", "end_date", "category_id", "type", "q", "sort",
    ) if criteria[key]}
    for key in ("min_amount", "max_amount"):
        cents = criteria[key + "_cents"]
        if cents is not None:
            params[key] = f"{cents // 100}.{cents % 100:02d}"
    return params
