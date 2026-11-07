"""Smoke tests for the local web application shell."""

import json
import tempfile
import unittest
from pathlib import Path

from lotterytally.app import _default_ledger_path, create_app


class AppShellTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
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

    def test_static_stylesheet_is_served(self):
        response = self.client.get("/static/css/app.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b".summary-grid", response.data)
        response.close()

    def test_default_ledger_path_is_inside_repository(self):
        expected = Path(__file__).resolve().parents[1] / "data" / "ledger.json"

        self.assertEqual(_default_ledger_path(), expected)

    def test_homepage_reads_the_configured_ledger_path(self):
        """首页通过存储模块读取 LEDGER_DATA_PATH 指向的账本。

        旧版在存储模块不存在时走 JSON 回退读取（fixture 因此没有
        schema_version）。存储模块现在已合入并按任务卡做版本检查，
        账本必须带 schema_version 才能被读取 —— 损坏/缺版本的文件
        会被拒绝而不是被当成空账本展示。
        """
        ledger = {
            "schema_version": 1,
            "categories": [{"id": "food", "name": "饮食", "type": "expense", "active": True}],
            "entries": [
                {
                    "id": "entry-1",
                    "date": "2026-11-07",
                    "type": "expense",
                    "amount_cents": 1234,
                    "category_id": "food",
                    "note": "午餐",
                    "created_at": "2026-11-07T12:00:00+08:00",
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


if __name__ == "__main__":
    unittest.main()
