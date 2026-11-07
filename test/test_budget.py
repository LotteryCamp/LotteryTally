"""budget.py 的单元测试：验收用例 T08/T09/T10，以及预算口径边界与页面流程。"""

from __future__ import annotations

import unittest

import support  # noqa: F401  # 安装存储/校验契约替身
from support import LedgerTestCase, storage

from lotterytally import budget as budget_module
from lotterytally import entries as entry_service
from lotterytally import models

MONTH = "2025-03"
PREV_MONTH = "2025-02"


class BudgetTestBase(LedgerTestCase):
    """预先放好：工资 3000 元 + 饮食 470 元（都在 2025-03）。"""

    def setUp(self):
        super().setUp()
        self.add_entry("income", "3000", f"{MONTH}-01", "salary", "工资")
        self.food_entry = self.add_entry("expense", "470", f"{MONTH}-03", "food", "本月餐饮")

    def status(self, month=MONTH):
        return budget_module.budget_status(self.load(), month)

    @staticmethod
    def line(status, category_id):
        for item in status["categories"]:
            if item["category_id"] == category_id:
                return item
        raise AssertionError(f"预算结果里没有类别 {category_id}")


class BudgetStatusTests(BudgetTestBase):
    def test_only_expenses_of_that_month_are_counted(self):
        self.add_entry("expense", "100", f"{PREV_MONTH}-28", "food", "上月餐饮")
        self.set_budget(MONTH, 150000, {"food": 50000})
        status = self.status()
        self.assertEqual(status["expense_cents"], 47000)
        self.assertEqual(status["expense_count"], 1)
        self.assertEqual(status["total"]["used_cents"], 47000)
        self.assertEqual(status["total"]["remaining_cents"], 103000)

    def test_income_never_enters_expense_budget(self):
        self.set_budget(MONTH, 150000, {})
        status = self.status()
        self.assertEqual(status["expense_cents"], 47000)  # 3000 元工资不参与
        self.assertEqual(status["total"]["remaining_cents"], 103000)

    def test_T08_over_budget_after_new_expense(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        food = self.line(self.status(), "food")
        self.assertEqual(food["used_cents"], 47000)
        self.assertEqual(food["status"], budget_module.STATUS_OK)
        self.assertEqual(food["remaining_cents"], 3000)
        self.assertEqual(food["status_text"], "剩余 30.00 元")
        self.assertEqual(food["percent"], 94)

        self.add_entry("expense", "45", f"{MONTH}-05", "food", "晚餐")

        food = self.line(self.status(), "food")
        self.assertEqual(food["used_cents"], 51500)
        self.assertEqual(food["status"], budget_module.STATUS_OVER)
        self.assertEqual(food["overspent_cents"], 1500)
        self.assertEqual(food["remaining_cents"], 0)
        self.assertEqual(food["status_text"], "超支 15.00 元")
        self.assertEqual(food["percent"], 103)
        self.assertEqual(food["bar_percent"], 100)  # 进度条封顶 100%

    def test_total_and_category_limits_are_independent(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        self.add_entry("expense", "45", f"{MONTH}-05", "food", "晚餐")
        status = self.status()
        self.assertEqual(status["total"]["status"], budget_module.STATUS_OK)
        self.assertEqual(status["total"]["remaining_cents"], 150000 - 51500)
        self.assertEqual(self.line(status, "food")["status"], budget_module.STATUS_OVER)

    def test_exact_limit_reports_exhausted(self):
        self.set_budget(MONTH, None, {"food": 47000})
        food = self.line(self.status(), "food")
        self.assertEqual(food["status"], budget_module.STATUS_EXHAUSTED)
        self.assertEqual(food["status_text"], "额度已用完")
        self.assertEqual(food["remaining_cents"], 0)
        self.assertEqual(food["overspent_cents"], 0)
        self.assertEqual(food["percent"], 100)

    def test_unset_budget_still_reports_used_amount(self):
        status = self.status()  # 一条预算都没设置
        self.assertFalse(status["total"]["has_limit"])
        self.assertEqual(status["total"]["status"], budget_module.STATUS_UNSET)
        self.assertEqual(status["total"]["used_cents"], 47000)
        self.assertEqual(status["budgeted_count"], 0)
        self.assertEqual(status["unbudgeted_count"], 1)
        self.assertEqual(status["unbudgeted_cents"], 47000)

    def test_T09_edit_amount_recalculates_budget(self):
        extra = self.add_entry("expense", "45", f"{MONTH}-05", "food", "晚餐")
        self.set_budget(MONTH, 150000, {"food": 50000})
        entry_service.update_entry(
            extra["id"],
            {
                "type": "expense",
                "amount": "20",
                "date": f"{MONTH}-05",
                "category_id": "food",
                "note": "晚餐",
            },
        )
        food = self.line(self.status(), "food")
        self.assertEqual(food["used_cents"], 49000)
        self.assertEqual(food["remaining_cents"], 1000)
        self.assertEqual(food["status_text"], "剩余 10.00 元")

    def test_T10_move_entry_to_previous_month(self):
        extra = self.add_entry("expense", "20", f"{MONTH}-05", "food", "晚餐")
        self.set_budget(MONTH, 150000, {"food": 50000})
        entry_service.update_entry(
            extra["id"],
            {
                "type": "expense",
                "amount": "20",
                "date": f"{PREV_MONTH}-20",
                "category_id": "food",
                "note": "晚餐",
            },
        )
        current = self.status()
        self.assertEqual(current["expense_cents"], 47000)
        self.assertEqual(self.line(current, "food")["remaining_cents"], 3000)

        previous = self.status(PREV_MONTH)
        self.assertEqual(previous["expense_cents"], 2000)
        self.assertEqual(previous["expense_count"], 1)

    def test_delete_entry_recalculates_budget(self):
        self.set_budget(MONTH, None, {"food": 50000})
        entry_service.delete_entry(self.food_entry["id"])
        food = self.line(self.status(), "food")
        self.assertEqual(food["used_cents"], 0)
        self.assertEqual(food["status_text"], "剩余 500.00 元")

    def test_inactive_category_is_still_counted_and_listed(self):
        storage.update_ledger(
            lambda ledger: models.find_category(ledger, "food").update({"active": False})
        )
        self.set_budget(MONTH, None, {"food": 50000})
        food = self.line(self.status(), "food")
        self.assertFalse(food["active"])
        self.assertEqual(food["used_cents"], 47000)
        self.assertEqual(food["status"], budget_module.STATUS_OK)

    def test_budgeted_categories_come_first(self):
        self.set_budget(MONTH, None, {"transport": 20000})
        categories = self.status()["categories"]
        self.assertEqual(categories[0]["category_id"], "transport")
        self.assertTrue(all(not item["has_limit"] for item in categories[1:]))

    def test_unknown_month_falls_back_to_current_month(self):
        status = budget_module.budget_status(self.load(), "2025-13")
        self.assertEqual(status["month"], models.current_month())
        self.assertEqual(status["expense_cents"], 0)

    def test_month_label_is_ready_for_template(self):
        self.assertEqual(self.status()["month_label"], "2025 年 3 月")


class BudgetSaveTests(LedgerTestCase):
    def test_saves_total_and_category_limits(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        self.assertEqual(
            self.budgets()[MONTH],
            {"total_cents": 150000, "category_limits": {"food": 50000}},
        )

    def test_none_clears_single_limit(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        self.set_budget(MONTH, 200000, {"food": None})
        self.assertEqual(self.budgets()[MONTH], {"total_cents": 200000})

    def test_clearing_everything_removes_the_month(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        self.set_budget(MONTH, None, {"food": None})
        self.assertNotIn(MONTH, self.budgets())

    def test_other_months_are_untouched(self):
        self.set_budget(PREV_MONTH, 100000, {"food": 30000})
        self.set_budget(MONTH, 150000, {"food": 50000})
        self.set_budget(MONTH, None, {"food": None})
        self.assertEqual(
            self.budgets()[PREV_MONTH],
            {"total_cents": 100000, "category_limits": {"food": 30000}},
        )

    def test_total_only_budget_is_allowed(self):
        self.set_budget(MONTH, 150000)
        self.assertEqual(self.budgets()[MONTH], {"total_cents": 150000})

    def test_save_budget_rejects_bad_month(self):
        with self.assertRaises(budget_module.BudgetError) as ctx:
            budget_module.save_budget("2025-13", 1000, {})
        self.assertIn("month", ctx.exception.errors)


class BudgetFormTests(BudgetTestBase):
    def test_accepts_yuan_text_and_blank_to_clear(self):
        month, total_cents, limits = budget_module.parse_budget_form(
            {"month": MONTH, "total": "1500", "limit_food": "500", "limit_transport": ""},
            self.load(),
        )
        self.assertEqual(month, MONTH)
        self.assertEqual(total_cents, 150000)
        self.assertEqual(limits, {"food": 50000, "transport": None})

    def test_rejects_zero_and_text_amounts(self):
        with self.assertRaises(budget_module.BudgetError) as ctx:
            budget_module.parse_budget_form({"month": MONTH, "total": "0"}, self.load())
        self.assertIn("total", ctx.exception.errors)

        with self.assertRaises(budget_module.BudgetError) as ctx:
            budget_module.parse_budget_form(
                {"month": MONTH, "total": "1500", "limit_food": "abc"}, self.load()
            )
        self.assertIn("limit_food", ctx.exception.errors)

    def test_rejects_bad_month(self):
        with self.assertRaises(budget_module.BudgetError) as ctx:
            budget_module.parse_budget_form({"month": "2025-13", "total": "1500"}, self.load())
        self.assertIn("month", ctx.exception.errors)

    def test_rejects_income_category_budget_field(self):
        with self.assertRaises(budget_module.BudgetError) as ctx:
            budget_module.parse_budget_form(
                {"month": MONTH, "total": "1500", "limit_salary": "100"}, self.load()
            )
        self.assertIn("limit_salary", ctx.exception.errors)


class BudgetPageTests(BudgetTestBase):
    def test_page_renders_current_budget(self):
        self.set_budget(MONTH, 150000, {"food": 50000})
        response = self.flask_client().get(f"/budget?month={MONTH}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("总预算", html)
        self.assertIn("饮食", html)
        self.assertIn("剩余 30.00 元", html)
        self.assertIn('value="1500.00"', html)
        self.assertIn('value="500.00"', html)

    def test_page_defaults_to_current_month(self):
        html = self.flask_client().get("/budget").get_data(as_text=True)
        self.assertIn(models.month_label(models.current_month()), html)

    def test_bad_month_falls_back_with_warning(self):
        response = self.flask_client().get("/budget?month=2025-13")
        self.assertEqual(response.status_code, 200)
        self.assertIn("月份格式应为", response.get_data(as_text=True))

    def test_post_saves_and_redirects(self):
        client = self.flask_client()
        response = client.post(
            "/budget",
            data={"month": MONTH, "total": "1500", "limit_food": "500", "limit_transport": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/budget?month={MONTH}", response.headers["Location"])
        self.assertEqual(self.budgets()[MONTH]["total_cents"], 150000)
        self.assertEqual(self.budgets()[MONTH]["category_limits"], {"food": 50000})

    def test_post_invalid_amount_keeps_input(self):
        client = self.flask_client()
        response = client.post("/budget", data={"month": MONTH, "total": "0", "limit_food": ""})
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("金额必须大于 0", html)
        self.assertIn('value="0"', html)  # 保留用户输入
        self.assertNotIn(MONTH, self.budgets())

    def test_post_income_category_field_is_rejected(self):
        client = self.flask_client()
        response = client.post(
            "/budget", data={"month": MONTH, "total": "1500", "limit_salary": "100"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("无效的支出类别", response.get_data(as_text=True))
        self.assertNotIn(MONTH, self.budgets())


if __name__ == "__main__":
    unittest.main()