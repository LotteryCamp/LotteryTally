"""单元测试：lotterytally.summary 统计汇总、分类小计、每日趋势与月度对比（王智钦模块）。

负责人：王智钦 102401609
运行方式：python -m unittest discover -s test -v
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import unittest

from lotterytally.summary import (
    SummaryValidationError,
    daily_totals,
    format_cents,
    monthly_comparison,
    summarize,
)


CATEGORIES = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "study", "name": "学习", "type": "expense", "active": True},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
]


def make_entry(
    entry_id: str = "e1",
    day: str = "2026-11-07",
    kind: str = "expense",
    amount_cents: int = 4500,
    category_id: str = "food",
    note: str = "晚餐",
    created_at: str = "2026-11-07T18:00:00+08:00",
) -> dict:
    return {
        "id": entry_id,
        "date": day,
        "type": kind,
        "amount_cents": amount_cents,
        "category_id": category_id,
        "note": note,
        "created_at": created_at,
    }


@dataclass
class DummyEntryObj:
    """模拟 dataclass 账目对象，验证 summary 对字典与对象的双兼容性。"""

    id: str
    date: str
    type: str
    amount_cents: int
    category_id: str
    note: str = ""


class FormatCentsTests(unittest.TestCase):
    def test_formats_positive_zero_and_negative_cents_without_float(self):
        self.assertEqual(format_cents(0), "0.00")
        self.assertEqual(format_cents(1), "0.01")
        self.assertEqual(format_cents(99), "0.99")
        self.assertEqual(format_cents(4500), "45.00")
        self.assertEqual(format_cents(51500), "515.00")
        self.assertEqual(format_cents(248500), "2485.00")
        self.assertEqual(format_cents(-1500), "-15.00")
        self.assertEqual(format_cents(-5), "-0.05")

    def test_rejects_non_int_or_boolean(self):
        for bad in (45.0, "4500", None, True, False):
            with self.subTest(bad=bad):
                with self.assertRaises(SummaryValidationError):
                    format_cents(bad)  # type: ignore[arg-type]


class SummarizeTests(unittest.TestCase):
    def test_empty_or_none_entries_returns_zeros_and_empty_categories(self):
        for empty in ([], None):
            with self.subTest(empty=empty):
                res = summarize(empty, CATEGORIES)
                self.assertEqual(res["count"], 0)
                self.assertEqual(res["income_cents"], 0)
                self.assertEqual(res["expense_cents"], 0)
                self.assertEqual(res["balance_cents"], 0)
                self.assertEqual(res["income_yuan"], "0.00")
                self.assertEqual(res["expense_yuan"], "0.00")
                self.assertEqual(res["balance_yuan"], "0.00")
                self.assertEqual(res["by_category"], [])

    def test_t07_acceptance_case_salary_and_food_expenses(self):
        """对应方案 T07：本月工资 3,000 元，饮食已支出 470 元，新增餐费 45 元 -> 支出 515 元，结余 2,485 元。"""
        entries = [
            make_entry("1", "2026-11-01", "income", 300000, "salary", "月度工资"),
            make_entry("2", "2026-11-03", "expense", 47000, "food", "日常餐饮"),
            make_entry("3", "2026-11-07", "expense", 4500, "food", "晚餐"),
        ]
        res = summarize(entries, CATEGORIES)
        self.assertEqual(res["count"], 3)
        self.assertEqual(res["income_count"], 1)
        self.assertEqual(res["expense_count"], 2)
        self.assertEqual(res["income_cents"], 300000)
        self.assertEqual(res["expense_cents"], 51500)
        self.assertEqual(res["balance_cents"], 248500)
        self.assertEqual(res["income_yuan"], "3000.00")
        self.assertEqual(res["expense_yuan"], "515.00")
        self.assertEqual(res["balance_yuan"], "2485.00")
        self.assertEqual(len(res["by_category"]), 1)
        self.assertEqual(res["by_category"][0]["category_id"], "food")
        self.assertEqual(res["by_category"][0]["category_name"], "饮食")
        self.assertEqual(res["by_category"][0]["amount_cents"], 51500)
        self.assertEqual(res["by_category"][0]["count"], 2)
        self.assertEqual(res["by_category"][0]["percent"], 100.0)

    def test_category_breakdown_sorted_by_expense_descending(self):
        entries = [
            make_entry("1", "2026-11-01", "expense", 1500, "traffic", "地铁"),
            make_entry("2", "2026-11-02", "expense", 6000, "food", "聚餐"),
            make_entry("3", "2026-11-03", "expense", 2500, "study", "买书"),
        ]
        res = summarize(entries, CATEGORIES)
        cats = res["by_category"]
        self.assertEqual([c["category_id"] for c in cats], ["food", "study", "traffic"])
        self.assertEqual([c["category_name"] for c in cats], ["饮食", "学习", "交通"])
        self.assertEqual([c["amount_cents"] for c in cats], [6000, 2500, 1500])
        self.assertEqual([c["share_text"] for c in cats], ["60.00%", "25.00%", "15.00%"])

    def test_supports_dataclass_objects_and_unknown_categories(self):
        entries = [
            DummyEntryObj("1", "2026-11-07", "expense", 2000, "retired_cat", "旧类别"),
        ]
        res = summarize(entries, CATEGORIES)
        self.assertEqual(res["expense_cents"], 2000)
        self.assertEqual(res["by_category"][0]["category_name"], "retired_cat")

    def test_rejects_invalid_entries(self):
        bad_cases = [
            [make_entry(amount_cents=0)],
            [make_entry(amount_cents=-100)],
            [make_entry(amount_cents=45.5)],  # type: ignore[arg-type]
            [make_entry(day="2026-02-30")],
            [make_entry(kind="transfer")],
        ]
        for bad in bad_cases:
            with self.subTest(bad=bad):
                with self.assertRaises(SummaryValidationError):
                    summarize(bad, CATEGORIES)


class DailyTotalsTests(unittest.TestCase):
    def test_fills_missing_dates_with_zeros_between_min_and_max_date(self):
        entries = [
            make_entry("1", "2026-11-01", "income", 300000, "salary"),
            make_entry("2", "2026-11-01", "expense", 2000, "food"),
            make_entry("3", "2026-11-04", "expense", 4500, "food"),
        ]
        series = daily_totals(entries)
        self.assertEqual([d["date"] for d in series], [
            "2026-11-01",
            "2026-11-02",
            "2026-11-03",
            "2026-11-04",
        ])
        self.assertEqual(series[0]["income_cents"], 300000)
        self.assertEqual(series[0]["expense_cents"], 2000)
        self.assertEqual(series[1]["income_cents"], 0)
        self.assertEqual(series[1]["expense_cents"], 0)
        self.assertEqual(series[1]["count"], 0)
        self.assertEqual(series[2]["expense_cents"], 0)
        self.assertEqual(series[3]["expense_cents"], 4500)

    def test_explicit_date_range_pads_leading_and_trailing_days(self):
        entries = [make_entry("1", "2026-11-02", "expense", 1000, "food")]
        series = daily_totals(entries, start_date="2026-11-01", end_date="2026-11-03")
        self.assertEqual(len(series), 3)
        self.assertEqual([d["expense_cents"] for d in series], [0, 1000, 0])

    def test_empty_entries_without_explicit_range_returns_empty_list(self):
        self.assertEqual(daily_totals([]), [])

    def test_rejects_reversed_date_range(self):
        with self.assertRaises(SummaryValidationError):
            daily_totals([], start_date="2026-11-08", end_date="2026-11-07")


class MonthlyComparisonTests(unittest.TestCase):
    def test_compares_month_to_date_with_previous_month_same_day(self):
        entries = [
            make_entry("1", "2026-10-05", "expense", 40000, "food"),
            make_entry("2", "2026-10-20", "expense", 99900, "food"),  # 晚于 10-07，不计入上月同期
            make_entry("3", "2026-11-03", "expense", 47000, "food"),
            make_entry("4", "2026-11-07", "expense", 4500, "food"),
        ]
        comp = monthly_comparison(entries, today=date(2026, 11, 7))
        self.assertEqual(comp["current_start"], "2026-11-01")
        self.assertEqual(comp["current_end"], "2026-11-07")
        self.assertEqual(comp["previous_start"], "2026-10-01")
        self.assertEqual(comp["previous_end"], "2026-10-07")
        self.assertEqual(comp["current_expense_cents"], 51500)
        self.assertEqual(comp["previous_expense_cents"], 40000)
        self.assertEqual(comp["diff_expense_cents"], 11500)
        self.assertEqual(comp["direction"], "up")
        self.assertEqual(comp["change_text"], "+28.75%")

    def test_clamps_previous_month_end_when_previous_month_is_shorter(self):
        """3 月 31 日对比 2 月同期时，上月截止到 2 月月末（平年 28 日）。"""
        entries = [
            make_entry("1", "2026-02-28", "expense", 20000, "food"),
            make_entry("2", "2026-03-31", "expense", 30000, "food"),
        ]
        comp = monthly_comparison(entries, today=date(2026, 3, 31))
        self.assertEqual(comp["previous_start"], "2026-02-01")
        self.assertEqual(comp["previous_end"], "2026-02-28")
        self.assertEqual(comp["previous_expense_cents"], 20000)
        self.assertEqual(comp["current_expense_cents"], 30000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
