"""
csv_export.py 的单元测试

负责人：蔡信坡 162404103
运行：python -m unittest discover -s test

覆盖方案 6.2 节对导出的四条要求：
    - 中文可读（BOM）
    - 逗号、换行、引号由 CSV 写入器正确处理
    - 公式型文本不被表格软件执行
    - 空结果导出带表头的空文件

另外覆盖两条容易回归的行为：
    - 默认保持调用方传入的顺序（不擅自重排）
    - 非法金额和非法记录直接报错，不静默兜底
"""

import csv
import io
import os
import sys
import unittest

# 把仓库根目录加进搜索路径，才能 import 到 lotterytally 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lotterytally import csv_export  # noqa: E402


CATEGORIES = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
]


def make_entry(**kwargs):
    """构造一条默认合法的账目，测试里只覆盖关心的字段。"""
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

    def test_accepts_integral_float_and_string(self):
        """JSON 里金额偶尔被写成 4500.0 或 \"4500\"，只要还是整数分就照收。"""
        self.assertEqual(csv_export.cents_to_yuan(4500.0), "45.00")
        self.assertEqual(csv_export.cents_to_yuan("4500"), "45.00")
        self.assertEqual(csv_export.cents_to_yuan(" 4500 "), "45.00")

    def test_invalid_amount_raises(self):
        """非法金额必须报错。以前返回 0.00，脏数据会变成零元支出混进导出。"""
        bad_values = [None, "abc", "", 45.5, True, False, [], {}]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(csv_export.ExportError):
                    csv_export.cents_to_yuan(value)

    def test_negative_amount_raises(self):
        """支出在数据里存正数，负数说明上游算错了。"""
        with self.assertRaises(csv_export.ExportError):
            csv_export.cents_to_yuan(-4500)


class TestExportCsv(unittest.TestCase):
    """主流程：表头、类型翻译、类别翻译、排序行为。"""

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
        # 默认保持传入顺序：11-01 在前
        self.assertEqual(rows[1][0], "2026-11-01")
        self.assertEqual(rows[1][1], "收入")
        self.assertEqual(rows[1][3], "3000.00")
        self.assertEqual(rows[2][1], "支出")
        self.assertEqual(rows[2][2], "饮食")
        self.assertEqual(rows[2][3], "45.00")

    def test_default_keeps_input_order(self):
        """默认不重排 —— 页面按金额排过序，导出必须和眼睛看到的一致。"""
        entries = [
            make_entry(id="a", date="2026-11-01", note="最早的一天"),
            make_entry(id="b", date="2026-11-05", note="最晚的一天"),
            make_entry(id="c", date="2026-11-03", note="中间"),
        ]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual([r[4] for r in rows[1:]],
                         ["最早的一天", "最晚的一天", "中间"])

    def test_sort_true_reorders_by_date_desc(self):
        """显式传 sort=True 时按日期倒序，用于标准化输出。"""
        entries = [
            make_entry(id="a", date="2026-11-01", note="最早的一天"),
            make_entry(id="b", date="2026-11-05", note="最晚的一天"),
            make_entry(id="c", date="2026-11-03", note="中间"),
        ]
        rows = parse(csv_export.export_csv(entries, CATEGORIES, sort=True))
        self.assertEqual([r[4] for r in rows[1:]],
                         ["最晚的一天", "中间", "最早的一天"])

    def test_empty_entries_keeps_header(self):
        """空结果也要能下载，至少给出表头。"""
        rows = parse(csv_export.export_csv([], CATEGORIES))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], csv_export.HEADER)

    def test_none_entries(self):
        """上游传 None（比如还没筛选过）不该崩，按空账本处理。"""
        rows = parse(csv_export.export_csv(None, None))
        self.assertEqual(rows[0], csv_export.HEADER)
        self.assertEqual(len(rows), 1)

    def test_unknown_category_falls_back_to_id(self):
        entries = [make_entry(category_id="ghost")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][2], "ghost")

    def test_missing_category_id(self):
        entries = [make_entry(category_id="")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][2], csv_export.UNKNOWN_CATEGORY)

    def test_note_can_be_none(self):
        entries = [make_entry(note=None)]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "")


