"""单元测试：lotterytally.entry_views 记账界面路由（陈常群模块）。

负责人：陈常群 102401318
运行方式：python -m unittest discover -s test -v
覆盖方案验收用例：
- T02：新增收入与支出，保存后写入账本，响应重定向到流水页
- T03：金额 0 / -1 / 12.345、不存在的日期 → 对应字段报错，账本未新增记录
- 快速复制：copy_from 预填来源账目的金额/类别/备注，日期默认今天
- 编辑：修改金额后 amount_cents 更新，created_at 保留，updated_at 刷新
- 删除：确认后账目从账本移除
- 类别联动：表单渲染当前类型的启用类别，编辑时保留已停用的原类别
- 边界：编辑/删除不存在的 id 返回 404
"""

from __future__ import annotations

import re
import unittest
from datetime import date

from lotterytally.entry_views import create_app


TODAY = date(2026, 11, 7)
NOW = "2026-11-07T16:20:00+08:00"

SAMPLE_CATEGORIES = [
    {"id": "food", "name": "饮食", "type": "expense", "active": True},
    {"id": "traffic", "name": "交通", "type": "expense", "active": True},
    {"id": "retired_snack", "name": "夜宵", "type": "expense", "active": False},
    {"id": "salary", "name": "工资", "type": "income", "active": True},
    {"id": "scholarship", "name": "奖学金", "type": "income", "active": True},
]


def make_entry(entry_id, day="2026-11-07", kind="expense", amount_cents=4500,
               category_id="food", note="晚餐",
               created_at="2026-11-07T08:00:00+08:00"):
    return {
        "id": entry_id,
        "date": day,
        "type": kind,
        "amount_cents": amount_cents,
        "category_id": category_id,
        "note": note,
        "created_at": created_at,
        "updated_at": created_at,
    }


