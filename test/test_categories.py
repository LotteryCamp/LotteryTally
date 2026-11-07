"""运行：python -m unittest discover -s test -p "test_categories.py" """

import copy
import unittest

from lotterytally.categories import (
    CategoryError,
    create_category,
    get_default_categories,
    list_categories,
    rename_category,
    set_category_active,
)


class TestCategories(unittest.TestCase):
    def setUp(self):
        self.ledger = {
            "schema_version": 1,
            "categories": get_default_categories(),
            "entries": [
                {
                    "id": "entry-1",
                    "type": "expense",
                    "category_id": "food",
                    "amount_cents": 4500,
                }
            ],
            "budgets": {
                "2026-11": {
                    "total_cents": 100000,
                    "category_limits": {"food": 50000},
                }
            },
        }

    def test_default_categories(self):
        categories = self.ledger["categories"]
        self.assertEqual(
            {c["name"] for c in categories if c["type"] == "income"},
            {"工资", "奖学金", "兼职", "其他"},
        )
        self.assertEqual(
            {c["name"] for c in categories if c["type"] == "expense"},
            {"饮食", "交通", "学习", "生活", "娱乐", "医疗", "其他"},
        )
        self.assertEqual(len({c["id"] for c in categories}), 11)
        self.assertTrue(all(c["active"] is True for c in categories))

    def test_defaults_are_independent(self):
        categories = get_default_categories()
        categories[0]["name"] = "修改后的名称"
        self.assertEqual(get_default_categories()[0]["name"], "工资")

    def test_filter_by_type_and_active(self):
        set_category_active(self.ledger, "food", False)
        visible = list_categories(self.ledger, "expense", active_only=True)
        self.assertEqual(len(visible), 6)
        self.assertTrue(all(c["type"] == "expense" for c in visible))
        self.assertNotIn("food", [c["id"] for c in visible])

        # 默认查询保留停用类别，历史查询仍可使用。
        all_expenses = list_categories(self.ledger, "expense")
        self.assertIn("food", [c["id"] for c in all_expenses])

    def test_query_returns_copies(self):
        categories = list_categories(self.ledger)
        categories[0]["name"] = "意外修改"
        self.assertEqual(self.ledger["categories"][0]["name"], "工资")

    def test_empty_ledger(self):
        self.assertEqual(list_categories({"categories": []}), [])

    def test_create_category(self):
        category = create_category(self.ledger, "  夜宵  ", "expense")
        self.assertEqual(category["name"], "夜宵")
        self.assertEqual(category["type"], "expense")
        self.assertTrue(category["active"])
        self.assertEqual(len(self.ledger["categories"]), 12)
        self.assertEqual(
            len({c["id"] for c in self.ledger["categories"]}), 12
        )

    def test_duplicate_inactive_name_is_rejected(self):
        set_category_active(self.ledger, "food", False)
        before = copy.deepcopy(self.ledger)
        with self.assertRaises(CategoryError) as error:
            create_category(self.ledger, " 饮食 ", "expense")
        self.assertEqual(error.exception.field, "name")
        self.assertEqual(self.ledger, before)

    def test_same_name_in_different_types_is_allowed(self):
        create_category(self.ledger, "红包", "income")
        create_category(self.ledger, "红包", "expense")
        self.assertEqual(len(self.ledger["categories"]), 13)

    def test_invalid_name_does_not_change_ledger(self):
        for name in ("", "   ", None, 123):
            with self.subTest(name=name):
                before = copy.deepcopy(self.ledger)
                with self.assertRaises(CategoryError):
                    create_category(self.ledger, name, "expense")
                self.assertEqual(self.ledger, before)

    def test_invalid_type_is_rejected(self):
        before = copy.deepcopy(self.ledger)
        with self.assertRaises(CategoryError):
            create_category(self.ledger, "测试", "支出")
        with self.assertRaises(CategoryError):
            list_categories(self.ledger, "invalid")
        self.assertEqual(self.ledger, before)

    def test_name_length_boundary_after_trimming(self):
        name = "类" * 20
        category = create_category(self.ledger, " " + name + " ", "expense")
        self.assertEqual(category["name"], name)
        renamed = rename_category(self.ledger, "food", " " + "名" * 20 + " ")
        self.assertEqual(renamed["name"], "名" * 20)

    def test_overlong_names_do_not_change_ledger(self):
        for operation in (
            lambda: create_category(self.ledger, "类" * 21, "expense"),
            lambda: rename_category(self.ledger, "food", "名" * 21),
        ):
            with self.subTest(operation=operation):
                before = copy.deepcopy(self.ledger)
                with self.assertRaises(CategoryError) as error:
                    operation()
                self.assertEqual(error.exception.field, "name")
                self.assertEqual(self.ledger, before)

    def test_rename_preserves_identity_and_references(self):
        entries = copy.deepcopy(self.ledger["entries"])
        budgets = copy.deepcopy(self.ledger["budgets"])
        category = rename_category(self.ledger, "food", " 餐饮 ")
        self.assertEqual(category, {
            "id": "food",
            "name": "餐饮",
            "type": "expense",
            "active": True,
        })
        self.assertEqual(self.ledger["entries"], entries)
        self.assertEqual(self.ledger["budgets"], budgets)

    def test_rename_same_name_is_allowed(self):
        category = rename_category(self.ledger, "food", " 饮食 ")
        self.assertEqual(category["name"], "饮食")

    def test_rename_duplicate_does_not_change_ledger(self):
        before = copy.deepcopy(self.ledger)
        with self.assertRaises(CategoryError):
            rename_category(self.ledger, "food", "交通")
        self.assertEqual(self.ledger, before)

    def test_disable_and_restore_preserve_references(self):
        before = copy.deepcopy(self.ledger)
        set_category_active(self.ledger, "food", False)
        self.assertEqual(len(self.ledger["categories"]), 11)
        self.assertEqual(self.ledger["entries"], before["entries"])
        self.assertEqual(self.ledger["budgets"], before["budgets"])

        set_category_active(self.ledger, "food", True)
        self.assertEqual(self.ledger, before)

    def test_missing_category_is_rejected(self):
        before = copy.deepcopy(self.ledger)
        with self.assertRaises(CategoryError):
            rename_category(self.ledger, "missing", "测试")
        with self.assertRaises(CategoryError):
            set_category_active(self.ledger, "missing", False)
        self.assertEqual(self.ledger, before)

    def test_active_requires_boolean(self):
        before = copy.deepcopy(self.ledger)
        for active in ("false", "true", 0, 1, None):
            with self.subTest(active=active):
                with self.assertRaises(CategoryError):
                    set_category_active(self.ledger, "food", active)
        self.assertEqual(self.ledger, before)


if __name__ == "__main__":
    unittest.main()
