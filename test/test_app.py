"""Smoke tests for the local web application shell."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from lotterytally.app import _default_ledger_path, create_app


class AppShellTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="lotterytally-app-")
        self.addCleanup(self.folder.cleanup)
        path = str(Path(self.folder.name) / "ledger.json")
        self.app = create_app({
            "TESTING": True, "LEDGER_DATA_PATH": path, "DATA_FILE": path,
        })
        self.client = self.app.test_client()

    def test_homepage_renders_without_backend_modules(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("PocketLedger".encode(), response.data)
        self.assertIn("还没有收支记录".encode(), response.data)

    def test_homepage_has_no_development_process_copy(self):
        response = self.client.get("/")

        for phrase in ("AI", "开发中", "骨架", "模块仍在集成"):
            self.assertNotIn(phrase.encode(), response.data)

    def test_all_pages_render_the_same_top_navigation(self):
        paths = ("/", "/entries/new", "/entries", "/stats", "/budget", "/categories", "/backup")
        rendered_links = []
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                match = re.search(rb'<nav class="main-nav"[^>]*>(.*?)</nav>', response.data, re.S)
                self.assertIsNotNone(match)
                links = re.findall(rb'<a href="([^"]+)"[^>]*>([^<]+)</a>', match.group(1))
                rendered_links.append([(href.decode(), label.decode().strip()) for href, label in links])

        self.assertTrue(all(links == rendered_links[0] for links in rendered_links[1:]))
        self.assertEqual(
            [href for href, _label in rendered_links[0]],
            ["/", "/entries/new", "/entries", "/stats", "/budget", "/categories", "/backup"],
        )

    def test_static_stylesheet_is_served(self):
        response = self.client.get("/static/css/app.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b".summary-grid", response.data)
        response.close()

    def test_default_ledger_path_is_inside_repository(self):
        expected = Path(__file__).resolve().parents[1] / "data" / "ledger.json"

        self.assertEqual(_default_ledger_path(), expected)

    def test_homepage_reads_the_configured_ledger_when_storage_module_is_absent(self):
        ledger = {
            "schema_version": 1,
            "categories": [{"id": "food", "name": "饮食", "type": "expense"}],
            "entries": [
                {
                    "id": "entry-1",
                    "date": "2026-11-07",
                    "type": "expense",
                    "amount_cents": 1234,
                    "category_id": "food",
                    "note": "午餐",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            app = create_app({"TESTING": True, "LEDGER_DATA_PATH": str(path)})

            response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("午餐".encode(), response.data)
        self.assertIn("12.34".encode(), response.data)

    def test_stats_route_uses_the_configured_ledger_path(self):
        ledger = {
            "schema_version": 1,
            "categories": [{"id": "food", "name": "饮食", "type": "expense", "active": True}],
            "entries": [
                {
                    "id": "entry-1",
                    "date": "2026-11-07",
                    "type": "expense",
                    "amount_cents": 5678,
                    "category_id": "food",
                    "note": "午餐",
                    "created_at": "2026-11-07T12:00:00+08:00",
                }
            ],
            "budgets": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            app = create_app({"TESTING": True, "LEDGER_DATA_PATH": str(path)})

            response = app.test_client().get("/stats")

        self.assertEqual(response.status_code, 200)
        self.assertIn("56.78".encode(), response.data)

    def test_entry_submission_appears_in_history_home_stats_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            app = create_app({"TESTING": True, "LEDGER_DATA_PATH": str(path)})
            client = app.test_client()

            form = client.get("/entries/new")
            self.assertEqual(form.status_code, 200)
            self.assertIn("饮食".encode(), form.data)

            response = client.post(
                "/entries/new",
                data={
                    "type": "expense",
                    "amount": "12.50",
                    "date": "2026-11-07",
                    "category_id": "food",
                    "note": "集成测试午餐",
                },
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("集成测试午餐".encode(), response.data)

            home = client.get("/")
            stats = client.get("/stats")
            budget = client.get("/budget?month=2026-11")
            exported = client.get("/stats/export.csv")
            self.assertEqual(home.status_code, 200)
            self.assertIn("集成测试午餐".encode(), home.data)
            self.assertEqual(stats.status_code, 200)
            self.assertIn(b"12.50", stats.data)
            self.assertEqual(budget.status_code, 200)
            self.assertIn(b"12.50", budget.data)
            self.assertEqual(exported.status_code, 200)
            self.assertIn("集成测试午餐".encode(), exported.data)
            self.assertIn(b"12.50", exported.data)

    def test_homepage_limits_recent_transactions_to_latest_ten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            app = create_app({"TESTING": True, "LEDGER_DATA_PATH": str(path)})
            client = app.test_client()
            client.get("/entries/new")

            for day in range(1, 12):
                response = client.post(
                    "/entries/new",
                    data={
                        "type": "expense",
                        "amount": "1",
                        "date": f"2026-10-{day:02d}",
                        "category_id": "food",
                        "note": f"记录{day}",
                    },
                )
                self.assertEqual(response.status_code, 302)

            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertNotIn(">记录1<".encode(), home.data)
            for day in range(2, 12):
                self.assertIn(f"记录{day}".encode(), home.data)

    def test_invalid_entry_submission_renders_field_errors_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            app = create_app({"TESTING": True, "LEDGER_DATA_PATH": str(path)})
            client = app.test_client()
            response = client.post(
                "/entries/new",
                data={"type": "expense", "amount": "0", "date": "2026-02-30", "category_id": "food"},
            )

            self.assertEqual(response.status_code, 400)
            self.assertIn("金额必须大于 0".encode(), response.data)
            self.assertIn("日期不存在".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