class EntryViewsTests(unittest.TestCase):
    def _make_client(self, entries):
        state = {
            "ledger": {
                "schema_version": 1,
                "categories": [dict(c) for c in SAMPLE_CATEGORIES],
                "entries": [dict(e) for e in entries],
                "budgets": {},
            }
        }
        app = create_app(
            ledger_loader=lambda: state["ledger"],
            ledger_saver=lambda data: state.__setitem__("ledger", data),
            today=TODAY,
            now=NOW,
        )
        app.config["TESTING"] = True
        return app.test_client(), state

    def _post_new(self, client, **overrides):
        data = {
            "date": "2026-11-07",
            "type": "expense",
            "amount": "45.50",
            "category_id": "food",
            "note": "晚餐",
        }
        data.update(overrides)
        return client.post("/entries/new", data=data, follow_redirects=False)

    # ---------- 新增 ----------
    def test_create_expense_persists_and_redirects(self):
        client, state = self._make_client([])
        resp = self._post_new(client)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/entries")

        entries = state["ledger"]["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["amount_cents"], 4550)
        self.assertEqual(entries[0]["type"], "expense")
        self.assertEqual(entries[0]["category_id"], "food")
        self.assertEqual(entries[0]["note"], "晚餐")
        self.assertEqual(entries[0]["created_at"], NOW)

    def test_create_income_uses_income_category(self):
        client, state = self._make_client([])
        resp = self._post_new(client, type="income", category_id="salary",
                              amount="3000", note="十一月工资")
        self.assertEqual(resp.status_code, 302)
        entry = state["ledger"]["entries"][0]
        self.assertEqual(entry["amount_cents"], 300000)
        self.assertEqual(entry["type"], "income")

    def test_amount_variants_convert_to_cents(self):
        for text, cents in (("45", 4500), ("45.5", 4550), ("45.50", 4550)):
            with self.subTest(amount=text):
                client, state = self._make_client([])
                resp = self._post_new(client, amount=text)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(state["ledger"]["entries"][0]["amount_cents"], cents)

    # ---------- 校验失败：保留输入、不落库 ----------
    def test_invalid_amounts_are_rejected_without_persisting(self):
        for bad in ("0", "-1", "12.345", "1e3", "abc"):
            with self.subTest(amount=bad):
                client, state = self._make_client([])
                resp = self._post_new(client, amount=bad)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("金额", resp.get_data(as_text=True))
                self.assertEqual(state["ledger"]["entries"], [])
                # 保留用户输入
                self.assertIn(bad, resp.get_data(as_text=True))

    def test_nonexistent_date_is_rejected(self):
        client, state = self._make_client([])
        resp = self._post_new(client, date="2026-02-30")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("日期不存在", resp.get_data(as_text=True))
        self.assertEqual(state["ledger"]["entries"], [])

    def test_category_type_mismatch_is_rejected(self):
        client, state = self._make_client([])
        resp = self._post_new(client, type="expense", category_id="salary")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不匹配", resp.get_data(as_text=True))
        self.assertEqual(state["ledger"]["entries"], [])

    def test_note_over_200_chars_is_rejected(self):
        client, state = self._make_client([])
        resp = self._post_new(client, note="长" * 201)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("备注", resp.get_data(as_text=True))
        self.assertEqual(state["ledger"]["entries"], [])

    # ---------- 快速复制 ----------
    def test_copy_from_prefills_source_with_today_date(self):
        client, _ = self._make_client([
            make_entry("e1", day="2026-11-01", amount_cents=12345,
                       category_id="traffic", note="打车")
        ])
        resp = client.get("/entries/new?copy_from=e1")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("123.45", html)      # 金额
        self.assertIn("打车", html)         # 备注
        self.assertIn("value=\"2026-11-07\"", html)  # 日期默认今天，而非来源日期
        self.assertIn("交通", html)

    # ---------- 编辑 ----------
    def test_edit_updates_fields_and_keeps_created_at(self):
        client, state = self._make_client([
            make_entry("e1", amount_cents=4500, note="晚餐",
                       created_at="2026-11-07T08:00:00+08:00")
        ])
        resp = client.post("/entries/e1/edit", data={
            "date": "2026-11-07", "type": "expense", "amount": "20",
            "category_id": "food", "note": "午餐改口",
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

        entry = state["ledger"]["entries"][0]
        self.assertEqual(entry["amount_cents"], 2000)
        self.assertEqual(entry["note"], "午餐改口")
        self.assertEqual(entry["created_at"], "2026-11-07T08:00:00+08:00")  # 保留
        self.assertEqual(entry["updated_at"], NOW)  # 刷新

    def test_edit_form_shows_existing_values_and_inactive_category(self):
        client, _ = self._make_client([
            make_entry("e1", amount_cents=4500, category_id="retired_snack",
                       note="夜宵", created_at="2026-11-07T08:00:00+08:00")
        ])
        resp = client.get("/entries/e1/edit")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("45.00", html)
        self.assertIn("夜宵", html)
        # 已停用类别在编辑时应保留显示
        self.assertIn("已停用", html)

    # ---------- 删除 ----------
    def test_delete_removes_entry(self):
        client, state = self._make_client([make_entry("e1")])
        resp = client.post("/entries/e1/delete", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(state["ledger"]["entries"], [])

    # ---------- 边界 ----------
    def test_edit_missing_entry_returns_404(self):
        client, _ = self._make_client([])
        resp = client.get("/entries/nope/edit")
        self.assertEqual(resp.status_code, 404)

    def test_new_form_renders_active_categories_only(self):
        client, _ = self._make_client([])
        resp = client.get("/entries/new")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("饮食", html)
        self.assertIn("交通", html)
        # 已停用的「夜宵」不应出现在新增表单的可选项里
        self.assertNotIn("夜宵", html)
        # 收入类别默认隐藏，由 JS 联动切换
        self.assertNotIn("工资", html)

    def test_static_entry_js_is_served(self):
        client, _ = self._make_client([])
        resp = client.get("/static/entry/js/entry.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data-delete-entry", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