class TestValidation(unittest.TestCase):
    """排序前就要把非法记录挡下来，报错信息要能指出是第几条。"""

    def test_non_dict_entry(self):
        with self.assertRaises(csv_export.ExportError) as ctx:
            csv_export.export_csv(["这不是字典"], CATEGORIES)
        self.assertIn("第 1 条记录", str(ctx.exception))

    def test_bad_date_format(self):
        for bad_date in ["2026/11/07", "2026-1-7", "20261107", ""]:
            with self.subTest(date=bad_date):
                with self.assertRaises(csv_export.ExportError):
                    csv_export.export_csv([make_entry(date=bad_date)], CATEGORIES)

    def test_impossible_date(self):
        """格式对但日期不存在（2 月 30 号）也要拦下来。"""
        with self.assertRaises(csv_export.ExportError):
            csv_export.export_csv([make_entry(date="2026-02-30")], CATEGORIES)

    def test_bad_type(self):
        for bad_type in ["expense1", "", None, "支出", "INCOME"]:
            with self.subTest(type=bad_type):
                with self.assertRaises(csv_export.ExportError):
                    csv_export.export_csv([make_entry(type=bad_type)], CATEGORIES)

    def test_bad_amount_points_to_row(self):
        """第 2 条记录的金额坏了，报错要指到第 2 条，不能只说金额错。"""
        entries = [make_entry(id="ok"), make_entry(id="bad", amount_cents=None)]
        with self.assertRaises(csv_export.ExportError) as ctx:
            csv_export.export_csv(entries, CATEGORIES)
        self.assertIn("第 2 条记录", str(ctx.exception))

    def test_created_at_missing_is_tolerated(self):
        """录入时间不参与业务，缺了也能导出。"""
        entry = make_entry()
        entry.pop("created_at")
        rows = parse(csv_export.export_csv([entry], CATEGORIES))
        self.assertEqual(len(rows), 2)


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
                self.assertEqual(rows[1][4], "'" + note)

    def test_formula_in_category_name_blocked(self):
        """类别名也可能被改成危险内容，一样要挡。"""
        cats = [{"id": "food", "name": "=HYPERLINK(\"http://evil\")"}]
        rows = parse(csv_export.export_csv([make_entry()], cats))
        self.assertTrue(rows[1][2].startswith("'"))

    def test_chinese_survives(self):
        entries = [make_entry(note="中文备注：早餐、地铁、工资")]
        csv_text = csv_export.export_csv(entries, CATEGORIES)
        self.assertIn("中文备注：早餐、地铁、工资", csv_text)

    def test_leading_and_trailing_space_preserved(self):
        """备注里的空格是用户输入的一部分，导出不能顺手剪掉。"""
        entries = [make_entry(note="  晚餐  ")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "  晚餐  ")

    def test_leading_newline_preserved(self):
        entries = [make_entry(note="\n第二段")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "\n第二段")

    def test_trailing_newline_preserved(self):
        entries = [make_entry(note="结尾换行\n")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "结尾换行\n")

    def test_formula_behind_leading_space_blocked(self):
        """前面垫了空格的公式一样要挡，否则绕过去就等于没防。"""
        rows = parse(csv_export.export_csv([make_entry(note="  =SUM(A1)")], CATEGORIES))
        self.assertEqual(rows[1][4], "'  =SUM(A1)")

    def test_whitespace_only_note_kept(self):
        entries = [make_entry(note="   ")]
        rows = parse(csv_export.export_csv(entries, CATEGORIES))
        self.assertEqual(rows[1][4], "   ")


class TestFilename(unittest.TestCase):
    def test_filename_is_ascii_csv(self):
        name = csv_export.suggest_filename()
        self.assertTrue(name.endswith(".csv"))
        # 文件名必须是纯 ASCII，避免浏览器下载时中文乱码
        name.encode("ascii")

    def test_default_prefix_used(self):
        self.assertTrue(csv_export.suggest_filename().startswith("pocketledger_"))

    def test_chinese_prefix_falls_back_to_default(self):
        """中文前缀净化后什么都不剩，退回默认前缀而不是输出中文名。"""
        name = csv_export.suggest_filename("账本")
        self.assertTrue(name.startswith("pocketledger_"))
        name.encode("ascii")

    def test_path_separator_in_prefix_removed(self):
        """带路径分隔符的前缀不能把目录带进下载名。"""
        for prefix in ["../etc/passwd", "a\\b", "x/y"]:
            with self.subTest(prefix=prefix):
                name = csv_export.suggest_filename(prefix)
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)
                name.encode("ascii")

    def test_allowed_chars_kept(self):
        name = csv_export.suggest_filename("my-ledger_2026")
        self.assertTrue(name.startswith("my-ledger_2026_"))

    def test_empty_prefix_falls_back(self):
        for prefix in ["", None, "___"]:
            with self.subTest(prefix=prefix):
                self.assertTrue(csv_export.suggest_filename(prefix).startswith("pocketledger_"))

    def test_long_prefix_truncated(self):
        name = csv_export.suggest_filename("a" * 200)
        self.assertLessEqual(len(name), 40 + 1 + 15 + 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
