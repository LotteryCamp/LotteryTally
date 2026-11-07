"""
backup.py 的单元测试

负责人：李义东 102401215
运行：python -m unittest discover -s test

覆盖方案 6.1 节恢复流程的每一步，对应两条验收用例：
    - T13：备份 -> 修改账目 -> 恢复，账本回到备份时状态，并留存恢复前备份
    - T14：导入损坏 JSON / 类别引用不存在的 JSON，页面报错，原账本保持原样
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

from lotterytally import backup, storage  # noqa: E402


class BackupTestBase(unittest.TestCase):
    """每个用例独享一个临时目录；路径统一用 pathlib，不做外部输入拼接。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="backup-test-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.data_file = self.tmpdir / "ledger.json"
        self.backup_dir = self.tmpdir / "backups"

    def make_ledger(self, note="晚餐", amount=4500, cid="food"):
        """构造一份最小合法账本，测试里按需改字段。"""
        return {
            "schema_version": 1,
            "categories": [
                {"id": "food", "name": "饮食", "type": "expense", "active": True},
                {"id": "salary", "name": "工资", "type": "income", "active": True},
            ],
            "entries": [{
                "id": "id-001",
                "date": "2026-11-07",
                "type": "expense",
                "amount_cents": amount,
                "category_id": cid,
                "note": note,
                "created_at": "2026-11-07T16:20:00+08:00",
            }],
            "budgets": {},
        }

    def write_ledger(self, ledger):
        """直接经存储入口落一份合法账本，不走备份模块。"""
        storage.save_ledger(ledger, data_file=self.data_file)

    def read_ledger(self):
        return storage.load_ledger(self.data_file)

    def encode(self, ledger, bom=False):
        text = json.dumps(ledger, ensure_ascii=False)
        if bom:
            text = "\ufeff" + text
        return text.encode("utf-8")


class TestDownloadBackup(BackupTestBase):
    """下载备份：内容完整、文件名安全。"""

    def test_payload_round_trips(self):
        self.write_ledger(self.make_ledger(note="带逗号,引号\"的备注"))
        payload, filename = backup.build_download_payload(self.data_file)
        text = payload.decode("utf-8")
        restored = json.loads(text)
        # JSON 会转义引号，所以解析后再比对原文，而不是在原始文本里找
        self.assertEqual(restored["entries"][0]["note"], "带逗号,引号\"的备注")
        self.assertEqual(restored["entries"][0]["amount_cents"], 4500)

    def test_payload_reflects_latest_data(self):
        self.write_ledger(self.make_ledger(amount=4500))
        payload, _ = backup.build_download_payload(self.data_file)
        self.assertEqual(json.loads(payload.decode("utf-8"))["entries"][0]["amount_cents"],
                         4500)

    def test_missing_file_downloads_default_ledger(self):
        """账本还不存在时下载默认空账本，而不是报错 —— 备份页要随时可用。"""
        payload, _ = backup.build_download_payload(self.data_file)
        data = json.loads(payload.decode("utf-8"))
        self.assertEqual(data["entries"], [])
        self.assertEqual(len(data["categories"]), 11)
        # 顺带把默认账本初始化了
        self.assertTrue(self.data_file.exists())

    def test_filename_is_ascii_json(self):
        _, filename = backup.build_download_payload(self.data_file)
        self.assertTrue(filename.endswith(".json"))
        filename.encode("ascii")
        self.assertTrue(filename.startswith("pocketledger_backup_"))


class TestCreateBackup(BackupTestBase):
    """本地留存备份。"""

    def test_backup_copies_current_content(self):
        self.write_ledger(self.make_ledger(note="备份时的内容"))
        name = backup.create_backup(data_file=self.data_file, backup_dir=self.backup_dir)

        backup_path = self.backup_dir / name
        self.assertTrue(backup_path.exists())
        content = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(content["entries"][0]["note"], "备份时的内容")

    def test_list_backups_newest_first(self):
        self.write_ledger(self.make_ledger())
        first = backup.create_backup(data_file=self.data_file, backup_dir=self.backup_dir)
        second = backup.create_backup(data_file=self.data_file, backup_dir=self.backup_dir)
        self.assertEqual(backup.list_backups(backup_dir=self.backup_dir),
                         sorted([first, second], reverse=True))

    def test_backup_of_missing_ledger_rejected(self):
        """账本还不存在时没有可备份的内容，明确报错而不是备份空壳。"""
        with self.assertRaises(backup.BackupError):
            backup.create_backup(data_file=self.data_file, backup_dir=self.backup_dir)

    def test_backup_filename_has_prefix_and_timestamp(self):
        self.write_ledger(self.make_ledger())
        name = backup.create_backup(data_file=self.data_file, backup_dir=self.backup_dir)
        self.assertTrue(name.startswith("backup_"))
        name.encode("ascii")


