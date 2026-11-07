"""validators.py 单元测试。

运行方式（在项目根目录）：

    python -m unittest discover -s test -v

或者直接跑本文件：

    python test/test_validators.py

覆盖范围：
    金额转换（合法口径、拒绝口径、精度、大金额、空值）
    筛选金额区间（含最小/最大颠倒）
    日期（真实日历日、格式、年份区间）
    收支类型（大小写容错、非法值）
    备注（去空白、长度上限、控制字符）
    预算月份
    账目整体校验（多字段同时报错、类别类型一致、停用类别放行规则）
    元/分格式化
"""

import doctest
import os
import sys
import unittest

# 让 test/ 下的测试可以直接 import 包内模块，
# 不依赖调用者的工作目录，也不依赖 test 是不是包。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from lotterytally import validators  # noqa: E402
from lotterytally.validators import (  # noqa: E402
    MAX_NOTE_LENGTH,
    ValidationError,
    format_cents,
    normalize_type,
    parse_amount_cents,
    parse_amount_range,
    parse_budget_limit,
    parse_date,
    parse_type_filter,
    validate_entry_fields,
    validate_month,
    validate_note,
)


def load_tests(loader, tests, ignore):
    """把 validators 里的 doctest 也纳入同一套 unittest 运行。"""
    tests.addTests(doctest.DocTestSuite(validators))
    return tests


