# 模型、账目服务与预算模块

负责人：佘博海 102401137

交付文件：`lotterytally/models.py`、`lotterytally/entries.py`、`lotterytally/budget.py`、`templates/budget.html`

本文说明三个模块的接口、预算口径、页面接入方式和自测结果，供应用入口（`app.py`）与其他模块联调时对照。

## 一、数据结构约定

与实施方案 4.1 节一致，账本是一个 JSON 对象；本模块只认下面这些字段：

```json
{
  "schema_version": 1,
  "categories": [{"id": "food", "name": "饮食", "type": "expense", "active": true}],
  "entries": [{
    "id": "6d623d60-f20b-43f2-9723-0f9a87349d89",
    "date": "2026-11-07",
    "type": "expense",
    "amount_cents": 4500,
    "category_id": "food",
    "note": "晚餐",
    "created_at": "2026-11-07T16:20:00+08:00",
    "updated_at": "2026-11-07T16:20:00+08:00"
  }],
  "budgets": {
    "2026-11": {"total_cents": 150000, "category_limits": {"food": 50000}}
  }
}
```

- 金额一律是**正整数分**，方向由 `type`（`income` / `expense`）决定。
- `budgets` **只存上限**，不存已用金额；已用金额每次由账目实时汇总。
- `created_at` / `updated_at` 必须带时区，用于同一天排序与编辑时间记录。

## 二、接口

### models.py —— 统一账本模型

| 接口 | 职责 |
| --- | --- |
| `SCHEMA_VERSION`、`TYPE_INCOME`、`TYPE_EXPENSE`、`ENTRY_TYPES`、`TYPE_LABELS` | 常量，避免各模块硬编码字段值 |
| `new_ledger(categories=None, entries=None, budgets=None)` | 构造空账本（默认类别由类别模块传入） |
| `validate_ledger_payload(payload)` | 返回全部结构问题列表，空列表表示通过 |
| `assert_valid_ledger(payload)` | 不合法时抛 `LedgerFormatError(problems)` |
| `sort_entries_newest_first(entries)` | 最近记录口径：`date` → `created_at` → `id` 倒序 |
| `entry_sort_key(entry)` | 上面的排序键 |
| `parse_date` / `is_valid_date` / `parse_timestamp` | 日期与时间校验 |
| `parse_month` / `month_of` / `entry_month` / `current_month` | 月份归属 |
| `shift_month` / `month_bounds` / `month_label` / `months_in_ledger` | 页面月份切换与展示 |
| `find_entry` / `find_category` / `category_index` / `category_name` | 查找 |
| `new_entry_id()` / `now_iso()` / `format_cents(cents)` | UUID、带时区时间、整数分转元字符串 |

结构校验覆盖：`schema_version`、类别 ID 与名称唯一性（同一收支类型）、账目 ID 唯一、日期真实存在、金额为正整数且非布尔、类别引用与类型一致、备注长度、时间戳合法、预算月份格式、预算分类必须是支出类别。

### entries.py —— 账目服务

| 接口 | 说明 |
| --- | --- |
| `create_entry(form_data) -> dict` | 新增一笔账目，返回保存后的记录 |
| `update_entry(entry_id, form_data) -> dict` | 保留原 `id` 与 `created_at`，刷新 `updated_at` |
| `delete_entry(entry_id) -> dict` | 删除并返回被删记录 |
| `get_entry(entry_id, ledger=None)` | 不存在返回 `None` |
| `list_entries(ledger=None, newest_first=True)` | 全部账目 |
| `recent_entries(limit=10, ledger=None)` | 首页最近 N 笔 |

业务规则（需要看到账本才能判断，因此放在存储事务内执行）：

1. 类别必须存在；
2. 类别类型必须与账目类型一致；
3. 新账目不能使用停用类别；
4. 编辑历史账目时，允许保留它原来的停用类别，换成其他类别则必须是启用状态。

错误统一为 `EntryError` 子类，`errors` 是 `{字段名: 中文提示}`：`EntryValidationError` 用于回填表单，`EntryNotFound` 用于已删除或过期链接。

### budget.py —— 预算规则与页面

| 接口 | 说明 |
| --- | --- |
| `budget_status(ledger, month)` | 返回该月总预算与分类预算的上限、已用、剩余、超支、状态与百分比 |
| `parse_budget_form(form_data, ledger)` | 校验预算表单，返回 `(month, total_cents, {category_id: cents})`；空字符串表示清除该项 |
| `save_budget(month, total_cents, category_limits)` | 写入预算并返回保存后的 `budget_status` |
| `bp` | Flask 蓝图，提供 `GET /budget` 与 `POST /budget` |

`budget_status()` 返回结构：