class TestRestore(BackupTestBase):
    """恢复上传的备份：成功路径（T13）与失败保护（T14）。"""

    def test_restore_round_trip(self):
        """T13 完整链路：备份 -> 改账本 -> 恢复，回到备份时状态并留存恢复前备份。"""
        original = self.make_ledger(note="备份时的备注", amount=4500)
        self.write_ledger(original)

        # 下载一份备份
        payload, _ = backup.build_download_payload(self.data_file)

        # 修改账本
        changed = self.make_ledger(note="恢复前的新备注", amount=9900)
        self.write_ledger(changed)
        self.assertEqual(self.read_ledger()["entries"][0]["amount_cents"], 9900)

        # 用刚才的备份恢复
        result = backup.restore_upload(payload, data_file=self.data_file,
                                       backup_dir=self.backup_dir)
        self.assertEqual(result["entries"], 1)
        self.assertEqual(result["categories"], 2)
        self.assertTrue(result["pre_restore_backup"])

        # 账本回到备份时状态
        restored = self.read_ledger()
        self.assertEqual(restored["entries"][0]["note"], "备份时的备注")
        self.assertEqual(restored["entries"][0]["amount_cents"], 4500)

        # 恢复前账本已自动留存
        pre_path = self.backup_dir / result["pre_restore_backup"]
        self.assertTrue(pre_path.exists())
        self.assertTrue(result["pre_restore_backup"].startswith("pre_restore_"))
        pre_content = json.loads(pre_path.read_text(encoding="utf-8"))
        self.assertEqual(pre_content["entries"][0]["note"], "恢复前的新备注")

    def test_restore_accepts_bom_payload(self):
        """Windows 记事本保存的 JSON 常带 BOM，恢复要兼容。"""
        payload = self.encode(self.make_ledger(), bom=True)
        result = backup.restore_upload(payload, data_file=self.data_file,
                                       backup_dir=self.backup_dir)
        self.assertEqual(result["entries"], 1)

    def test_restore_without_existing_ledger(self):
        """首次恢复：没有旧账本可留存，恢复仍然成功。"""
        payload = self.encode(self.make_ledger())
        result = backup.restore_upload(payload, data_file=self.data_file,
                                       backup_dir=self.backup_dir)
        self.assertIsNone(result["pre_restore_backup"])
        self.assertEqual(self.read_ledger()["entries"][0]["note"], "晚餐")

    # ---- T14：失败场景，现有数据必须保持原样 ----

    def test_oversized_payload_rejected(self):
        self.write_ledger(self.make_ledger())
        before = self.read_ledger()
        payload = b"x" * (backup.MAX_UPLOAD_BYTES + 1)
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(payload, data_file=self.data_file,
                                  backup_dir=self.backup_dir)
        self.assertEqual(self.read_ledger(), before)

    def test_empty_payload_rejected(self):
        self.write_ledger(self.make_ledger())
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(b"", data_file=self.data_file,
                                  backup_dir=self.backup_dir)

    def test_broken_json_rejected_data_unchanged(self):
        self.write_ledger(self.make_ledger())
        before = self.read_ledger()
        with self.assertRaises(backup.BackupError) as ctx:
            backup.restore_upload("{不是 JSON".encode("utf-8"), data_file=self.data_file,
                                  backup_dir=self.backup_dir)
        self.assertIn("JSON", str(ctx.exception))
        self.assertEqual(self.read_ledger(), before)
        # 失败的恢复不能留下"恢复前备份"，让用户误以为恢复发生过
        self.assertEqual(backup.list_backups(backup_dir=self.backup_dir), [])

    def test_non_utf8_rejected(self):
        self.write_ledger(self.make_ledger())
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(b"\xff\xfe\x00bad", data_file=self.data_file,
                                  backup_dir=self.backup_dir)

    def test_missing_category_reference_rejected(self):
        """类别引用不存在的备份 → 拒绝，原账本保持原样（T14）。"""
        self.write_ledger(self.make_ledger())
        before = self.read_ledger()
        bad = self.make_ledger()
        bad["entries"][0]["category_id"] = "ghost"
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(self.encode(bad), data_file=self.data_file,
                                  backup_dir=self.backup_dir)
        self.assertEqual(self.read_ledger(), before)
        self.assertEqual(backup.list_backups(backup_dir=self.backup_dir), [])

    def test_bad_version_rejected(self):
        self.write_ledger(self.make_ledger())
        before = self.read_ledger()
        bad = self.make_ledger()
        bad["schema_version"] = 2
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(self.encode(bad), data_file=self.data_file,
                                  backup_dir=self.backup_dir)
        self.assertEqual(self.read_ledger(), before)

    def test_invalid_entries_rejected(self):
        """金额为 0、日期不存在、重复 ID 的备份都不许恢复。"""
        cases = []
        bad = self.make_ledger()
        bad["entries"][0]["amount_cents"] = 0
        cases.append(bad)
        bad = self.make_ledger()
        bad["entries"][0]["date"] = "2026-02-30"
        cases.append(bad)
        bad = self.make_ledger()
        bad["entries"].append(dict(bad["entries"][0]))
        cases.append(bad)

        self.write_ledger(self.make_ledger())
        before = self.read_ledger()
        for bad_case in cases:
            with self.subTest(note=bad_case["entries"][0].get("note")):
                with self.assertRaises(backup.BackupError):
                    backup.restore_upload(self.encode(bad_case),
                                          data_file=self.data_file,
                                          backup_dir=self.backup_dir)
        self.assertEqual(self.read_ledger(), before)

    def test_none_payload_rejected(self):
        with self.assertRaises(backup.BackupError):
            backup.restore_upload(None, data_file=self.data_file,
                                  backup_dir=self.backup_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
