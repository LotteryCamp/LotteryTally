"""Contract tests for the shared ledger filtering module.

Run from the repository root with ``python -m unittest discover -s tests``.
The data below is fictional and intentionally independent of storage and Flask.
"""

from copy import deepcopy
from datetime import date
import unittest

from filters import (
    FilterValidationError,
    filter_entries,
    filters_to_query,
    parse_filters,
    sort_entries,
)


TODAY = date(2026, 11, 7)


def entry(entry_id, day="2026-11-07", amount=4500, category="food",
          kind="expense", note="晚餐", created="2026-11-07T12:00:00+08:00"):
    return {
        "id": entry_id,
        "date": day,
        "type": kind,
        "amount_cents": amount,
        "category_id": category,
        "note": note,
        "created_at": created,
        "updated_at": created,
    }


def ids(entries):
    return [record["id"] for record in entries]


class ParseFiltersTests(unittest.TestCase):
    def test_empty_query_has_unrestricted_criteria_and_default_order(self):
        criteria = parse_filters({}, TODAY)
        for key in ("start_date", "end_date", "category_id", "type",
                    "min_amount_cents", "max_amount_cents"):
            self.assertIsNone(criteria[key], key)
        self.assertEqual(criteria["q"], "")
        self.assertEqual(criteria["preset"], "")
        self.assertEqual(criteria["sort"], "date_desc")

    def test_blank_controls_are_unrestricted(self):
        criteria = parse_filters({
            "start_date": " ", "end_date": "", "category_id": " ",
            "type": "", "min_amount": " ", "max_amount": "",
            "q": "  ", "sort": "", "preset": "",
        }, TODAY)
        self.assertEqual(criteria, parse_filters({}, TODAY))

    def test_parses_and_trims_all_conditions_without_mutating_query(self):
        query = {
            "start_date": " 2026-11-01 ", "end_date": "2026-11-30",
            "category_id": " food ", "type": " expense ",
            "min_amount": " 30.5 ", "max_amount": "50",
            "q": " 晚餐 ", "sort": "amount_desc", "page": "2",
        }
        original = deepcopy(query)
        criteria = parse_filters(query, TODAY)
        self.assertEqual(criteria["start_date"], "2026-11-01")
        self.assertEqual(criteria["end_date"], "2026-11-30")
        self.assertEqual(criteria["category_id"], "food")
        self.assertEqual(criteria["type"], "expense")
        self.assertEqual(criteria["min_amount_cents"], 3050)
        self.assertEqual(criteria["max_amount_cents"], 5000)
        self.assertEqual(criteria["q"], "晚餐")
        self.assertEqual(criteria["sort"], "amount_desc")
        self.assertNotIn("page", criteria)
        self.assertEqual(query, original)

    def test_amount_conversion_is_exact_in_integer_cents(self):
        for text, cents in (("45", 4500), ("45.5", 4550),
                            ("45.50", 4550), ("0.01", 1), ("0.29", 29)):
            with self.subTest(text=text):
                criteria = parse_filters({"min_amount": text}, TODAY)
                self.assertEqual(criteria["min_amount_cents"], cents)
                self.assertIs(type(criteria["min_amount_cents"]), int)

    def test_rejects_invalid_amount_syntax_and_nonpositive_amounts(self):
        for text in ("0", "0.00", "-1", "12.345", "1e2", "NaN",
                     "Infinity", "abc", "1,000", "+1"):
            for field in ("min_amount", "max_amount"):
                with self.subTest(text=text, field=field):
                    with self.assertRaises(FilterValidationError) as caught:
                        parse_filters({field: text}, TODAY)
                    self.assertIn(field, caught.exception.errors)

    def test_rejects_invalid_calendar_dates_and_non_iso_formats(self):
        for text in ("2026-02-29", "2026-11-31", "2026-13-01",
                     "2026-1-01", "20261107", "2026-11-07T00:00:00"):
            for field in ("start_date", "end_date"):
                with self.subTest(text=text, field=field):
                    with self.assertRaises(FilterValidationError) as caught:
                        parse_filters({field: text}, TODAY)
                    self.assertIn(field, caught.exception.errors)

    def test_accepts_leap_day_and_equal_range_endpoints(self):
        criteria = parse_filters({
            "start_date": "2024-02-29", "end_date": "2024-02-29",
            "min_amount": "45.50", "max_amount": "45.5",
        }, TODAY)
        self.assertEqual(criteria["start_date"], criteria["end_date"])
        self.assertEqual(criteria["min_amount_cents"], criteria["max_amount_cents"])

    def test_rejects_reversed_date_range(self):
        with self.assertRaises(FilterValidationError) as caught:
            parse_filters({"start_date": "2026-11-08", "end_date": "2026-11-07"}, TODAY)
        self.assertTrue({"start_date", "end_date"} & set(caught.exception.errors))

    def test_rejects_reversed_amount_range(self):
        with self.assertRaises(FilterValidationError) as caught:
            parse_filters({"min_amount": "50", "max_amount": "30"}, TODAY)
        self.assertTrue({"min_amount", "max_amount"} & set(caught.exception.errors))

    def test_collects_independent_field_errors(self):
        with self.assertRaises(FilterValidationError) as caught:
            parse_filters({
                "start_date": "2026-02-29", "end_date": "bad-date",
                "min_amount": "-1", "max_amount": "12.345",
                "type": "transfer", "sort": "random",
            }, TODAY)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertTrue({"start_date", "end_date", "min_amount", "max_amount",
                         "type", "sort"}.issubset(caught.exception.errors))
        for message in caught.exception.errors.values():
            self.assertIsInstance(message, str)
            self.assertTrue(message)
            self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in message))

    def test_rejects_unknown_presets(self):
        with self.assertRaises(FilterValidationError) as caught:
            parse_filters({"preset": "last_100_days"}, TODAY)
        self.assertIn("preset", caught.exception.errors)

    def test_accepts_both_entry_types_and_each_documented_order(self):
        for kind in ("income", "expense"):
            for order in ("date_desc", "date_asc", "amount_asc", "amount_desc"):
                with self.subTest(kind=kind, order=order):
                    criteria = parse_filters({"type": kind, "sort": order}, TODAY)
                    self.assertEqual(criteria["type"], kind)
                    self.assertEqual(criteria["sort"], order)


