"""Real-app filtering integration with a temporary, persisted JSON ledger.

No filtering, validation, storage, or route implementations are substituted.
Run from the repository root: python -m unittest discover -s test.
"""

import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from flask import template_rendered

from lotterytally.app import create_app
from lotterytally import storage


TODAY = date(2026, 11, 7)
MONTH_START = TODAY.replace(day=1)
MONTH_END = TODAY.replace(day=calendar.monthrange(TODAY.year, TODAY.month)[1])


def make_entry(entry_id, amount=4000, category="food", kind="expense",
               day=TODAY, note="筛选餐费"):
    timestamp = f"{TODAY.isoformat()}T16:00:00+08:00"
    return {
        "id": entry_id, "date": day.isoformat(), "type": kind,
        "amount_cents": amount, "category_id": category, "note": note,
        "created_at": timestamp, "updated_at": timestamp,
    }


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


class FilterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="lotterytally-filter-routes-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "ledger.json"
        self.matching = [
            make_entry(f"match-{i:02d}", amount=3000 + 2000 * i // 44,
                       note=f"筛选餐费-{i:02d}")
            for i in range(45)
        ]
        self.matching[0]["date"] = MONTH_START.isoformat()
        self.matching[-1]["date"] = MONTH_END.isoformat()
        self.ledger = {
            "schema_version": 1,
            "categories": [
                {"id": "food", "name": "饮食", "type": "expense", "active": True},
                {"id": "traffic", "name": "交通", "type": "expense", "active": True},
                {"id": "retired_snack", "name": "夜宵", "type": "expense", "active": False},
                {"id": "salary", "name": "工资", "type": "income", "active": True},
            ],
            "entries": self.matching + [
                make_entry("too-small", amount=2999),
                make_entry("too-large", amount=5001),
                make_entry("traffic", category="traffic"),
                make_entry("salary", category="salary", kind="income"),
                make_entry("previous-month", day=MONTH_START - timedelta(days=1)),
                make_entry("different-note", note="不匹配的备注"),
                make_entry("retired", category="retired_snack", note="夜宵历史"),
            ],
            "budgets": {},
        }
        storage.save_ledger(self.ledger, self.path)
        self.app = create_app({
            "TESTING": True,
            "LEDGER_DATA_PATH": str(self.path),
            "DATA_FILE": str(self.path),
            "LEDGER_TODAY": TODAY,
        })
        self.client = self.app.test_client()
        self.query = {
            "preset": "this_month", "category_id": "food", "type": "expense",
            "min_amount": "30", "max_amount": "50", "q": "筛选餐费",
            "sort": "amount_desc",
        }

    def rendered_get(self, path, query=None):
        contexts = []

        def record(sender, template, context, **extra):
            contexts.append(dict(context))

        with template_rendered.connected_to(record, self.app):
            response = self.client.get(path, query_string=query)
        self.assertTrue(contexts, f"No template rendered for {path}: {response.status_code}")
        return response, contexts[-1]

    def csv_rows(self, query):
        response = self.client.get("/stats/export.csv", query_string=query)
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["日期", "收支类型", "类别", "金额（元）", "备注"])
        return rows[1:]

    def test_history_is_registered_and_reachable_from_real_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/entries", Links(response.get_data(as_text=True)).hrefs)
        response, context = self.rendered_get("/entries")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(context["matched_count"], len(self.ledger["entries"]))
        self.assertEqual(len(context["entries"]), 20)

    def test_history_pages_cover_45_matches_and_preserve_all_effective_filters(self):
        visible_ids = []
        for page, count in ((1, 20), (2, 20), (3, 5)):
            with self.subTest(page=page):
                response, context = self.rendered_get("/entries", dict(self.query, page=page))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(context["matched_count"], 45)
                self.assertEqual(context["page_count"], 3)
                self.assertEqual(len(context["entries"]), count)
                visible_ids.extend(row["id"] for row in context["entries"])
                page_links = [href for href in Links(response.get_data(as_text=True)).hrefs
                              if urlsplit(href).path == "/entries"
                              and "page" in parse_qs(urlsplit(href).query)]
                self.assertTrue(page_links)
                for href in page_links:
                    params = parse_qs(urlsplit(href).query)
                    for key in ("category_id", "type", "q", "sort"):
                        self.assertEqual(params[key], [self.query[key]])
                    self.assertEqual(params["start_date"], [MONTH_START.isoformat()])
                    self.assertEqual(params["end_date"], [MONTH_END.isoformat()])
                    self.assertEqual(params["min_amount"], ["30.00"])
                    self.assertEqual(params["max_amount"], ["50.00"])
                    self.assertNotIn("preset", params)
        self.assertEqual(visible_ids, [row["id"] for row in reversed(self.matching)])

    def test_stats_charts_and_csv_share_the_complete_matching_set_and_order(self):
        response, context = self.rendered_get("/stats", dict(self.query, page=2))
        self.assertEqual(response.status_code, 200)
        expected_cents = sum(row["amount_cents"] for row in self.matching)
        self.assertEqual(context["summary"]["count"], 45)
        self.assertEqual(context["summary"]["expense_cents"], expected_cents)
        self.assertEqual(context["summary"]["income_cents"], 0)
        self.assertEqual(context["chart_data"]["count"], 45)
        self.assertEqual(context["chart_data"]["expense_cents"], expected_cents)
        self.assertEqual(sum(row["expense_cents"] for row in context["daily_totals"]),
                         expected_cents)
        rows = self.csv_rows(dict(self.query, page=2))
        self.assertEqual(len(rows), 45)
        self.assertEqual([row[4] for row in rows],
                         [row["note"] for row in context["entries"]])
        self.assertEqual(sum(int(Decimal(row[3]) * 100) for row in rows), expected_cents)
        self.assertTrue(all(row[1:3] == ["支出", "饮食"] for row in rows))

    def test_frozen_history_parameters_keep_stats_and_csv_consistent_next_month(self):
        response, history = self.rendered_get("/entries", self.query)
        self.assertEqual(response.status_code, 200)
        effective_query = history["query"]
        self.app.config["LEDGER_TODAY"] = date(2026, 12, 3)
        response, stats = self.rendered_get("/stats", effective_query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stats["summary"]["count"], history["matched_count"])
        self.assertEqual(stats["query_params"], effective_query)
        self.assertEqual(len(self.csv_rows(effective_query)), 45)

    def test_explicit_dates_override_presets_consistently_in_results_and_forms(self):
        query = dict(self.query, preset="last_month",
                     start_date=TODAY.isoformat(), end_date=TODAY.isoformat())
        for endpoint in ("/entries", "/stats"):
            with self.subTest(endpoint=endpoint):
                response, context = self.rendered_get(endpoint, query)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(context["form"].get("preset"))
                count = context.get("matched_count", context.get("summary", {}).get("count"))
                self.assertEqual(count, 43)
                effective = context.get("query", context.get("query_params"))
                self.assertNotIn("preset", effective)
        self.assertEqual(len(self.csv_rows(query)), 43)

    def test_inactive_categories_remain_queryable_across_all_routes(self):
        query = {"preset": "this_month", "category_id": "retired_snack"}
        response, history = self.rendered_get("/entries", query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in history["entries"]], ["retired"])
        self.assertIn("已停用", response.get_data(as_text=True))
        response, stats = self.rendered_get("/stats", query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stats["summary"]["count"], 1)
        self.assertEqual(stats["summary"]["expense_cents"], 4000)
        self.assertEqual(self.csv_rows(query)[0][2:], ["夜宵", "40.00", "夜宵历史"])

    def test_invalid_ranges_return_400_and_cannot_offer_unfiltered_csv(self):
        for query in ({"min_amount": "50", "max_amount": "30"},
                      {"start_date": "2026-11-08", "end_date": "2026-11-07"}):
            for endpoint in ("/entries", "/stats", "/stats/export.csv"):
                with self.subTest(query=query, endpoint=endpoint):
                    response = self.client.get(endpoint, query_string=query)
                    self.assertEqual(response.status_code, 400)
                    html = response.get_data(as_text=True)
                    self.assertNotIn("筛选餐费-00", html)
                    for href in Links(html).hrefs:
                        if urlsplit(href).path == "/stats/export.csv":
                            # An error page must not turn an invalid query into a
                            # working link that downloads the whole ledger.
                            self.assertTrue(urlsplit(href).query)
                            self.assertEqual(self.client.get(href).status_code, 400)

    def test_routes_are_read_only_and_reload_records_saved_to_the_json_file(self):
        original_bytes = self.path.read_bytes()
        for endpoint in ("/entries", "/stats", "/stats/export.csv"):
            self.assertEqual(self.client.get(endpoint, query_string=self.query).status_code, 200)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        storage.update_ledger(
            lambda ledger: ledger["entries"].append(
                make_entry("new-from-disk", note="筛选餐费-新增")),
            self.path,
        )
        response, history = self.rendered_get("/entries", self.query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(history["matched_count"], 46)
        response, stats = self.rendered_get("/stats", self.query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stats["summary"]["count"], 46)
        self.assertEqual(len(self.csv_rows(self.query)), 46)


if __name__ == "__main__":
    unittest.main()
