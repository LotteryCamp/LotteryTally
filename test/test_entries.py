"""entries.py 的单元测试：金额规则、类别业务规则、编辑删除与查询口径。"""

from __future__ import annotations

import unittest

import support  # noqa: F401  # 安装存储/校验契约替身
from support import LedgerTestCase, storage

from lotterytally import entries as entry_service
from lotterytally import models


class CreateEntryTests(LedgerTestCase):
    def test_saves_cents_uuid_and_timestamps(self):
        created = self.add_entry(
            entry_type="income",
            amount="3000",
            date="2025-03-01",
            category_id="salary",
            note="  本月工资  ",
        )
        self.assertEqual(created["amount_cents"], 300000)
        self.assertEqual(created["note"], "本月工资")  # 首尾空白被去掉
        self.assertEqual(len(created["id"]), 36)
        self.assertEqual(created["created_at"], created["updated_at"])
        self.assertIsNotNone(models.parse_timestamp(created["created_at"]))
        self.assertEqual(len(self.entries()), 1)

    def test_amount_accepts_one_or_two_decimals(self):
        for text, expected in (("45", 4500), ("45.5", 4550), ("45.50", 4550), ("0.01", 1)):
            with self.subTest(text=text):
                created = self.add_entry(amount=text)
                self.assertEqual(created["amount_cents"], expected)

    def test_rejects_invalid_amounts_and_writes_nothing(self):
        for text in ("0", "0.00", "-1", "-0.5", "12.345", "1e3", "45.", ".5", "abc", "", "   "):
            with self.subTest(text=text):
                with self.assertRaises(entry_service.EntryValidationError) as ctx:
                    self.add_entry(amount=text)
                self.assertIn("amount", ctx.exception.errors)
        self.assertEqual(self.entries(), [])  # 非法输入没有产生任何记录

    def test_rejects_impossible_dates(self):
        for value in ("2026-02-30", "2026-13-01", "2026/01/01", ""):
            with self.subTest(value=value):
                with self.assertRaises(entry_service.EntryValidationError) as ctx:
                    self.add_entry(date=value)
                self.assertIn("date", ctx.exception.errors)

    def test_rejects_missing_or_unknown_category(self):
        for category_id in ("", "ghost"):
            with self.subTest(category_id=category_id):
                with self.assertRaises(entry_service.EntryValidationError) as ctx:
                    self.add_entry(category_id=category_id)
                self.assertIn("category_id", ctx.exception.errors)

    def test_rejects_category_of_the_other_type(self):
        with self.assertRaises(entry_service.EntryValidationError) as ctx:
            self.add_entry(entry_type="expense", category_id="salary")
        self.assertIn("不一致", ctx.exception.errors["category_id"])

    def test_rejects_invalid_entry_type(self):
        with self.assertRaises(entry_service.EntryValidationError) as ctx:
            self.add_entry(entry_type="transfer")
        self.assertIn("type", ctx.exception.errors)

    def test_rejects_inactive_category_for_new_entry(self):
        storage.update_ledger(
            lambda ledger: models.find_category(ledger, "food").update({"active": False})
        )
        with self.assertRaises(entry_service.EntryValidationError) as ctx:
            self.add_entry()
        self.assertIn("已停用", ctx.exception.errors["category_id"])

    def test_note_length_limit(self):
        self.add_entry(note="备" * models.NOTE_MAX_LENGTH)
        with self.assertRaises(entry_service.EntryValidationError) as ctx:
            self.add_entry(note="备" * (models.NOTE_MAX_LENGTH + 1))
        self.assertIn("note", ctx.exception.errors)