class PresetTests(unittest.TestCase):
    def assert_range(self, preset, today, start, end):
        criteria = parse_filters({"preset": preset}, today)
        self.assertEqual(criteria["start_date"], start)
        self.assertEqual(criteria["end_date"], end)
        self.assertEqual(criteria["preset"], preset)

    def test_today_uses_supplied_ledger_date(self):
        self.assert_range("today", TODAY, "2026-11-07", "2026-11-07")

    def test_this_week_is_monday_through_sunday(self):
        for today in (date(2026, 11, 2), date(2026, 11, 7), date(2026, 11, 8)):
            with self.subTest(today=today):
                self.assert_range("this_week", today, "2026-11-02", "2026-11-08")

    def test_this_week_crosses_year_boundary(self):
        self.assert_range("this_week", date(2027, 1, 1), "2026-12-28", "2027-01-03")

    def test_this_month_includes_future_days_and_calendar_month_end(self):
        self.assert_range("this_month", TODAY, "2026-11-01", "2026-11-30")
        self.assert_range("this_month", date(2024, 2, 3), "2024-02-01", "2024-02-29")
        self.assert_range("this_month", date(2026, 12, 31), "2026-12-01", "2026-12-31")

    def test_last_month_handles_year_change_and_february(self):
        self.assert_range("last_month", date(2026, 1, 15), "2025-12-01", "2025-12-31")
        self.assert_range("last_month", date(2024, 3, 31), "2024-02-01", "2024-02-29")
        self.assert_range("last_month", date(2025, 3, 31), "2025-02-01", "2025-02-28")

    def test_both_explicit_dates_override_preset(self):
        criteria = parse_filters({
            "preset": "last_month", "start_date": "2026-11-06",
            "end_date": "2026-11-07",
        }, TODAY)
        self.assertEqual(criteria["start_date"], "2026-11-06")
        self.assertEqual(criteria["end_date"], "2026-11-07")
        self.assertEqual(criteria["preset"], "")

    def test_single_explicit_date_overrides_preset_without_filling_other_side(self):
        for field, missing in (("start_date", "end_date"), ("end_date", "start_date")):
            with self.subTest(field=field):
                criteria = parse_filters({"preset": "last_month", field: "2026-11-07"}, TODAY)
                self.assertEqual(criteria[field], "2026-11-07")
                self.assertIsNone(criteria[missing])
                self.assertEqual(criteria["preset"], "")

    def test_blank_explicit_controls_do_not_disable_preset(self):
        criteria = parse_filters({"preset": "this_month", "start_date": "", "end_date": " "}, TODAY)
        self.assertEqual(criteria["start_date"], "2026-11-01")
        self.assertEqual(criteria["end_date"], "2026-11-30")


class FilterEntriesTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            entry("lower", "2026-11-01", 3000),
            entry("middle", "2026-11-07", 4500),
            entry("upper", "2026-11-30", 5000),
            entry("next-month", "2026-12-01", 4000),
            entry("other-category", amount=4500, category="traffic"),
            entry("income", amount=4500, category="salary", kind="income"),
            entry("too-small", amount=2999),
            entry("too-large", amount=5001),
            entry("other-note", "2026-11-06", 4000, note="早餐"),
        ]

    def test_all_conditions_are_combined_with_and_and_include_boundaries(self):
        criteria = parse_filters({
            "preset": "this_month", "category_id": "food", "type": "expense",
            "min_amount": "30", "max_amount": "50", "q": "晚餐",
        }, TODAY)
        self.assertEqual(ids(filter_entries(self.records, criteria)), ["lower", "middle", "upper"])

    def test_unrestricted_filter_returns_complete_new_list_without_mutation(self):
        before = deepcopy(self.records)
        criteria = parse_filters({}, TODAY)
        criteria_before = deepcopy(criteria)
        result = filter_entries(self.records, criteria)
        self.assertEqual(result, self.records)
        self.assertIsNot(result, self.records)
        self.assertEqual(self.records, before)
        self.assertEqual(criteria, criteria_before)

    def test_single_date_boundary_is_open_on_the_other_side(self):
        start_only = parse_filters({"start_date": "2026-11-30"}, TODAY)
        end_only = parse_filters({"end_date": "2026-11-01"}, TODAY)
        self.assertEqual(ids(filter_entries(self.records, start_only)), ["upper", "next-month"])
        self.assertEqual(ids(filter_entries(self.records, end_only)), ["lower"])

    def test_single_amount_boundary_and_one_cent_precision(self):
        records = [entry("small", amount=28), entry("exact", amount=29), entry("large", amount=30)]
        lower = parse_filters({"min_amount": "0.29"}, TODAY)
        upper = parse_filters({"max_amount": "0.29"}, TODAY)
        self.assertEqual(ids(filter_entries(records, lower)), ["exact", "large"])
        self.assertEqual(ids(filter_entries(records, upper)), ["small", "exact"])

    def test_type_can_select_income(self):
        criteria = parse_filters({"type": "income"}, TODAY)
        self.assertEqual(ids(filter_entries(self.records, criteria)), ["income"])

    def test_category_is_exact_and_does_not_require_an_active_category(self):
        records = [entry("old", category="retired-food"), entry("similar", category="food")]
        criteria = parse_filters({"category_id": "retired-food"}, TODAY)
        self.assertEqual(ids(filter_entries(records, criteria)), ["old"])
        self.assertEqual(filter_entries(records, parse_filters({"category_id": "retired"}, TODAY)), [])

    def test_note_search_is_case_sensitive_literal_substring(self):
        records = [entry("literal", note="报销 A+B [餐费]"),
                   entry("lower", note="报销 a+b [餐费]"),
                   entry("regex-like", note="报销 AAAAB 餐费")]
        self.assertEqual(ids(filter_entries(records, parse_filters({"q": " A+B "}, TODAY))), ["literal"])
        self.assertEqual(ids(filter_entries(records, parse_filters({"q": "[餐费]"}, TODAY))), ["literal", "lower"])

    def test_filter_preserves_input_order_and_does_not_paginate(self):
        records = [entry(str(number), amount=100 + number) for number in range(47)]
        criteria = parse_filters({"type": "expense", "sort": "amount_desc", "page": "2"}, TODAY)
        result = filter_entries(records, criteria)
        self.assertEqual(ids(result), [str(number) for number in range(47)])
        self.assertEqual(sum(record["amount_cents"] for record in result), 5781)

    def test_empty_or_unmatched_inputs_return_empty_list(self):
        self.assertEqual(filter_entries([], parse_filters({}, TODAY)), [])
        self.assertEqual(filter_entries(self.records, parse_filters({"q": "不存在的备注"}, TODAY)), [])


class SortEntriesTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            entry("a", amount=100, created="2026-11-07T12:00:00+08:00"),
            entry("b", amount=200, created="2026-11-07T05:00:00+00:00"),
            entry("c", amount=200, created="2026-11-07T13:00:00+08:00"),
            entry("d", "2026-11-08", 150, created="2026-11-07T01:00:00+00:00"),
            entry("e", "2026-11-06", 100, created="2026-11-08T01:00:00+00:00"),
        ]

    def test_date_desc_uses_ledger_date_then_actual_creation_time_then_id(self):
        self.assertEqual(ids(sort_entries(self.records, "date_desc")), ["d", "c", "b", "a", "e"])

    def test_date_asc_retains_newest_first_within_the_same_day(self):
        self.assertEqual(ids(sort_entries(self.records, "date_asc")), ["e", "c", "b", "a", "d"])

    def test_amount_ascending_uses_default_order_for_ties(self):
        self.assertEqual(ids(sort_entries(self.records, "amount_asc")), ["a", "e", "d", "c", "b"])

    def test_amount_descending_uses_default_order_for_ties(self):
        self.assertEqual(ids(sort_entries(self.records, "amount_desc")), ["c", "b", "d", "a", "e"])

    def test_sort_does_not_mutate_input_or_entry_data(self):
        before = deepcopy(self.records)
        for order in ("date_desc", "date_asc", "amount_asc", "amount_desc"):
            with self.subTest(order=order):
                result = sort_entries(self.records, order)
                self.assertIsNot(result, self.records)
                self.assertEqual(self.records, before)

    def test_empty_input_is_supported(self):
        for order in ("date_desc", "date_asc", "amount_asc", "amount_desc"):
            with self.subTest(order=order):
                self.assertEqual(sort_entries([], order), [])


class QueryReuseTests(unittest.TestCase):
    def test_default_query_only_needs_sort(self):
        self.assertEqual(filters_to_query(parse_filters({}, TODAY)), {"sort": "date_desc"})

    def test_serializes_effective_parameters_in_yuan_and_omits_page_and_preset(self):
        criteria = parse_filters({
            "preset": "this_month", "category_id": "food", "type": "expense",
            "min_amount": "30", "max_amount": "45.5", "q": " 晚餐 ",
            "sort": "amount_desc", "page": "3",
        }, TODAY)
        before = deepcopy(criteria)
        query = filters_to_query(criteria)
        self.assertEqual(query, {
            "start_date": "2026-11-01", "end_date": "2026-11-30",
            "category_id": "food", "type": "expense", "min_amount": "30.00",
            "max_amount": "45.50", "q": "晚餐", "sort": "amount_desc",
        })
        self.assertEqual(criteria, before)

    def test_preserves_open_date_ranges(self):
        criteria = parse_filters({"preset": "this_month", "start_date": "2026-11-07"}, TODAY)
        self.assertEqual(filters_to_query(criteria), {"start_date": "2026-11-07", "sort": "date_desc"})

    def test_export_conditions_remain_same_when_reparsed_in_another_month(self):
        criteria = parse_filters({"preset": "this_month", "min_amount": "0.29", "q": "晚餐"}, TODAY)
        query = filters_to_query(criteria)
        reparsed = parse_filters(query, date(2026, 12, 3))
        records = [entry("november", amount=29), entry("december", "2026-12-03", 29)]
        self.assertEqual(ids(filter_entries(records, criteria)), ["november"])
        self.assertEqual(filter_entries(records, reparsed), filter_entries(records, criteria))

    def test_full_filter_sort_and_reuse_pipeline_keeps_all_matching_records(self):
        records = [entry(str(number), amount=3000 + number) for number in range(45)]
        records += [entry("wrong-month", "2026-10-31", 3020), entry("wrong-category", category="traffic")]
        criteria = parse_filters({
            "preset": "this_month", "category_id": "food", "type": "expense",
            "min_amount": "30", "max_amount": "30.44", "q": "晚餐",
            "sort": "amount_desc", "page": "2",
        }, TODAY)
        matched = filter_entries(records, criteria)
        ordered = sort_entries(matched, criteria["sort"])
        self.assertEqual(ids(ordered), [str(number) for number in range(44, -1, -1)])
        self.assertEqual(len(ordered), 45)
        self.assertEqual(sum(record["amount_cents"] for record in matched), 135990)
        reused = parse_filters(filters_to_query(criteria), date(2026, 12, 1))
        self.assertEqual(sort_entries(filter_entries(records, reused), reused["sort"]), ordered)


if __name__ == "__main__":
    unittest.main()
