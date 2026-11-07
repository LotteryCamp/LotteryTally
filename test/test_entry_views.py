"""单元测试：lotterytally.entry_views 记账界面路由（陈常群模块）。

负责人：陈常群 102401318
运行方式：python -m unittest discover -s test -v

本用例复用 test/support.py 里的 storage / validators 契约替身，让路由真正走
lotterytally.entries 的 create_entry / update_entry / delete_entry 服务，
覆盖方案验收项：
- T02：新增收入与支出，保存后写入账本，响应重定向到流水页
- T03：金额 0 / -1 / 12.345、不存在的日期 → 对应字段报错，账本未新增记录
- 快速复制：copy_from 预填来源账目的金额/类别/备注，日期默认今天
- 编辑：修改金额后 amount_cents 更新，created_at 保留，updated_at 刷新
- 删除：确认后账目从账本移除
- 类别联动：新增表单只渲染当前类型的启用类别
- 边界：编辑/删除不存在的 id 返回 404
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import Flask
from jinja2 import ChoiceLoader, DictLoader

from lotterytally import entry_views, models
from lotterytally.categories import get_default_categories
from support import LedgerTestCase, storage

TODAY = date(2026, 11, 7)

#: 与 support.BASE_TEMPLATE_STUB 类似的最小父模板，保留 navigation / content 两个入口。
_BASE_STUB = (
    "<!doctype html><html><head><title>{% block title %}{% endblock %}</title></head><body>"
    '<nav>{% for item in navigation %}<a href="{{ item.path }}">{{ item.label }}</a>{% endfor %}</nav>'
    "{% block content %}{% endblock %}</body></html>"
)


def _categories():
    """真实默认类别 + 一个自定义停用类别（用于编辑时保留已停用类别）。"""
    categories = get_default_categories()
    categories.append(
        {"id": "retired_snack", "name": "夜宵", "type": "expense", "active": False}
    )
    return categories


def make_entry(
    entry_id,
    day="2026-11-07",
    kind="expense",
    amount_cents=4500,
    category_id="food",
    note="晚餐",
    created_at="2026-11-07T08:00:00+08:00",
):
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


class EntryViewsTests(LedgerTestCase):
    def setUp(self):
        super().setUp()
        storage.write_ledger(models.new_ledger(_categories()))

    def _seed(self, entries):
        storage.write_ledger(models.new_ledger(_categories(), entries=entries))

    def _client(self):
        package_dir = Path(entry_views.__file__).resolve().parent
        app = Flask(
            __name__,
            template_folder=str(package_dir / "templates"),
            static_folder=str(package_dir / "static"),
        )
        app.secret_key = "test-only"
        app.config["TESTING"] = True
        app.config["LEDGER_TODAY"] = TODAY
        app.register_blueprint(entry_views.entry_bp)
        app.jinja_loader = ChoiceLoader(
            [DictLoader({"base.html": _BASE_STUB}), app.jinja_loader]
        )
        return app.test_client()

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
        client = self._client()
        resp = self._post_new(client)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/entries")

        entries = storage.load_ledger()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["amount_cents"], 4550)
        self.assertEqual(entries[0]["type"], "expense")
        self.assertEqual(entries[0]["category_id"], "food")
        self.assertEqual(entries[0]["note"], "晚餐")
        self.assertIsNotNone(models.parse_timestamp(entries[0]["created_at"]))

    def test_create_income_uses_income_category(self):
        client = self._client()
        resp = self._post_new(
            client, type="income", category_id="salary", amount="3000", note="十一月工资"
        )
        self.assertEqual(resp.status_code, 302)
        entry = storage.load_ledger()["entries"][0]
        self.assertEqual(entry["amount_cents"], 300000)
        self.assertEqual(entry["type"], "income")

    def test_amount_variants_convert_to_cents(self):
        for text, cents in (("45", 4500), ("45.5", 4550), ("45.50", 4550)):
            with self.subTest(amount=text):
                storage.write_ledger(models.new_ledger(_categories()))
                client = self._client()
                resp = self._post_new(client, amount=text)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(storage.load_ledger()["entries"][0]["amount_cents"], cents)

    # ---------- 校验失败：保留输入、不落库 ----------
    def test_invalid_amounts_are_rejected_without_persisting(self):
        for bad in ("0", "-1", "12.345", "1e3", "abc"):
            with self.subTest(amount=bad):
                client = self._client()
                resp = self._post_new(client, amount=bad)
                self.assertEqual(resp.status_code, 400)
                html = resp.get_data(as_text=True)
                self.assertIn("金额", html)
                self.assertEqual(storage.load_ledger()["entries"], [])
                self.assertIn(bad, html)

    def test_nonexistent_date_is_rejected(self):
        client = self._client()
        resp = self._post_new(client, date="2026-02-30")
        self.assertEqual(resp.status_code, 400)
        html = resp.get_data(as_text=True)
        self.assertIn("日期", html)
        self.assertIn("2026-02-30", html)
        self.assertEqual(storage.load_ledger()["entries"], [])

    def test_category_type_mismatch_is_rejected(self):
        client = self._client()
        resp = self._post_new(client, type="expense", category_id="salary")
        self.assertEqual(resp.status_code, 400)
        html = resp.get_data(as_text=True)
        self.assertIn("不一致", html)
        self.assertEqual(storage.load_ledger()["entries"], [])

    def test_note_over_200_chars_is_rejected(self):
        client = self._client()
        resp = self._post_new(client, note="长" * 201)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("备注", resp.get_data(as_text=True))
        self.assertEqual(storage.load_ledger()["entries"], [])

    # ---------- 快速复制 ----------
    def test_copy_from_prefills_source_with_today_date(self):
        self._seed([
            make_entry(
                "e1", day="2026-11-01", amount_cents=12345,
                category_id="traffic", note="打车",
            )
        ])
        client = self._client()
        resp = client.get("/entries/new?copy_from=e1")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("123.45", html)                 # 金额
        self.assertIn("打车", html)                    # 备注
        self.assertIn("交通", html)                    # 类别
        self.assertIn('value="2026-11-07"', html)      # 日期默认今天
        self.assertNotIn("2026-11-01", html)           # 而非来源日期

    # ---------- 编辑 ----------
    def test_edit_updates_fields_and_keeps_created_at(self):
        self._seed([
            make_entry("e1", amount_cents=4500, note="晚餐",
                       created_at="2026-11-07T08:00:00+08:00")
        ])
        client = self._client()
        resp = client.post("/entries/e1/edit", data={
            "date": "2026-11-07", "type": "expense", "amount": "20",
            "category_id": "food", "note": "午餐改口",
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

        entry = storage.load_ledger()["entries"][0]
        self.assertEqual(entry["amount_cents"], 2000)
        self.assertEqual(entry["note"], "午餐改口")
        self.assertEqual(entry["created_at"], "2026-11-07T08:00:00+08:00")  # 保留
        self.assertNotEqual(entry["updated_at"], "2026-11-07T08:00:00+08:00")  # 刷新

    def test_edit_form_shows_existing_values_and_inactive_category(self):
        self._seed([
            make_entry("e1", amount_cents=4500, category_id="retired_snack",
                       note="夜宵", created_at="2026-11-07T08:00:00+08:00")
        ])
        client = self._client()
        resp = client.get("/entries/e1/edit")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("45.00", html)
        self.assertIn("夜宵", html)
        self.assertIn("已停用", html)

    # ---------- 删除 ----------
    def test_delete_removes_entry(self):
        self._seed([make_entry("e1")])
        client = self._client()
        resp = client.post("/entries/e1/delete", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(storage.load_ledger()["entries"], [])

    # ---------- 边界 ----------
    def test_edit_missing_entry_returns_404(self):
        client = self._client()
        resp = client.get("/entries/nope/edit")
        self.assertEqual(resp.status_code, 404)

    def test_new_form_renders_active_categories_only(self):
        client = self._client()
        resp = client.get("/entries/new")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('value="food"', html)               # 支出启用类别
        self.assertIn('value="traffic"', html)
        self.assertNotIn('value="salary"', html)          # 收入类别默认隐藏
        self.assertNotIn('value="retired_snack"', html)   # 停用类别不出现

    def test_static_entry_js_is_served(self):
        client = self._client()
        resp = client.get("/static/js/entry.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("data-delete-entry", resp.get_data(as_text=True))


if __name__ == "__main__":
    import unittest

    unittest.main()