class AmountCentsTest(unittest.TestCase):
    """金额文本 -> 整数分。"""

    def test_accepts_plain_integer(self):
        self.assertEqual(parse_amount_cents("45"), 4500)

    def test_accepts_one_decimal(self):
        self.assertEqual(parse_amount_cents("45.5"), 4550)

    def test_accepts_two_decimals(self):
        self.assertEqual(parse_amount_cents("45.50"), 4550)
        self.assertEqual(parse_amount_cents("45.05"), 4505)

    def test_smallest_positive_amount(self):
        # 一分钱也要能记，不能因为整数化被抹成 0
        self.assertEqual(parse_amount_cents("0.01"), 1)

    def test_strips_surrounding_spaces(self):
        self.assertEqual(parse_amount_cents("  45.50  "), 4550)

    def test_accepts_int_and_float_input(self):
        self.assertEqual(parse_amount_cents(45), 4500)
        self.assertEqual(parse_amount_cents(45.5), 4550)

    def test_decimal_conversion_is_exact(self):
        # 用 float 计算 0.29 * 100 会得到 28.999999999999996，这里必须是精确的 29
        self.assertEqual(parse_amount_cents("0.29"), 29)
        self.assertEqual(parse_amount_cents("1.15"), 115)
        self.assertEqual(parse_amount_cents("8.20"), 820)

    def test_large_amount_keeps_every_cent(self):
        self.assertEqual(
            parse_amount_cents("123456789012345678901234567890.99"),
            12345678901234567890123456789099,
        )

    def test_accepts_ten_digit_amount(self):
        # 与 filters.py 的口径对齐：那边不限整数位，这边不能更窄，
        # 否则同一个金额在筛选页能填、在记账页被拒
        self.assertEqual(parse_amount_cents("1000000000"), 100_000_000_000)
        self.assertEqual(parse_amount_cents("999999999999.99"), 99_999_999_999_999)

    def test_no_unagreed_business_amount_cap(self):
        self.assertEqual(parse_amount_cents("1000000000000"), 100_000_000_000_000)

    def test_returns_plain_int(self):
        # filters.py 用 type(cents) is not int 做严格判断，不能返回 int 子类
        self.assertIs(type(parse_amount_cents("45.5")), int)

    def test_rejects_zero(self):
        for text in ("0", "0.0", "0.00", "00"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_cents(text)
                self.assertIn("必须大于 0", str(ctx.exception))

    def test_rejects_negative(self):
        for text in ("-1", "-0.01", "-45.50"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_cents(text)
                self.assertIn("负数", str(ctx.exception))

    def test_rejects_more_than_two_decimals(self):
        for text in ("12.345", "0.001", "45.999"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_cents(text)
                self.assertIn("两位小数", str(ctx.exception))

    def test_rejects_scientific_notation(self):
        for text in ("1e3", "1E3", "4.5e2"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_cents(text)
                self.assertIn("科学计数法", str(ctx.exception))

    def test_rejects_thousands_separator(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_cents("1,000")
        self.assertIn("千分位", str(ctx.exception))

    def test_rejects_unit_and_words(self):
        for text in ("45元", "abc", "四十五", "45 元", "45rmb"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    parse_amount_cents(text)

    def test_rejects_full_width_digits(self):
        # Python 的 \d 默认匹配全角数字，必须靠 re.ASCII 拦住
        with self.assertRaises(ValidationError):
            parse_amount_cents("４５")

    def test_rejects_broken_decimal_point(self):
        for text in (".", ".5", "5.", "1.2.3", "1..2"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    parse_amount_cents(text)

    def test_rejects_bool(self):
        # True 不能被当成 1 元悄悄记进去
        with self.assertRaises(ValidationError):
            parse_amount_cents(True)

    def test_rejects_empty_by_default(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_cents(value)
                self.assertIn("请填写金额", str(ctx.exception))

    def test_allow_empty_returns_none(self):
        self.assertIsNone(parse_amount_cents("", allow_empty=True))
        self.assertIsNone(parse_amount_cents(None, allow_empty=True))

    def test_allow_zero_returns_zero(self):
        self.assertEqual(parse_amount_cents("0", allow_empty=True, allow_zero=True), 0)

    def test_error_carries_requested_field(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_cents("", "min_amount")
        self.assertEqual(ctx.exception.field, "min_amount")
        self.assertIn("最小金额", str(ctx.exception))

    def test_budget_total_error_uses_chinese_label(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_cents("0", field="total")
        self.assertEqual(ctx.exception.errors, {"total": "预算金额必须大于 0"})

    def test_category_budget_error_retains_field_name(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_cents("0", field="limit_food")
        self.assertEqual(ctx.exception.errors, {"limit_food": "分类预算金额必须大于 0"})


class AmountRangeTest(unittest.TestCase):
    """筛选页的最小/最大金额。"""

    def test_returns_cents_pair(self):
        self.assertEqual(parse_amount_range("30", "50"), (3000, 5000))

    def test_both_sides_optional(self):
        self.assertEqual(parse_amount_range("", ""), (None, None))
        self.assertEqual(parse_amount_range(None, None), (None, None))
        self.assertEqual(parse_amount_range("30", ""), (3000, None))
        self.assertEqual(parse_amount_range("", "50"), (None, 5000))

    def test_zero_bounds_follow_current_filters_contract(self):
        for minimum, maximum, field in (("0", "", "min_amount"), ("", "0", "max_amount")):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(ValidationError) as ctx:
                    parse_amount_range(minimum, maximum)
                self.assertIn(field, ctx.exception.errors)

    def test_equal_bounds_allowed(self):
        self.assertEqual(parse_amount_range("50", "50"), (5000, 5000))

    def test_rejects_reversed_bounds(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_range("50", "30")
        self.assertIn("max_amount", ctx.exception.errors)
        self.assertIn("最小金额不能大于最大金额", ctx.exception.errors["max_amount"])

    def test_reports_both_sides_at_once(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_amount_range("abc", "-5")
        self.assertEqual(set(ctx.exception.errors), {"min_amount", "max_amount"})


class DateTest(unittest.TestCase):
    """账目日期。"""

    def test_accepts_real_calendar_day(self):
        self.assertEqual(parse_date("2026-11-07"), "2026-11-07")

    def test_accepts_leap_day(self):
        self.assertEqual(parse_date("2024-02-29"), "2024-02-29")

    def test_rejects_leap_day_in_common_year(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_date("2026-02-29")
        self.assertIn("日期不存在", str(ctx.exception))

    def test_rejects_impossible_day(self):
        for text in ("2026-04-31", "2026-13-01", "2026-00-10", "2026-01-00"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    parse_date(text)

    def test_rejects_non_padded_format(self):
        for text in ("2026-1-1", "20261107", "2026/11/07", "07-11-2026"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    parse_date(text)
                self.assertIn("YYYY-MM-DD", str(ctx.exception))

    def test_rejects_year_zero(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_date("0000-12-31")
        self.assertIn("年份", str(ctx.exception))

    def test_full_calendar_range_matches_filters(self):
        for text in ("0001-01-01", "1899-12-31", "3000-01-01", "9999-12-31"):
            with self.subTest(text=text):
                self.assertEqual(parse_date(text), text)

    def test_rejects_empty(self):
        for value in ("", None, "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    parse_date(value)

    def test_tolerates_surrounding_spaces(self):
        self.assertEqual(parse_date(" 2026-11-07 "), "2026-11-07")


class MonthTest(unittest.TestCase):
    """预算月份。"""

    def test_accepts_november(self):
        self.assertEqual(validate_month("2026-11"), "2026-11")

    def test_rejects_month_13(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_month("2026-13")
        self.assertIn("01–12", str(ctx.exception))

    def test_rejects_wrong_format(self):
        for text in ("2026-1", "2026/11", "202611", ""):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    validate_month(text)

    def test_month_of_entry_date(self):
        self.assertEqual(validators.month_of("2026-11-07"), "2026-11")


class TypeTest(unittest.TestCase):
    """收支类型。"""

    def test_accepts_two_types(self):
        self.assertEqual(normalize_type("income"), "income")
        self.assertEqual(normalize_type("expense"), "expense")

    def test_normalizes_case_and_spaces(self):
        self.assertEqual(normalize_type(" Income "), "income")
        self.assertEqual(normalize_type("EXPENSE"), "expense")

    def test_rejects_other_values(self):
        for value in ("", "transfer", "收", "支出", None, "all"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_type(value)

    def test_filter_type_allows_blank(self):
        self.assertIsNone(parse_type_filter(""))
        self.assertIsNone(parse_type_filter(None))
        self.assertEqual(parse_type_filter("income"), "income")

    def test_filter_type_still_rejects_garbage(self):
        with self.assertRaises(ValidationError):
            parse_type_filter("transfer")


class NoteTest(unittest.TestCase):
    """备注。"""

    def test_strips_spaces(self):
        self.assertEqual(validate_note("  晚餐  "), "晚餐")

    def test_blank_note_is_allowed(self):
        self.assertEqual(validate_note(""), "")
        self.assertEqual(validate_note(None), "")

    def test_length_boundary(self):
        self.assertEqual(len(validate_note("账" * MAX_NOTE_LENGTH)), MAX_NOTE_LENGTH)

    def test_rejects_too_long(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_note("账" * (MAX_NOTE_LENGTH + 1))
        self.assertIn(str(MAX_NOTE_LENGTH), str(ctx.exception))

    def test_length_counts_after_strip(self):
        # 前后空格不计入长度
        self.assertEqual(validate_note("  " + "账" * MAX_NOTE_LENGTH + "  "), "账" * MAX_NOTE_LENGTH)

    def test_rejects_control_characters(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_note("晚餐\x07")
        self.assertIn("控制字符", str(ctx.exception))

    def test_allows_newline_and_tab(self):
        self.assertEqual(validate_note("晚餐\n和夜宵\t两笔"), "晚餐\n和夜宵\t两笔")

    def test_normalizes_windows_textarea_line_endings(self):
        self.assertEqual(validate_note("晚餐\r\n和夜宵\r两笔"), "晚餐\n和夜宵\n两笔")

    def test_normalized_note_length_boundary(self):
        note = "账" * (MAX_NOTE_LENGTH - 2) + "\r\n尾"
        self.assertEqual(len(validate_note(note)), MAX_NOTE_LENGTH)


class FormatCentsTest(unittest.TestCase):
    """整数分 -> 展示字符串。"""

    def test_common_values(self):
        self.assertEqual(format_cents(4500), "45.00")
        self.assertEqual(format_cents(4550), "45.50")
        self.assertEqual(format_cents(5), "0.05")
        self.assertEqual(format_cents(0), "0.00")

    def test_negative_value_keeps_sign_first(self):
        self.assertEqual(format_cents(-1550), "-15.50")
        self.assertEqual(format_cents(-1550, with_symbol=True), "-¥15.50")
        self.assertEqual(format_cents(4500, with_symbol=True), "¥45.00")

    def test_rejects_non_integer(self):
        for value in (45.5, "4500", True):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    format_cents(value)


class BudgetLimitTest(unittest.TestCase):
    """预算上限复用金额规则。"""

    def test_parses_limit(self):
        self.assertEqual(parse_budget_limit("500"), 50000)
        self.assertEqual(parse_budget_limit("500.5"), 50050)

    def test_blank_means_no_limit(self):
        self.assertIsNone(parse_budget_limit(""))

    def test_rejects_zero_limit(self):
        with self.assertRaises(ValidationError):
            parse_budget_limit("0")

    def test_same_rules_as_entry_amount(self):
        for text in ("-1", "12.345", "1e3", "abc", "1,000"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    parse_budget_limit(text)


class EntryFieldsTest(unittest.TestCase):
    """账目表单整体校验。"""

    def setUp(self):
        self.categories = [
            {"id": "food", "name": "饮食", "type": "expense", "active": True},
            {"id": "salary", "name": "工资", "type": "income", "active": True},
            {"id": "supper", "name": "夜宵", "type": "expense", "active": False},
        ]
        self.form = {
            "date": "2026-11-07",
            "type": "expense",
            "amount": "45.5",
            "category_id": "food",
            "note": " 晚餐 ",
        }

    def test_returns_cleaned_payload(self):
        cleaned = validate_entry_fields(self.form, self.categories)
        self.assertEqual(
            cleaned,
            {
                "date": "2026-11-07",
                "type": "expense",
                "amount_cents": 4550,
                "category_id": "food",
                "note": "晚餐",
            },
        )

    def test_works_without_categories(self):
        cleaned = validate_entry_fields(self.form)
        self.assertEqual(cleaned["category_id"], "food")

    def test_amount_yuan_alias(self):
        form = dict(self.form)
        form.pop("amount")
        form["amount_yuan"] = "3000"
        cleaned = validate_entry_fields(form, self.categories)
        self.assertEqual(cleaned["amount_cents"], 300000)

    def test_t03_collects_every_field_error_at_once(self):
        # 方案 T03：0、-1、12.345、不存在的日期都要报错，且一次返回全部问题
        form = {"date": "2026-02-30", "type": "", "amount": "12.345", "category_id": ""}
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(form, self.categories)
        self.assertEqual(
            set(ctx.exception.errors), {"date", "type", "amount", "category_id"}
        )

    def test_zero_amount_is_rejected(self):
        form = dict(self.form, amount="0")
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(form, self.categories)
        self.assertIn("amount", ctx.exception.errors)

    def test_rejects_category_of_other_type(self):
        form = dict(self.form, category_id="salary")  # 支出账目选了收入类别
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(form, self.categories)
        self.assertIn("不一致", ctx.exception.errors["category_id"])

    def test_rejects_unknown_category(self):
        form = dict(self.form, category_id="ghost")
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(form, self.categories)
        self.assertIn("不存在", ctx.exception.errors["category_id"])

    def test_rejects_inactive_category_for_new_entry(self):
        # 方案 T12：停用类别不能用于新账
        form = dict(self.form, category_id="supper")
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(form, self.categories)
        self.assertIn("停用", ctx.exception.errors["category_id"])

    def test_keeps_inactive_category_when_editing_same_entry(self):
        # 编辑历史账目时允许保留原停用类别，只改金额也能存
        existing = {"id": "e1", "category_id": "supper"}
        form = dict(self.form, category_id="supper", amount="20")
        cleaned = validate_entry_fields(form, self.categories, existing_entry=existing)
        self.assertEqual(cleaned["category_id"], "supper")
        self.assertEqual(cleaned["amount_cents"], 2000)

    def test_inactive_category_still_rejected_when_editing_to_other_entry(self):
        existing = {"id": "e1", "category_id": "food"}
        form = dict(self.form, category_id="supper")
        with self.assertRaises(ValidationError):
            validate_entry_fields(form, self.categories, existing_entry=existing)

    def test_missing_form_data_does_not_crash(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_entry_fields(None)
        self.assertIn("date", ctx.exception.errors)

    def test_accepts_multidict_like_object(self):
        class FakeMultiDict:
            def __init__(self, data):
                self._data = data

            def get(self, key, default=None):
                return self._data.get(key, default)

        cleaned = validate_entry_fields(FakeMultiDict(self.form), self.categories)
        self.assertEqual(cleaned["amount_cents"], 4550)


class ErrorPayloadTest(unittest.TestCase):
    """错误对象给路由和模板用的形状。"""

    def test_single_field_message(self):
        exc = ValidationError.of("date", "请选择日期")
        self.assertEqual(str(exc), "请选择日期")
        self.assertEqual(exc.field, "date")

    def test_multi_field_message_lists_all(self):
        exc = ValidationError({"date": "请选择日期", "amount": "金额必须大于 0"})
        text = str(exc)
        self.assertIn("请选择日期", text)
        self.assertIn("金额必须大于 0", text)

    def test_to_dict_is_plain_data(self):
        payload = ValidationError({"date": "请选择日期"}).to_dict()
        self.assertEqual(payload["field"], "date")
        self.assertEqual(payload["errors"], {"date": "请选择日期"})

    def test_is_value_error(self):
        # 路由层可以统一 except ValueError，不会漏接
        self.assertTrue(issubclass(ValidationError, ValueError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
