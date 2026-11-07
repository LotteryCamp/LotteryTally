"""models.py 的单元测试：日期/月份边界、排序口径、账本结构校验。"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date

import support  # noqa: F401  # 安装存储/校验契约替身，并补充仓库根目录到 sys.path
from support import REPO_ROOT

from lotterytally import models


def example_ledger():
    """一份最小但合法的账本，供各用例按需破坏其中一处。"""
    return {
        "schema_version": 1,
        "categories": [
            {"id": "salary", "name": "工资", "type": "income", "active": True},
            {"id": "food", "name": "饮食", "type": "expense", "active": True},
        ],
        "entries": [
            {
                "id": "entry-1",
                "date": "2026-11-05",
                "type": "expense",
                "amount_cents": 4500,
                "category_id": "food",
                "note": "晚餐",
                "created_at": "2026-11-05T19:00:00+08:00",
                "updated_at": "2026-11-05T19:00:00+08:00",
            }
        ],
        "budgets": {"2026-11": {"total_cents": 150000, "category_limits": {"food": 50000}}},
    }


class DateTests(unittest.TestCase):
    def test_accepts_existing_calendar_dates(self):
        self.assertEqual(models.parse_date("2026-11-07"), date(2026, 11, 7))
        self.assertEqual(models.parse_date("2028-02-29"), date(2028, 2, 29))
        self.assertTrue(models.is_valid_date(" 2026-01-01 "))

    def test_rejects_impossible_and_non_iso_dates(self):
        for value in (
            "2026-02-30",  # 2 月没有 30 号
            "2027-02-29",  # 平年没有 2 月 29 日
            "2026-13-01",
            "2026-00-10",
            "2026-1-1",  # 必须是两位月份
            "2026/11/07",
            "20261107",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(models.parse_date(value))

    def test_now_iso_is_timezone_aware(self):
        stamp = models.parse_timestamp(models.now_iso())
        self.assertIsNotNone(stamp)
        self.assertIsNotNone(stamp.tzinfo)


class MonthTests(unittest.TestCase):
    def test_month_of(self):
        self.assertEqual(models.month_of("2026-11-07"), "2026-11")
        self.assertEqual(models.month_of(date(2026, 3, 1)), "2026-03")
        with self.assertRaises(ValueError):
            models.month_of("不是日期")

    def test_entry_month_uses_entry_date(self):
        self.assertEqual(models.entry_month({"date": "2025-03-05"}), "2025-03")
        self.assertIsNone(models.entry_month({"date": "2025-03-32"}))
        self.assertIsNone(models.entry_month("not-a-dict"))

    def test_shift_month_crosses_year_boundary(self):
        self.assertEqual(models.shift_month("2026-01", -1), "2025-12")
        self.assertEqual(models.shift_month("2026-12", 1), "2027-01")
        self.assertEqual(models.shift_month("2026-03", -13), "2025-02")
        self.assertEqual(models.shift_month("2026-03", 12), "2027-03")

    def test_month_bounds_includes_leap_february(self):
        self.assertEqual(
            models.month_bounds("2028-02"), (date(2028, 2, 1), date(2028, 2, 29))
        )
        self.assertEqual(
            models.month_bounds("2026-11"), (date(2026, 11, 1), date(2026, 11, 30))
        )

    def test_month_label_and_parse_month(self):
        self.assertEqual(models.month_label("2026-11"), "2026 年 11 月")
        self.assertEqual(models.parse_month(" 2026-11 "), "2026-11")
        for value in ("2026-13", "2026-1", "202611", "", None):
            with self.subTest(value=value):
                self.assertIsNone(models.parse_month(value))

    def test_months_in_ledger_collects_entries_and_budgets(self):
        ledger = example_ledger()
        ledger["entries"].append(
            {
                "id": "entry-2",
                "date": "2026-09-01",
                "type": "income",
                "amount_cents": 100,
                "category_id": "salary",
                "note": "",
                "created_at": "2026-09-01T09:00:00+08:00",
                "updated_at": "2026-09-01T09:00:00+08:00",
            }
        )
        self.assertEqual(models.months_in_ledger(ledger), ["2026-11", "2026-09"])


class SortingTests(unittest.TestCase):
    def test_newest_first_is_date_then_created_at_then_id_desc(self):
        entries = [
            {"id": "a", "date": "2026-11-01", "created_at": "2026-11-01T10:00:00+08:00"},
            {"id": "b", "date": "2026-11-02", "created_at": "2026-11-02T09:00:00+08:00"},
            {"id": "c", "date": "2026-11-01", "created_at": "2026-11-01T12:00:00+08:00"},
            {"id": "d", "date": "2026-11-01", "created_at": "2026-11-01T12:00:00+08:00"},
        ]
        ordered = [entry["id"] for entry in models.sort_entries_newest_first(entries)]
        self.assertEqual(ordered, ["b", "d", "c", "a"])

    def test_sort_does_not_mutate_input(self):
        entries = [
            {"id": "a", "date": "2026-11-01", "created_at": "2026-11-01T10:00:00+08:00"},
            {"id": "b", "date": "2026-11-02", "created_at": "2026-11-02T09:00:00+08:00"},
        ]
        models.sort_entries_newest_first(entries)
        self.assertEqual([entry["id"] for entry in entries], ["a", "b"])


class FormattingTests(unittest.TestCase):
    def test_format_cents(self):
        self.assertEqual(models.format_cents(4550), "45.50")
        self.assertEqual(models.format_cents(45), "0.45")
        self.assertEqual(models.format_cents(50000), "500.00")
        self.assertEqual(models.format_cents(0), "0.00")
        self.assertEqual(models.format_cents(-1500), "-15.00")
        self.assertEqual(models.format_cents(None), "0.00")

    def test_finders(self):
        ledger = example_ledger()
        self.assertEqual(models.find_entry(ledger, "entry-1")["amount_cents"], 4500)
        self.assertIsNone(models.find_entry(ledger, "missing"))
        self.assertEqual(models.find_category(ledger, "food")["name"], "饮食")
        self.assertEqual(models.category_name(ledger, "food"), "饮食")
        self.assertEqual(models.category_name(ledger, "gone"), "（已删除类别）")


class LedgerValidationTests(unittest.TestCase):
    def test_example_ledger_is_valid(self):
        self.assertEqual(models.validate_ledger_payload(example_ledger()), [])

    def test_new_ledger_is_valid(self):
        self.assertEqual(
            models.validate_ledger_payload(
                models.new_ledger(
                    [{"id": "food", "name": "饮食", "type": "expense", "active": True}]
                )
            ),
            [],
        )

    def test_rejects_non_object_root(self):
        self.assertTrue(models.validate_ledger_payload([]))

    def test_detects_wrong_schema_version(self):
        ledger = example_ledger()
        ledger["schema_version"] = 2
        self.assertIn("schema_version", "；".join(models.validate_ledger_payload(ledger)))

    def test_detects_duplicate_entry_id(self):
        ledger = example_ledger()
        ledger["entries"].append(copy.deepcopy(ledger["entries"][0]))
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("重复" in problem for problem in problems), problems)

    def test_detects_unknown_category_reference(self):
        ledger = example_ledger()
        ledger["entries"][0]["category_id"] = "night_snack"
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("不存在的类别" in problem for problem in problems), problems)

    def test_detects_category_type_mismatch(self):
        ledger = example_ledger()
        ledger["entries"][0]["category_id"] = "salary"  # 支出却引用了收入类别
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("不一致" in problem for problem in problems), problems)

    def test_detects_zero_negative_and_bool_amounts(self):
        for amount in (0, -1, True, "4500"):
            ledger = example_ledger()
            ledger["entries"][0]["amount_cents"] = amount
            with self.subTest(amount=amount):
                self.assertTrue(models.validate_ledger_payload(ledger))

    def test_detects_bad_entry_date(self):
        ledger = example_ledger()
        ledger["entries"][0]["date"] = "2026-02-30"
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("日期" in problem for problem in problems), problems)

    def test_detects_duplicate_category_name_within_same_type(self):
        ledger = example_ledger()
        ledger["categories"].append(
            {"id": "food2", "name": " 饮食 ", "type": "expense", "active": True}
        )
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("重复" in problem for problem in problems), problems)

    def test_same_name_in_different_types_is_allowed(self):
        ledger = example_ledger()
        ledger["categories"].append(
            {"id": "other_income", "name": "其他", "type": "income", "active": True}
        )
        ledger["categories"].append(
            {"id": "other_expense", "name": "其他", "type": "expense", "active": True}
        )
        self.assertEqual(models.validate_ledger_payload(ledger), [])

    def test_detects_bad_budget_month(self):
        ledger = example_ledger()
        ledger["budgets"]["2026-13"] = {"total_cents": 100}
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("月份" in problem for problem in problems), problems)

    def test_detects_budget_for_income_category(self):
        ledger = example_ledger()
        ledger["budgets"]["2026-11"]["category_limits"]["salary"] = 50000
        problems = models.validate_ledger_payload(ledger)
        self.assertTrue(any("支出类别" in problem for problem in problems), problems)

    def test_assert_valid_ledger_raises_with_all_problems(self):
        ledger = example_ledger()
        ledger["entries"][0]["amount_cents"] = 0
        ledger["entries"][0]["date"] = "2026-02-30"
        with self.assertRaises(models.LedgerFormatError) as ctx:
            models.assert_valid_ledger(ledger)
        self.assertGreaterEqual(len(ctx.exception.problems), 2)


if __name__ == "__main__":
    unittest.main()