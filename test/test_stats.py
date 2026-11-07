"""单元测试：lotterytally.stats 统计页面路由与 CSV 导出同源联调（王智钦模块）。

负责人：王智钦 102401609
运行方式：python -m unittest discover -s test -v
覆盖方案验收用例：
- T01 / 空结果：统计卡片显示 0.00，图表显示空状态提示，CSV 可下载仅含表头的空文件
- T05：查询本月、饮食类、金额 30–50 元，只统计满足全部条件的账目（含边界 30 和 50）
- T07：本月工资 3,000 元，饮食 470 元 + 45 元 -> 支出 515.00 元，结余 2485.00 元
- T11：用与统计相同的条件导出 CSV，笔数与金额合计同统计页完全一致，超过一页时不截断
"""

from __future__ import annotations

import csv
from datetime import date
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from lotterytally.stats import create_app


TODAY = date(2026, 11, 7)

SAMPLE_CATEGORIES = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "retired_snack", "name": "夜宵", "type": "expense", "active": False},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
]


def make_entry(
    entry_id: str,
    day: str = "2026-11-07",
    kind: str = "expense",
    amount_cents: int = 4500,
    category_id: str = "food",
    note: str = "晚餐",
    created_at: str = "2026-11-07T18:00:00+08:00",
) -> dict:
    return {
        "id": entry_id,
        "date": day,
        "type": kind,
        "amount_cents": amount_cents,
        "category_id": category_id,
        "note": note,
        "created_at": created_at,
    }


class StatsRoutesTests(unittest.TestCase):
    def _make_client(self, entries: list[dict]):
        ledger = {
            "schema_version": 1,
            "categories": list(SAMPLE_CATEGORIES),
            "entries": list(entries),
            "budgets": {},
        }
        app = create_app(ledger_loader=lambda: ledger, today=TODAY)
        app.config["TESTING"] = True
        return app.test_client()

    def test_empty_ledger_renders_zero_cards_and_downloads_header_only_csv(self):
        client = self._make_client([])
        resp = client.get("/stats")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("0.00", html)
        self.assertIn("暂无支出记录", html)

        csv_resp = client.get("/stats/export.csv")
        self.assertEqual(csv_resp.status_code, 200)
        raw_bytes = csv_resp.get_data()
        # 确认只有一份 UTF-8 BOM (EF BB BF)
        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig"))))
        self.assertEqual(rows, [["日期", "收支类型", "类别", "金额（元）", "备注"]])

    def test_t07_monthly_salary_and_food_summary(self):
        entries = [
            make_entry("1", "2026-11-01", "income", 300000, "salary", "工资"),
            make_entry("2", "2026-11-03", "expense", 47000, "food", "食堂"),
            make_entry("3", "2026-11-07", "expense", 4500, "food", "晚餐"),
        ]
        client = self._make_client(entries)
        resp = client.get("/stats?preset=this_month")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("3000.00", html)
        self.assertIn("515.00", html)
        self.assertIn("2485.00", html)

        # 验证注入给前端图表的 JSON 数据精确为整数分
        match = re.search(
            r'<script id="pocketledger-chart-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        self.assertIsNotNone(match)
        chart_data = json.loads(match.group(1))
        self.assertEqual(chart_data["income_cents"], 300000)
        self.assertEqual(chart_data["expense_cents"], 51500)
        self.assertEqual(chart_data["balance_cents"], 248500)
        self.assertEqual(chart_data["by_category"][0]["amount_cents"], 51500)

    def test_t05_and_t11_filtered_stats_matches_csv_export_and_never_paginates(self):
        """验证筛选条件（本月、饮食、30-50元）下统计与 CSV 完全一致，且超过 20 条时导出完整集合。"""
        entries = [
            make_entry(f"in-{i}", "2026-11-07", "expense", 3000 + i * 50, "food", f"餐费{i}")
            for i in range(25)  # 30.00 元 ~ 42.00 元，共 25 笔，超过单页 20 条
        ] + [
            make_entry("upper-bound", "2026-11-07", "expense", 5000, "food", "50元边界"),
            make_entry("too-small", "2026-11-07", "expense", 2999, "food", "低于30元"),
            make_entry("too-large", "2026-11-07", "expense", 5001, "food", "高于50元"),
            make_entry("other-cat", "2026-11-07", "expense", 4000, "traffic", "地铁"),
            make_entry("last-month", "2026-10-25", "expense", 4000, "food", "上月餐费"),
        ]
        client = self._make_client(entries)
        query = "/stats?preset=this_month&category_id=food&min_amount=30&max_amount=50&sort=amount_desc"
        resp = client.get(query)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        match = re.search(
            r'<script id="pocketledger-chart-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        chart_data = json.loads(match.group(1))
        self.assertEqual(chart_data["count"], 26)

        csv_resp = client.get(
            "/stats/export.csv?preset=this_month&category_id=food&min_amount=30&max_amount=50&sort=amount_desc"
        )
        self.assertEqual(csv_resp.status_code, 200)
        rows = list(csv.reader(io.StringIO(csv_resp.get_data().decode("utf-8-sig"))))
        # 表头 1 行 + 26 条匹配记录 = 27 行
        self.assertEqual(len(rows), 27)
        # 第一条应为金额降序最大的 50.00 元
        self.assertEqual(rows[1][3], "50.00")
        # CSV 金额加总应等于 chart_data["expense_cents"]
        total_cents_in_csv = sum(
            int(round(float(row[3]) * 100)) for row in rows[1:]
        )
        self.assertEqual(total_cents_in_csv, chart_data["expense_cents"])

    def test_invalid_filter_returns_400_on_both_stats_and_export(self):
        client = self._make_client([])
        for bad_url in (
            "/stats?min_amount=-5",
            "/stats?start_date=2026-11-10&end_date=2026-11-01",
            "/stats?min_amount=100&max_amount=20",
        ):
            with self.subTest(bad_url=bad_url):
                resp = client.get(bad_url)
                self.assertEqual(resp.status_code, 400)
                csv_resp = client.get(bad_url.replace("/stats", "/stats/export.csv", 1))
                self.assertEqual(csv_resp.status_code, 400)

    def test_inactive_category_can_still_be_queried_in_stats(self):
        """对应方案 T12：已停用类别仍可在统计页筛选查询。"""
        entries = [
            make_entry("1", "2026-11-05", "expense", 3500, "retired_snack", "烧烤"),
        ]
        client = self._make_client(entries)
        resp = client.get("/stats?category_id=retired_snack")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("35.00", html)
        self.assertIn("夜宵（已停用）", html)

    def test_reads_ledger_data_path_from_app_config(self):
        """验证与 app.py 的 LEDGER_DATA_PATH 配置项无缝对接。"""
        ledger_payload = {
            "schema_version": 1,
            "categories": list(SAMPLE_CATEGORIES),
            "entries": [
                make_entry("demo-1", "2026-11-07", "expense", 8800, "food", "演示账本聚餐"),
            ],
            "budgets": {},
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            ledger_file = Path(tmp_dir) / "demo_ledger.json"
            ledger_file.write_text(json.dumps(ledger_payload, ensure_ascii=False), encoding="utf-8")
            app = create_app(today=TODAY)
            app.config["TESTING"] = True
            app.config["LEDGER_DATA_PATH"] = str(ledger_file)
            client = app.test_client()

            resp = client.get("/stats")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("88.00", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
