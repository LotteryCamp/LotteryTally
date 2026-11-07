"""类别页面与真实存储集成测试；每项测试仅操作临时账本。

运行：python -m unittest discover -s test -p "test_categor*.py" -v
"""

from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lotterytally import storage
from lotterytally.app import create_app
from lotterytally.categories import list_categories


class FormParser(HTMLParser):
    """按输入框 ID 提取属性，检查错误回显而不依赖页面排版。"""

    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "id" in attrs:
            self.inputs[attrs["id"]] = attrs


class CategoryViewsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="category-views-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "selected-ledger.json"
        ledger = storage.new_ledger()
        ledger["entries"] = [{
            "id": "entry-1", "date": "2026-11-07", "type": "expense",
            "amount_cents": 4500, "category_id": "food", "note": "晚餐",
            "created_at": "2026-11-07T12:00:00+08:00",
            "updated_at": "2026-11-07T12:00:00+08:00",
        }]
        ledger["budgets"] = {
            "2026-11": {"total_cents": 100000, "category_limits": {"food": 50000}}
        }
        storage.save_ledger(ledger, data_file=self.path)
        self.original = storage.load_ledger(self.path)
        self.app = self.make_app()
        self.client = self.app.test_client()

    def make_app(self):
        return create_app({
            "TESTING": True, "SECRET_KEY": "category-test-key",
            "LEDGER_DATA_PATH": str(self.path),
            # 故意配置不同路径，确认类别路由使用 LEDGER_DATA_PATH。
            "DATA_FILE": str(self.path.parent / "unused-ledger.json"),
        })

    def update(self, action, category_id="food", **fields):
        return self.client.post(
            f"/categories/{category_id}/update", data={"action": action, **fields}
        )

    def category(self, category_id="food"):
        return next(
            c for c in storage.load_ledger(self.path)["categories"]
            if c["id"] == category_id
        )

    def test_page_uses_shared_layout_and_registered_navigation(self):
        response = self.client.get("/categories")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("类别管理", html)
        self.assertIn("支出类别", html)
        self.assertIn("收入类别", html)
        self.assertIn('href="/"', html)
        self.assertIn('href="/categories"', html)
        self.assertIn("css/app.css", html)

    def test_create_redirects_and_survives_new_app(self):
        response = self.client.post(
            "/categories", data={"name": " 夜宵 ", "type": "expense"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/categories")
        categories = storage.load_ledger(self.path)["categories"]
        self.assertEqual(len(categories), 12)
        created = next(c for c in categories if c["name"] == "夜宵")
        self.assertEqual(created["type"], "expense")
        self.assertTrue(created["active"])
        restarted = self.make_app().test_client()
        self.assertIn("夜宵", restarted.get("/categories").get_data(as_text=True))
        # 刷新 GET 不应再次创建；不同的 DATA_FILE 路径也不应生成账本。
        self.client.get("/categories")
        self.assertEqual(len(storage.load_ledger(self.path)["categories"]), 12)
        self.assertFalse((self.path.parent / "unused-ledger.json").exists())

    def test_create_errors_preserve_input_and_file(self):
        before = self.path.read_bytes()
        cases = [
            ({"name": "", "type": "expense"}, "不能为空"),
            ({"name": " 饮食 ", "type": "expense"}, "不能重复"),
            ({"name": "长" * 21, "type": "income"}, "最多 20"),
            ({"name": "保留输入", "type": "invalid"}, "收支类型"),
        ]
        for form, message in cases:
            with self.subTest(form=form):
                response = self.client.post("/categories", data=form)
                self.assertEqual(response.status_code, 400)
                html = response.get_data(as_text=True)
                self.assertIn(message, html)
                self.assertEqual(FormParser(html).inputs["category-name"]["value"], form["name"])
                self.assertEqual(self.path.read_bytes(), before)

    def test_rename_preserves_identity_and_history_references(self):
        self.assertEqual(self.update("rename", name=" 餐饮 ").status_code, 302)
        self.assertEqual(self.category(), {
            "id": "food", "name": "餐饮", "type": "expense", "active": True,
        })
        ledger = storage.load_ledger(self.path)
        self.assertEqual(ledger["entries"], self.original["entries"])
        self.assertEqual(ledger["budgets"], self.original["budgets"])
        names = {c["id"]: c["name"] for c in list_categories(ledger)}
        self.assertEqual(names[ledger["entries"][0]["category_id"]], "餐饮")

    def test_failed_rename_preserves_row_input_and_file(self):
        before = self.path.read_bytes()
        for name in ("", "交通", "长" * 21):
            with self.subTest(name=name):
                response = self.update("rename", name=name)
                self.assertEqual(response.status_code, 400)
                inputs = FormParser(response.get_data(as_text=True)).inputs
                self.assertEqual(inputs["rename-food"]["value"], name)
                self.assertEqual(inputs["rename-food"]["aria-invalid"], "true")
                self.assertEqual(self.path.read_bytes(), before)

    def test_disable_is_visible_in_history_but_not_new_options(self):
        self.assertEqual(self.update("disable").status_code, 302)
        ledger = storage.load_ledger(self.path)
        self.assertFalse(self.category()["active"])
        self.assertIn("food", [c["id"] for c in list_categories(ledger)])
        self.assertNotIn("food", [c["id"] for c in list_categories(ledger, active_only=True)])
        html = self.client.get("/categories").get_data(as_text=True)
        self.assertIn("已停用", html)
        self.assertIn('id="rename-food"', html)
        self.assertEqual(ledger["entries"], self.original["entries"])
        self.assertEqual(ledger["budgets"], self.original["budgets"])
        self.assertEqual(self.update("enable").status_code, 302)
        self.assertEqual(storage.load_ledger(self.path), self.original)

    def test_inactive_name_cannot_be_reused(self):
        self.update("disable")
        before = self.path.read_bytes()
        response = self.client.post("/categories", data={"name": "饮食", "type": "expense"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_category_and_invalid_action_do_not_write(self):
        before = self.path.read_bytes()
        for action in ("rename", "disable", "enable"):
            with self.subTest(action=action):
                self.assertEqual(self.update(action, "missing", name="测试").status_code, 404)
        for action in ("", "delete", "unknown"):
            with self.subTest(action=action):
                self.assertEqual(self.update(action).status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_update_rejects_get_without_writing(self):
        before = self.path.read_bytes()
        self.assertEqual(self.client.get("/categories/food/update").status_code, 405)
        self.assertEqual(self.path.read_bytes(), before)

    def test_names_are_html_escaped(self):
        response = self.client.post(
            "/categories", data={"name": '<script>"&', "type": "income"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("类别已创建", html)

    def test_corrupted_ledger_is_preserved(self):
        self.path.write_bytes(b"{broken")
        response = self.client.get("/categories")
        self.assertEqual(response.status_code, 503)
        self.assertIn("账本暂时无法读取", response.get_data(as_text=True))
        self.assertEqual(self.path.read_bytes(), b"{broken")

    def test_write_failure_displays_error_without_modifying_file(self):
        before = self.path.read_bytes()
        for error in (storage.StorageError("failed"), PermissionError("denied")):
            with self.subTest(error=error):
                with patch.object(storage, "update_ledger", side_effect=error):
                    for url, form in (
                        ("/categories", {"name": "夜宵", "type": "expense"}),
                        ("/categories/food/update", {"action": "disable"}),
                    ):
                        response = self.client.post(url, data=form)
                        self.assertEqual(response.status_code, 503)
                        self.assertIn("账本暂时无法保存", response.get_data(as_text=True))
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
