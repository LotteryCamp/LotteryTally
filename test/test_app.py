"""Smoke tests for the local web application shell."""

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


if __name__ == "__main__":
    unittest.main()