```python
{
  "month": "2026-11", "month_label": "2026 年 11 月",
  "total": {"has_limit": True, "limit_cents": 150000, "used_cents": 61900,
            "remaining_cents": 88100, "overspent_cents": 0,
            "status": "ok", "status_text": "剩余 881.00 元",
            "percent": 41, "bar_percent": 41},
  "categories": [
    {"category_id": "food", "name": "饮食", "active": True,
     "has_limit": True, "limit_cents": 50000, "used_cents": 47000,
     "remaining_cents": 3000, "overspent_cents": 0,
     "status": "ok", "status_text": "剩余 30.00 元", "percent": 94, "bar_percent": 94}
  ],
  "expense_cents": 61900, "expense_count": 4,
  "budgeted_count": 1, "unbudgeted_count": 1, "unbudgeted_cents": 8900
}
```

## 三、预算口径

1. **按账目日期归属月份**：录入一笔上月支出，更新的是上月预算，不看录入时间。
2. **收入不进入支出预算**：`expense_cents` 只累加 `type == "expense"` 的账目。
3. **总预算与分类预算独立**：同一笔饮食支出同时计入总预算和饮食预算，但两者不要求相加相等。
4. **状态按整数分判断**：已用 < 上限 → 剩余；已用 == 上限 → 额度已用完；已用 > 上限 → 超支金额。
5. **进度条封顶**：`bar_percent = min(percent, 100)`，旁边百分比照实显示（超支时可能 103%）。
6. **停用类别仍计入预算**：分类停用只影响新账选择，历史支出照常统计与展示。

## 四、页面接入

应用入口（魏之皓的 `app.py`）需要：

```python
from flask import Flask
from lotterytally.budget import bp as budget_bp

app = Flask(__name__, template_folder="templates")   # 仓库根的 templates/
app.secret_key = "..."                                # flash 需要
app.register_blueprint(budget_bp)
```

- 模板 `templates/budget.html` 继承 `base.html`，依赖 `{% block title %}` 与 `{% block content %}` 两个块。
- 蓝图注册时会向应用注册 `yuan` 模板过滤器（整数分 → 两位小数元），预算页用它显示金额。
- `GET /budget?month=YYYY-MM` 查看，`POST /budget` 保存；保存成功重定向，校验失败返回 400 并保留输入。

## 五、自测

依赖只有 Flask。`pyproject.toml` 的项目依赖由 filters 模块的 PR #1 补齐，合入后可以直接
`pip install -e .`；在它合入之前，本分支按下面的方式安装并运行测试：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

当前 **75 个用例全部通过**（Windows / Python 3.14.6 / Flask 3.1.3）：

- `test/test_models.py`（27）：日期真实性、闰年 2 月、跨年月份换算、排序口径、账本结构校验的各种破坏场景；
- `test/test_entries.py`（19）：金额文本、非法日期、类别存在/类型/停用规则、编辑保留 `id` 与 `created_at`、删除只删目标、最近 10 笔口径；
- `test/test_budget.py`（29）：T08 超支 15 元、T09 改金额后剩余 10 元、T10 改到上月、正好用完、未设预算、进度封顶、保存与清除、页面 GET/POST。

金额文本的最终规则由 `validators` 模块（何慧渊）负责；`entries` 侧的测试验证的是“错误被正确包装成 `EntryValidationError` 并带字段名”。

### 测试替身说明

`test/support.py` 按实施方案 5.2 的接口约定，为尚未合入的两个依赖提供**仅测试使用**的内存替身：

- `lotterytally.storage`：`load_ledger()` / `update_ledger(change)` / `write_ledger(ledger)`，内存账本 + 深拷贝提交；
- `lotterytally.validators`：`parse_amount_cents(text)` / `validate_entry_fields(form)` / `ValidationError`。

替身不进入 `lotterytally` 包。真实模块合入后，把 `test/support.py` 中的替身替换为真实导入即可，业务代码无需改动。

## 六、待联调

| 依赖 | 状态 | 联调方式 |
| --- | --- | --- |
| `lotterytally/storage.py`（李义东） | 未合入 | 合入后跑一遍真实落盘：新增 → 重启 → 记录仍在 |
| `lotterytally/validators.py`（何慧渊） | 未合入 | 合入后替换测试替身，确认金额错误提示一致 |
| `categories.py`（刘谦益） | 未合入 | 默认类别 ID 需与 `test/support.py` 的夹具一致（salary / food 等） |
| `app.py`、`base.html`（魏之皓） | 未合入 | 按第四节的注册方式接入，打开 `/budget` 核对演示数字 |

已知限制：本模块不负责 `app.py` 启动参数、数据文件路径与备份；`budget.py` 的页面依赖 Flask，纯计算函数（`budget_status`）不依赖 Web 上下文，便于独立测试。