class UpdateDeleteTests(LedgerTestCase):
    def test_update_preserves_id_and_created_at(self):
        created = self.add_entry(amount="45")
        updated = entry_service.update_entry(
            created["id"],
            {
                "type": "expense",
                "amount": "20",
                "date": "2025-03-10",
                "category_id": "food",
                "note": "改价",
            },
        )
        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["created_at"], created["created_at"])
        self.assertEqual(updated["amount_cents"], 2000)
        self.assertEqual(updated["date"], "2025-03-10")
        self.assertGreaterEqual(
            models.parse_timestamp(updated["updated_at"]),
            models.parse_timestamp(created["updated_at"]),
        )
        self.assertEqual(len(self.entries()), 1)

    def test_update_keeps_inactive_current_category(self):
        created = self.add_entry()
        storage.update_ledger(
            lambda ledger: models.find_category(ledger, "food").update({"active": False})
        )
        updated = entry_service.update_entry(
            created["id"],
            {
                "type": "expense",
                "amount": "50",
                "date": created["date"],
                "category_id": "food",
                "note": "",
            },
        )
        self.assertEqual(updated["category_id"], "food")
        self.assertEqual(updated["amount_cents"], 5000)

    def test_update_cannot_switch_to_another_inactive_category(self):
        created = self.add_entry()

        def add_paused_category(ledger):
            ledger["categories"].append(
                {"id": "paused", "name": "夜宵", "type": "expense", "active": False}
            )

        storage.update_ledger(add_paused_category)
        with self.assertRaises(entry_service.EntryValidationError) as ctx:
            entry_service.update_entry(
                created["id"],
                {
                    "type": "expense",
                    "amount": "50",
                    "date": created["date"],
                    "category_id": "paused",
                    "note": "",
                },
            )
        self.assertIn("已停用", ctx.exception.errors["category_id"])

    def test_update_missing_entry_raises_not_found(self):
        with self.assertRaises(entry_service.EntryNotFound):
            entry_service.update_entry(
                "missing",
                {
                    "type": "expense",
                    "amount": "20",
                    "date": "2025-03-05",
                    "category_id": "food",
                    "note": "",
                },
            )

    def test_delete_removes_only_the_target_entry(self):
        first = self.add_entry(amount="10")
        second = self.add_entry(amount="20")
        third = self.add_entry(amount="30")
        removed = entry_service.delete_entry(second["id"])
        self.assertEqual(removed["id"], second["id"])
        remaining = [entry["id"] for entry in self.entries()]
        self.assertEqual(remaining, [first["id"], third["id"]])

    def test_delete_missing_entry_raises_not_found(self):
        with self.assertRaises(entry_service.EntryNotFound):
            entry_service.delete_entry("missing")


class QueryTests(LedgerTestCase):
    def _seed_twelve(self):
        def seed(ledger):
            ledger["entries"] = []
            for index in range(12):
                day = index + 1
                ledger["entries"].append(
                    {
                        "id": f"entry-{day:02d}",
                        "date": f"2025-03-{day:02d}",
                        "type": "expense",
                        "amount_cents": 100 + index,
                        "category_id": "food",
                        "note": "",
                        "created_at": f"2025-03-{day:02d}T12:00:00+08:00",
                        "updated_at": f"2025-03-{day:02d}T12:00:00+08:00",
                    }
                )

        storage.update_ledger(seed)

    def test_recent_entries_returns_latest_ten(self):
        self._seed_twelve()
        recent = entry_service.recent_entries(10)
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0]["date"], "2025-03-12")
        self.assertEqual(recent[-1]["date"], "2025-03-03")

    def test_get_entry(self):
        created = self.add_entry(amount="45")
        self.assertEqual(entry_service.get_entry(created["id"])["amount_cents"], 4500)
        self.assertIsNone(entry_service.get_entry("missing"))

    def test_list_entries_can_skip_sorting(self):
        self.add_entry(amount="10", date="2025-03-01")
        self.add_entry(amount="20", date="2025-03-02")
        newest_first = [entry["date"] for entry in entry_service.list_entries()]
        original = [entry["date"] for entry in entry_service.list_entries(newest_first=False)]
        self.assertEqual(newest_first, ["2025-03-02", "2025-03-01"])
        self.assertEqual(original, ["2025-03-01", "2025-03-02"])

    def test_entry_survives_reload(self):
        created = self.add_entry(amount="45")
        reloaded = storage.load_ledger()
        self.assertEqual(models.find_entry(reloaded, created["id"])["amount_cents"], 4500)


if __name__ == "__main__":
    unittest.main()