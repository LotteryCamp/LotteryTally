"""PocketLedger 收支汇总、分类占比、每日趋势与月度同期对比计算（王智钦模块）。

负责人：王智钦 102401609
对应《PocketLedger 现场编程与技术实施方案》第二节、第四节与第五节：
- summarize(entries, categories=None)：返回笔数、总收入、总支出、结余和分类汇总
- daily_totals(entries, start_date=None, end_date=None)：返回按日期升序的每日收入和支出，缺失日补零
- monthly_comparison(entries, today=None)：计算「本月至今」与「上月同期」支出对比（月末自动对齐）

本模块为纯计算层，不读写本地文件、不依赖 Flask，可独立运行并通过 unittest 验收。
所有金额运算均采用正整数分（cents），杜绝二进制浮点数误差。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

__all__ = [
    "SummaryValidationError",
    "format_cents",
    "summarize",
    "daily_totals",
    "monthly_comparison",
]

VALID_TYPES = ("income", "expense")
UNKNOWN_CATEGORY_NAME = "未分类"
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class SummaryValidationError(ValueError):
    """统计输入数据不合法时抛出的异常。"""


def _get_field(item: Any, key: str, default: Any = None) -> Any:
    """兼容 dict 与对象属性读取，防止上下游模型表示形式差异导致报错。"""
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _validate_iso_date(raw_date: Any, label: str = "日期") -> date:
    """校验 YYYY-MM-DD 字符串或 date 对象并返回 datetime.date。"""
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        return raw_date
    if not isinstance(raw_date, str):
        raise SummaryValidationError(f"{label}必须为 YYYY-MM-DD 字符串，收到：{raw_date!r}")
    text = raw_date.strip()
    if not _DATE_PATTERN.fullmatch(text):
        raise SummaryValidationError(f"{label}格式须为 YYYY-MM-DD，收到：{raw_date!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SummaryValidationError(f"{label}不是真实存在的日历日期：{raw_date!r}") from exc


def _normalize_amount_cents(raw_amount: Any, label: str = "金额") -> int:
    """校验并规范化正整数分金额，拒绝布尔值、非整数、负数与空值。"""
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
        raise SummaryValidationError(f"{label}必须为整数分（int），收到：{raw_amount!r}")
    if raw_amount <= 0:
        raise SummaryValidationError(f"{label}必须为大于 0 的正整数分，收到：{raw_amount!r}")
    return raw_amount


def format_cents(cents: int) -> str:
    """将整数分转换为两位小数的元字符串（如 4500 -> '45.00'，-1500 -> '-15.00'）。

    全程使用整数整除与取模，不经过 float。
    """
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise SummaryValidationError(f"format_cents 仅接受整数分，收到：{cents!r}")
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}{abs_cents // 100}.{abs_cents % 100:02d}"


def _build_category_map(categories: Iterable[ Any ] | Mapping[str, Any] | None) -> dict[str, str]:
    """将类别列表或字典转换为 category_id -> category_name 映射表。"""
    if categories is None:
        return {}
    if isinstance(categories, Mapping):
        result: dict[str, str] = {}
        for k, v in categories.items():
            if isinstance(v, Mapping):
                result[str(k)] = str(v.get("name") or k)
            else:
                result[str(k)] = str(v or k)
        return result

    mapping: dict[str, str] = {}
    for item in categories:
        cid = _get_field(item, "id")
        if cid:
            name = _get_field(item, "name") or cid
            mapping[str(cid)] = str(name)
    return mapping


def _normalize_entries(entries: Iterable[Any] | None) -> list[dict[str, Any]]:
    """校验并提取参与统计的账目字段。"""
    if entries is None:
        return []
    try:
        items = list(entries)
    except TypeError as exc:
        raise SummaryValidationError("entries 必须是可迭代对象。") from exc

    normalized: list[dict[str, Any]] = []
    for idx, entry in enumerate(items, start=1):
        label = f"第 {idx} 条账目"
        if entry is None or isinstance(entry, (str, bytes, int, float, bool)):
            raise SummaryValidationError(f"{label}不是有效的账目映射或对象。")

        entry_date = _validate_iso_date(_get_field(entry, "date"), f"{label}的日期")
        entry_type = _get_field(entry, "type") or _get_field(entry, "kind")
        if entry_type not in VALID_TYPES:
            raise SummaryValidationError(
                f"{label}的收支类型须为 income 或 expense，收到：{entry_type!r}"
            )

        raw_cents = _get_field(entry, "amount_cents")
        if raw_cents is None:
            raw_cents = _get_field(entry, "amount_fen")
        amount_cents = _normalize_amount_cents(raw_cents, f"{label}的金额")

        raw_cat = _get_field(entry, "category_id")
        if raw_cat is None:
            raw_cat = _get_field(entry, "category")
        category_id = str(raw_cat).strip() if raw_cat is not None else ""

        normalized.append({
            "date": entry_date.isoformat(),
            "date_obj": entry_date,
            "type": entry_type,
            "amount_cents": amount_cents,
            "category_id": category_id,
        })
    return normalized


def _aggregate_by_category(
    rows: list[dict[str, Any]],
    target_type: str,
    total_cents: int,
    cat_map: dict[str, str],
) -> list[dict[str, Any]]:
    """按类别聚合指定收支类型的金额、笔数和占比，按金额降序排列。"""
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["type"] != target_type:
            continue
        cid = row["category_id"] or ""
        if cid not in buckets:
            buckets[cid] = {"amount_cents": 0, "count": 0}
        buckets[cid]["amount_cents"] += row["amount_cents"]
        buckets[cid]["count"] += 1

    items: list[dict[str, Any]] = []
    for cid, stats in buckets.items():
        amount = stats["amount_cents"]
        name = cat_map.get(cid) or cid or UNKNOWN_CATEGORY_NAME
        if total_cents > 0:
            # 使用整数千分比四舍五入计算两位小数百分比，避免浮点中间误差
            basis_points = (amount * 10000 + total_cents // 2) // total_cents
            percent = basis_points / 100.0
            share_text = f"{basis_points // 100}.{basis_points % 100:02d}%"
        else:
            percent = 0.0
            share_text = "0.00%"

        items.append({
            "category_id": cid or "uncategorized",
            "category_name": name,
            "name": name,
            "label": name,
            "amount_cents": amount,
            "amount_yuan": format_cents(amount),
            "count": stats["count"],
            "percent": percent,
            "share_text": share_text,
        })

    # 按金额降序排列；金额相同时按类别名称及 ID 升序稳定决胜
    items.sort(key=lambda item: (-item["amount_cents"], item["category_name"], item["category_id"]))
    return items


def summarize(
    entries: Iterable[Any] | None,
    categories: Iterable[Any] | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """计算筛选后账目集合的收支汇总与分类小计（方案 5.2 & 6.2 节）。

    返回字段：
        - count: 总记录数（int）
        - income_count: 收入笔数（int）
        - expense_count: 支出笔数（int）
        - income_cents: 总收入（整数分）
        - expense_cents: 总支出（整数分）
        - balance_cents: 结余 = income_cents - expense_cents（整数分）
        - income_yuan / expense_yuan / balance_yuan: 两位小数元字符串
        - by_category / category_totals / expense_by_category:
          按支出金额降序排列的分类支出汇总列表（无支出时为空列表 []）
        - income_by_category: 按收入金额降序排列的分类收入汇总列表
    """
    rows = _normalize_entries(entries)
    cat_map = _build_category_map(categories)

    income_cents = 0
    expense_cents = 0
    income_count = 0
    expense_count = 0

    for row in rows:
        if row["type"] == "income":
            income_cents += row["amount_cents"]
            income_count += 1
        else:
            expense_cents += row["amount_cents"]
            expense_count += 1

    balance_cents = income_cents - expense_cents
    expense_categories = _aggregate_by_category(rows, "expense", expense_cents, cat_map)
    income_categories = _aggregate_by_category(rows, "income", income_cents, cat_map)

    return {
        "count": len(rows),
        "income_count": income_count,
        "expense_count": expense_count,
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "balance_cents": balance_cents,
        "income_yuan": format_cents(income_cents),
        "expense_yuan": format_cents(expense_cents),
        "balance_yuan": format_cents(balance_cents),
        "by_category": expense_categories,
        "category_totals": expense_categories,
        "expense_by_category": expense_categories,
        "income_by_category": income_categories,
    }


def daily_totals(
    entries: Iterable[Any] | None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[dict[str, Any]]:
    """计算按日期升序排列的每日收入与支出序列，缺失日期自动补零（方案 5.2 & 6.2 节）。

    日期范围规则：
        1. 若同时指定 start_date 与 end_date，按该闭区间逐日生成序列（即使 entries 为空也生成全零序列）。
        2. 若未指定日期范围，按匹配记录的首末日期生成连续日序列；无记录且无完整起止日期时返回 []。
        3. 若只指定一端日期且有记录，另一端取记录中的最远日期（若记录超出单侧边界则以两者包络为准）。
    """
    rows = _normalize_entries(entries)

    explicit_start = _validate_iso_date(start_date, "起始日期") if start_date else None
    explicit_end = _validate_iso_date(end_date, "结束日期") if end_date else None

    if explicit_start and explicit_end and explicit_start > explicit_end:
        raise SummaryValidationError("结束日期不能早于起始日期。")

    if not rows:
        if explicit_start and explicit_end:
            start_day, end_day = explicit_start, explicit_end
        else:
            return []
    else:
        min_entry_day = min(row["date_obj"] for row in rows)
        max_entry_day = max(row["date_obj"] for row in rows)
        start_day = explicit_start if explicit_start is not None else min_entry_day
        end_day = explicit_end if explicit_end is not None else max_entry_day
        if start_day > end_day:
            raise SummaryValidationError("结束日期不能早于起始日期。")

    daily_map: dict[date, dict[str, int]] = {}
    for row in rows:
        day = row["date_obj"]
        if day < start_day or day > end_day:
            continue
        if day not in daily_map:
            daily_map[day] = {"income_cents": 0, "expense_cents": 0, "count": 0}
        if row["type"] == "income":
            daily_map[day]["income_cents"] += row["amount_cents"]
        else:
            daily_map[day]["expense_cents"] += row["amount_cents"]
        daily_map[day]["count"] += 1

    series: list[dict[str, Any]] = []
    current = start_day
    one_day = timedelta(days=1)
    while current <= end_day:
        bucket = daily_map.get(current, {"income_cents": 0, "expense_cents": 0, "count": 0})
        inc = bucket["income_cents"]
        exp = bucket["expense_cents"]
        bal = inc - exp
        series.append({
            "date": current.isoformat(),
            "income_cents": inc,
            "expense_cents": exp,
            "balance_cents": bal,
            "income_yuan": format_cents(inc),
            "expense_yuan": format_cents(exp),
            "balance_yuan": format_cents(bal),
            "count": bucket["count"],
        })
        current += one_day

    return series


def monthly_comparison(
    entries: Iterable[Any] | None,
    today: str | date | None = None,
) -> dict[str, Any]:
    """计算「本月至今」与「上月同期」收支对比（方案 4.3 节）。

    口径约定：
        - 本月至今：当月 1 日至 today（含边界）。
        - 上月同期：上月 1 日至 min(today.day, 上月最后一天)（含边界）。
          例如 2026-03-31 对比 2026-02-01 至 2026-02-28（闰年则为 02-29）。
        - 单独标注使用全账本数据，不受当前页面筛选条件影响。
    """
    rows = _normalize_entries(entries)
    if today is None:
        ref_day = datetime.now().astimezone().date()
    else:
        ref_day = _validate_iso_date(today, "基准日期 today")

    cur_start = ref_day.replace(day=1)
    cur_end = ref_day

    prev_month_last_date = cur_start - timedelta(days=1)
    prev_start = prev_month_last_date.replace(day=1)
    prev_last_day_num = calendar.monthrange(prev_start.year, prev_start.month)[1]
    prev_end = prev_start.replace(day=min(ref_day.day, prev_last_day_num))

    cur_expense = 0
    cur_income = 0
    prev_expense = 0
    prev_income = 0

    for row in rows:
        day = row["date_obj"]
        if cur_start <= day <= cur_end:
            if row["type"] == "expense":
                cur_expense += row["amount_cents"]
            else:
                cur_income += row["amount_cents"]
        elif prev_start <= day <= prev_end:
            if row["type"] == "expense":
                prev_expense += row["amount_cents"]
            else:
                prev_income += row["amount_cents"]

    diff_expense = cur_expense - prev_expense
    if diff_expense > 0:
        direction = "up"
    elif diff_expense < 0:
        direction = "down"
    else:
        direction = "flat"

    if prev_expense > 0:
        basis_points = (abs(diff_expense) * 10000 + prev_expense // 2) // prev_expense
        change_percent: float | None = basis_points / 100.0
        sign = "+" if diff_expense > 0 else ("-" if diff_expense < 0 else "")
        change_text = f"{sign}{basis_points // 100}.{basis_points % 100:02d}%"
    elif cur_expense > 0:
        change_percent = None
        change_text = "上月同期为 0.00 元"
    else:
        change_percent = 0.0
        change_text = "0.00%"

    return {
        "current_month": cur_start.strftime("%Y-%m"),
        "previous_month": prev_start.strftime("%Y-%m"),
        "current_start": cur_start.isoformat(),
        "current_end": cur_end.isoformat(),
        "previous_start": prev_start.isoformat(),
        "previous_end": prev_end.isoformat(),
        "current_expense_cents": cur_expense,
        "previous_expense_cents": prev_expense,
        "current_income_cents": cur_income,
        "previous_income_cents": prev_income,
        "diff_expense_cents": diff_expense,
        "current_expense_yuan": format_cents(cur_expense),
        "previous_expense_yuan": format_cents(prev_expense),
        "diff_expense_yuan": format_cents(diff_expense),
        "direction": direction,
        "change_percent": change_percent,
        "change_text": change_text,
        "scope_note": "注：月度对比固定使用全账本数据，统计「本月至今」与「上月同期」支出。",
    }
