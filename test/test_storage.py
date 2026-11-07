"""
storage.py 的单元测试

负责人：李义东 102401215
运行：python -m unittest discover -s test

覆盖任务卡的四条基础交付要求：
    - 初始化：首次启动生成默认账本和默认类别（T01）
    - 版本检查：不识别的 schema_version 直接拒绝
    - 原子写入：更新落盘、无临时文件残留、失败不落盘
    - 换坏文件保护：损坏的账本报错且原文件保持原样（T14 的存储侧）

另外覆盖方案 6.1 节的核心承诺：
    - update_ledger 在同一锁内完成读取、修改、写入
    - 校验不过的数据进不了文件（账目、类别、预算的各类非法场景）
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# 把仓库根目录加进搜索路径，才能 import 到 lotterytally 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lotterytally import storage  # noqa: E402


class StorageTestBase(unittest.TestCase):
    """每个用例独享一个临时目录，账本互不干扰。

    路径统一用 pathlib 拼接，始终落在 setUp 创建的临时目录内部，
    不做任何基于外部输入的路径拼接。
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="storage-test-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data_file = self.tmpdir / "ledger.json"

    def read_file_json(self):
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def read_file_text(self):
        return self.data_file.read_text(encoding="utf-8")

    def write_file_text(self, text):
        self.data_file.write_text(text, encoding="utf-8")

    def make_category(self, cid, entry_type="expense", name=None):
        return {"id": cid, "name": name or cid, "type": entry_type, "active": True}

    def make_entry(self, **kwargs):
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

    def make_default_entry(self, **kwargs):
        """给默认账本用的账目：category_id 取默认支出类别的真实 ID。

        默认账本的类别 ID 由 storage 生成（形如 expense_饮食），
        不是 full_ledger 里的自造 ID，直接用 make_entry 会被校验拦下。
        """
        ledger = storage.load_ledger(self.data_file)
        kwargs.setdefault(
            "category_id",
            next(c["id"] for c in ledger["categories"] if c["type"] == "expense"))
        return self.make_entry(**kwargs)

    def full_ledger(self):
        """一份最小但完全合法的账本，供校验类用例做增删改。"""
        return {
            "schema_version": 1,
            "categories": [
                self.make_category("food", "expense", "饮食"),
                self.make_category("salary", "income", "工资"),
            ],
            "entries": [self.make_entry()],
            "budgets": {
                "2026-11": {"total_cents": 150000, "category_limits": {"food": 50000}}
            },
        }


class TestInit(StorageTestBase):
    """初始化：首次启动自动生成默认账本（T01）。"""

    def test_ensure_ledger_creates_default_file(self):
        path = storage.ensure_ledger(self.data_file)
        self.assertEqual(Path(path).resolve(), self.data_file.resolve())
        self.assertTrue(self.data_file.exists())

        data = self.read_file_json()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["budgets"], {})

    def test_default_categories_complete(self):
        """默认类别由类别模块提供（方案 5.2 节），存储不做第二套。

        深拷贝比对：ID 体系必须和 categories 模块完全一致，
        否则表单按类别模块的 ID 记账会因引用不存在被存储校验拒掉。
        """
        from lotterytally import categories

        data = storage.load_ledger(self.data_file)
        self.assertEqual(data["categories"], categories.get_default_categories())
        income = [c["name"] for c in data["categories"] if c["type"] == "income"]
        expense = [c["name"] for c in data["categories"] if c["type"] == "expense"]
        self.assertEqual(income, ["工资", "奖学金", "兼职", "其他"])
        self.assertEqual(expense, ["饮食", "交通", "学习", "生活", "娱乐", "医疗", "其他"])
        for c in data["categories"]:
            self.assertTrue(c["active"])
            self.assertTrue(c["id"])

    def test_ensure_ledger_idempotent(self):
        """第二次调用不能重置账本 —— 已有数据被冲掉是重大事故。"""
        storage.ensure_ledger(self.data_file)
        storage.update_ledger(
            lambda ledger: ledger["entries"].append(self.make_default_entry()) or None,
            self.data_file)
        storage.ensure_ledger(self.data_file)
        self.assertEqual(len(storage.load_ledger(self.data_file)["entries"]), 1)

    def test_ensure_ledger_does_not_touch_corrupted_file(self):
        """文件已存在但损坏：初始化必须原样保留，等 load 报错，不能悄悄重建。"""
        self.write_file_text("{不是 JSON")
        storage.ensure_ledger(self.data_file)
        self.assertEqual(self.read_file_text(), "{不是 JSON")

    def test_load_creates_default_when_missing(self):
        """load_ledger 在文件不存在时自动初始化默认账本。"""
        data = storage.load_ledger(self.data_file)
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(self.data_file.exists())

    def test_load_returns_deep_copy(self):
        """快照是深拷贝：调用方改了返回值，账本不能跟着变。"""
        snapshot = storage.load_ledger(self.data_file)
        snapshot["entries"].append(self.make_entry())
        snapshot["schema_version"] = 999
        again = storage.load_ledger(self.data_file)
        self.assertEqual(again["entries"], [])
        self.assertEqual(again["schema_version"], 1)


