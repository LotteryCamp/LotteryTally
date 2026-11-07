"""
csv_export.py 的单元测试

负责人：蔡信坡 162404103
运行：python -m unittest discover -s tests

覆盖方案 6.2 节对导出的四条要求：
    - 中文可读（BOM）
    - 逗号、换行、引号由 CSV 写入器正确处理
    - 公式型文本不被表格软件执行
    - 空结果导出带表头的空文件
"""

import csv
import io
import os
import sys
import unittest

# 让测试能直接 import 上一级目录的 csv_export
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csv_export  # noqa: E402


CATEGORIES = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
]


def make_entry(**kwargs):
    """构造一条默认账目，测试里只覆盖关心的字段。"""
    entry = {
        "id": "id-001",
        "date": "2026-11-07",
        "type": "expense",
        "amount_cents": 4500,
        "category_id": "food",
        "note": "晚餐",
        "created_at": "2026-11-07T16:20:00+08:00",
    }
    entry.update(kwargs)
    return entry


def parse(csv_text):
    """去掉 BOM 后按 CSV 解析成二维列表，方便断言。"""
    assert csv_text.startswith("\ufeff"), "导出结果必须以 BOM 开头"
    return list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))


class TestCentsToYuan(unittest.TestCase):
    """金额换算：整数分 -> 元，不允许出现浮点误差。"""

    def test_common_values(self):
        cases = [
            (0, "0.00"),
            (1, "0.01"),
            (99, "0.99"),
            (100, "1.00"),
            (4500, "45.00"),
            (4550, "45.50"),
            (100000, "1000.00"),
            (300000, "3000.00"),
        ]
        for cents, expected in cases:
            with self.subTest(cents=cents):
                self.assertEqual(csv_export.cents_to_yuan(cents), expected)

    def test_negative_and_invalid(self):
        self.assertEqual(csv_export.cents_to_yuan(-4500), "-45.00")
        # 脏数据不该让导出整体崩掉
        self.assertEqual(csv_export.cents_to_yuan(None), "0.00")
        self.assertEqual(csv_export.cents_to_yuan("abc"), "0.00")


class TestExportCsv(unittest.TestCase):
    """主流程：表头、类型翻译、类别翻译、排序。"""

    def test_header_and_two_rows(self):
        entries = [
            make_entry(id="a", date="2026-11-01", type="income",
                       amount_cents=300000, category_id="salary", note="月工资"),
            make_entry(id="b", date="2026-11-02", type="expense",
                       amount_cents=4500, category_id="food", note="晚餐"),
        ]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))

        self.assertEqual(rows[0], csv_export.HEADER)
        self.assertEqual(len(rows), 3)
        # 默认倒序，11-02 在前
        self.assertEqual(rows[1][0], "2026-11-02")
        self.assertEqual(rows[1][1], "支出")
        self.assertEqual(rows[1][2], "饮食")
        self.assertEqual(rows[1][3], "45.00")
        self.assertEqual(rows[2][1], "收入")
        self.assertEqual(rows[2][3], "3000.00")

    def test_empty_entries_keeps_header(self):
        """空结果也要能下载，至少给出表头。"""
        rows = parse(csv_export.export_csv([], CATEGORIES))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], csv_export.HEADER)

    def test_none_entries(self):
        rows = parse(csv_export.export_csv(None, None))
        self.assertEqual(rows[0], csv_export.HEADER)

    def test_sort_disabled_keeps_input_order(self):
        entries = [
            make_entry(id="a", date="2026-11-01", note="先录入"),
            make_entry(id="b", date="2026-11-05", note="后录入"),
        ]
        rows = parse(csv_export.export_csv(entries, CATEGORIES, sort=False))
        self.assertEqual(rows[1][4], "先录入")
        self.assertEqual(rows[2][4], "后录入")

    def test_unknown_category_falls_back_to_id(self):
        entries = [make_entry(category_id="ghost")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][2], "ghost")

    def test_missing_category_id(self):
        entries = [make_entry(category_id="")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][2], csv_export.UNKNOWN_CATEGORY)


class TestCsvSafety(unittest.TestCase):
    """逗号、换行、引号、公式注入 —— 这几条最容易在演示时翻车。"""

    def test_comma_and_quote_in_note(self):
        entries = [make_entry(note='买了"两"本书,一共80元')]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        # CSV 写入器会自动加引号包裹，解析回来必须还是一整格
        self.assertEqual(rows[1][4], '买了"两"本书,一共80元')

    def test_newline_in_note(self):
        entries = [make_entry(note="第一行\n第二行")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "第一行\n第二行")

    def test_formula_injection_blocked(self):
        """= + - @ 开头的内容要加单引号，避免被 Excel 当公式执行。"""
        dangerous = ["=1+1", "+86", "-4500", "@SUM(A1)"]
        for note in dangerous:
            with self.subTest(note=note):
                rows = parse(csv_export.export_csv([make_entry(note=note)], CATEGORIES))
                self.assertTrue(rows[1][4].startswith("'"), "公式型备注未转义")
                self.assertEqual(rows[1][4], "'" + note)

    def test_chinese_survives(self):
        entries = [make_entry(note="中文备注：早餐、地铁、工资")]
        csv_text = csv_export.export_csv(entries, CATEGORIES)
        self.assertIn("中文备注：早餐、地铁、工资", csv_text)


class TestFilename(unittest.TestCase):
    def test_filename_is_ascii_csv(self):
        name = csv_export.suggest_filename()
        self.assertTrue(name.endswith(".csv"))
        # 文件名必须是纯 ASCII，避免浏览器下载时中文乱码
        name.encode("ascii")


if __name__ == "__main__":
    unittest.main(verbosity=2)