class TestVersionCheck(StorageTestBase):
    """版本检查：只放行支持的版本，其他一律拒绝且不动文件。"""

    def test_unsupported_future_version_rejected(self):
        data = self.full_ledger()
        data["schema_version"] = 2
        self.write_file_text(json.dumps(data, ensure_ascii=False))
        with self.assertRaises(storage.LedgerCorruptedError) as ctx:
            storage.load_ledger(self.data_file)
        self.assertIn("版本", str(ctx.exception))
        # 文件保持原样
        self.assertEqual(self.read_file_json()["schema_version"], 2)

    def test_missing_version_rejected(self):
        data = self.full_ledger()
        del data["schema_version"]
        self.write_file_text(json.dumps(data, ensure_ascii=False))
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.load_ledger(self.data_file)

    def test_non_int_version_rejected(self):
        data = self.full_ledger()
        data["schema_version"] = "1"
        self.write_file_text(json.dumps(data, ensure_ascii=False))
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.load_ledger(self.data_file)


class TestAtomicWrite(StorageTestBase):
    """原子写入与落盘行为。"""

    def test_update_persists(self):
        """T02 的存储侧：更新后落盘，重新 load 还在。"""
        storage.update_ledger(
            lambda ledger: ledger["entries"].append(self.make_default_entry()) or None,
            self.data_file)
        data = storage.load_ledger(self.data_file)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["amount_cents"], 4500)

    def test_update_callable_can_replace_whole_ledger(self):
        """返回含 schema_version 的完整账本 dict → 按整体替换处理。"""
        storage.update_ledger(lambda l: self.full_ledger(), self.data_file)
        self.assertEqual(len(storage.load_ledger(self.data_file)["entries"]), 1)

    def test_informational_return_value_is_ignored(self):
        """categories 等业务模块的函数"原地修改 + 返回信息副本"。

        自然组合写法 update_ledger(lambda l: create_category(l, ...))
        必须能直接工作：副本不是账本，不能当整体替换，忽略返回值，
        以原地修改为准 —— 否则账本会被覆盖成一个类别对象。
        """
        from lotterytally import categories

        def change(ledger):
            return categories.create_category(ledger, "宠物", "expense")

        result = storage.update_ledger(change, self.data_file)
        names = [c["name"] for c in result["categories"]]
        self.assertIn("宠物", names)
        self.assertEqual(len(result["categories"]), 12)
        # 落盘的也是修改后的完整账本，而不是那个副本
        self.assertEqual(
            [c["name"] for c in storage.load_ledger(self.data_file)["categories"]],
            names)

    def test_update_with_dict_replaces(self):
        storage.update_ledger(self.full_ledger(), self.data_file)
        data = storage.load_ledger(self.data_file)
        self.assertEqual(data["budgets"]["2026-11"]["total_cents"], 150000)

    def test_update_rejects_bad_change_type(self):
        with self.assertRaises(storage.LedgerValidationError):
            storage.update_ledger("不是回调", self.data_file)

    def test_update_validates_before_save(self):
        """校验不过的修改必须整体拒绝，文件里不能留下半合法数据。"""
        def bad_change(ledger):
            ledger["entries"].append(self.make_entry(amount_cents=0))

        with self.assertRaises(storage.LedgerValidationError):
            storage.update_ledger(bad_change, self.data_file)
        self.assertEqual(storage.load_ledger(self.data_file)["entries"], [])

    def test_update_returns_updated_snapshot(self):
        result = storage.update_ledger(
            lambda ledger: ledger["entries"].append(self.make_default_entry()) or None,
            self.data_file)
        self.assertEqual(len(result["entries"]), 1)

    def test_no_temp_file_left_behind(self):
        """成功与失败都不留临时文件，目录里只有账本本身。"""
        storage.update_ledger(lambda l: None, self.data_file)
        with self.assertRaises(storage.LedgerValidationError):
            storage.update_ledger(
                lambda ledger: ledger["entries"].append({"x": 1}) or None,
                self.data_file)
        leftovers = [n for n in os.listdir(self.tmpdir) if n != "ledger.json"]
        self.assertEqual(leftovers, [])

    def test_chinese_kept_readable(self):
        """ensure_ascii=False：中文按原文保存，手工排查损坏时能看懂。"""
        storage.update_ledger(lambda l: None, self.data_file)
        self.assertIn("饮食", self.read_file_text())

    def test_concurrent_updates_do_not_lose_writes(self):
        """并发更新互不覆盖 —— 方案 6.1 节要求锁住完整的读取到写入过程。

        多线程各自追加一笔账。如果只锁最后一步写入，两个请求会都读到
        旧账本、后保存的覆盖先保存的，最终笔数就会少于请求数。
        """
        import threading

        worker_count = 8
        updates_per_worker = 5
        # 每笔账要唯一 ID：校验会拒绝重复 ID，共用 ID 会让线程
        # 在校验环节集体失败，测的就不再是并发而是报错了
        counter = iter(range(worker_count * updates_per_worker))

        def worker():
            for _ in range(updates_per_worker):
                entry = self.make_default_entry(id="id-{:03d}".format(next(counter)))
                storage.update_ledger(
                    lambda ledger: ledger["entries"].append(entry) or None,
                    self.data_file)

        threads = [threading.Thread(target=worker) for _ in range(worker_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = storage.load_ledger(self.data_file)
        self.assertEqual(len(data["entries"]), worker_count * updates_per_worker)


class TestCorruptionProtection(StorageTestBase):
    """读到损坏文件必须报错并保留原文件，绝不静默重建（T14 存储侧）。"""

    def test_broken_json_rejected_and_preserved(self):
        broken = '{"schema_version": 1, "entries": [半截'
        self.write_file_text(broken)
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.load_ledger(self.data_file)
        self.assertEqual(self.read_file_text(), broken)

    def test_non_object_top_level_rejected(self):
        self.write_file_text("[1, 2, 3]")
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.load_ledger(self.data_file)

    def test_update_on_corrupted_file_fails_without_overwrite(self):
        """损坏文件上的更新也必须失败：不能把坏文件当空账本覆盖掉。"""
        broken = "{损坏"
        self.write_file_text(broken)
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.update_ledger(lambda l: None, self.data_file)
        self.assertEqual(self.read_file_text(), broken)

    def test_non_utf8_file_rejected(self):
        """不是 UTF-8 的文件同样按损坏处理。"""
        self.data_file.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(storage.LedgerCorruptedError):
            storage.load_ledger(self.data_file)


class TestValidateLedger(StorageTestBase):
    """全量结构校验：恢复备份（T14）和日常写入共用的这道门。"""

    def test_valid_ledger_passes_and_returns_input(self):
        data = self.full_ledger()
        self.assertIs(storage.validate_ledger(data), data)

    def test_valid_empty_ledger_passes(self):
        self.assertTrue(storage.validate_ledger(storage.new_ledger()))

    def test_top_level_must_be_object(self):
        for bad in [[], "ledger", None, 42]:
            with self.subTest(bad=bad):
                with self.assertRaises(storage.LedgerValidationError):
                    storage.validate_ledger(bad)

    # ---- 账目 ----

    def test_duplicate_entry_id_rejected(self):
        data = self.full_ledger()
        data["entries"].append(self.make_entry(id="id-001", note="重复 ID"))
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_missing_category_reference_rejected(self):
        """类别引用不存在 → 拒绝（T14 的关键场景）。"""
        data = self.full_ledger()
        data["entries"][0]["category_id"] = "ghost"
        with self.assertRaises(storage.LedgerValidationError) as ctx:
            storage.validate_ledger(data)
        self.assertIn("ghost", str(ctx.exception))

    def test_category_type_mismatch_rejected(self):
        """饮食是支出类，记一笔收入到饮食 → 拒绝（方案 4.2 节）。"""
        data = self.full_ledger()
        data["entries"][0]["type"] = "income"
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_bad_dates_rejected(self):
        for bad in ["2026/11/07", "2026-1-7", "2026-02-30", "2026-13-01", ""]:
            with self.subTest(date=bad):
                data = self.full_ledger()
                data["entries"][0]["date"] = bad
                with self.assertRaises(storage.LedgerValidationError):
                    storage.validate_ledger(data)

    def test_bad_amounts_rejected(self):
        """零、负数、小数、字符串、布尔都不许进账本。"""
        for bad in [0, -4500, 45.5, "4500", True, False, None, 4500.0]:
            with self.subTest(amount=bad):
                data = self.full_ledger()
                data["entries"][0]["amount_cents"] = bad
                with self.assertRaises(storage.LedgerValidationError):
                    storage.validate_ledger(data)

    def test_bad_type_rejected(self):
        for bad in ["支出", "INCOME", "", None]:
            with self.subTest(type=bad):
                data = self.full_ledger()
                data["entries"][0]["type"] = bad
                with self.assertRaises(storage.LedgerValidationError):
                    storage.validate_ledger(data)

    def test_note_too_long_rejected(self):
        data = self.full_ledger()
        data["entries"][0]["note"] = "长" * 201
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_note_200_chars_ok(self):
        data = self.full_ledger()
        data["entries"][0]["note"] = "长" * 200
        storage.validate_ledger(data)

    def test_note_can_be_none(self):
        data = self.full_ledger()
        data["entries"][0]["note"] = None
        storage.validate_ledger(data)

    # ---- 类别 ----

    def test_duplicate_category_id_rejected(self):
        data = self.full_ledger()
        data["categories"].append(self.make_category("food", "income", "重复ID"))
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_duplicate_category_name_same_type_rejected(self):
        """同收支类型下名称要唯一，否则表单出现多个同名选项。"""
        data = self.full_ledger()
        data["categories"].append(self.make_category("food2", "expense", "饮食 "))
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_same_name_different_type_allowed(self):
        """收入和支出各有一个"其他"是方案允许的，不能误杀。"""
        data = self.full_ledger()
        data["categories"].append(self.make_category("other_in", "income", "其他"))
        data["categories"].append(self.make_category("other_out", "expense", "其他"))
        storage.validate_ledger(data)

    def test_category_active_must_be_bool(self):
        data = self.full_ledger()
        data["categories"][0]["active"] = "yes"
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_category_active_defaults_true(self):
        data = self.full_ledger()
        del data["categories"][0]["active"]
        storage.validate_ledger(data)

    # ---- 预算 ----

    def test_bad_budget_month_rejected(self):
        for bad in ["2026-13", "2026/11", "202611", "2026-00", "26-11"]:
            with self.subTest(month=bad):
                data = self.full_ledger()
                data["budgets"] = {bad: {"total_cents": 10000}}
                with self.assertRaises(storage.LedgerValidationError):
                    storage.validate_ledger(data)

    def test_budget_reference_missing_category_rejected(self):
        data = self.full_ledger()
        data["budgets"]["2026-11"]["category_limits"] = {"ghost": 10000}
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_budget_limits_allow_zero_but_not_negative(self):
        data = self.full_ledger()
        data["budgets"]["2026-11"]["category_limits"] = {"food": 0}
        storage.validate_ledger(data)
        data["budgets"]["2026-11"]["total_cents"] = -1
        with self.assertRaises(storage.LedgerValidationError):
            storage.validate_ledger(data)

    def test_budget_total_can_be_omitted(self):
        """只设分类预算、不设总预算是合法配置。"""
        data = self.full_ledger()
        data["budgets"]["2026-11"] = {"category_limits": {"food": 50000}}
        storage.validate_ledger(data)


class TestConfigure(StorageTestBase):
    """configure() 指定全局路径后，不传路径的调用都落到同一份文件。"""

    def test_configure_redirects_default_calls(self):
        storage.configure(self.data_file)
        self.addCleanup(storage.configure, None)
        storage.update_ledger(
            lambda ledger: ledger["entries"].append(self.make_default_entry()) or None)
        data = storage.load_ledger()
        self.assertEqual(len(data["entries"]), 1)
        self.assertTrue(self.data_file.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
