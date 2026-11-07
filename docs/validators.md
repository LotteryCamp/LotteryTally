# 校验接口与接入说明

任务负责人：何慧渊 102401135。对应实施方案 4.2、5.2 节，源码为 `lotterytally/validators.py`。

## 金额

```python
from lotterytally.validators import parse_amount_cents, ValidationError

parse_amount_cents("45")     # 4500
parse_amount_cents("45.5")   # 4550
parse_amount_cents("0.29")   # 29
```

金额以元输入，用字符串和 `Decimal` 精确转换成普通 `int` 分。默认要求正数，最多两位小数；拒绝零、负数、科学计数法、千分位、全角数字、单位、残缺小数点和布尔值。首尾空白会去除，整数位不另设方案以外的上限。

`parse_amount_cents(value, field="amount", *, allow_empty=False, allow_zero=False)`：`field` 决定错误归属；`allow_empty=True` 时空值返回 `None`；`allow_zero=True` 是显式可选能力，当前账目、筛选区间和预算的默认口径仍要求正数。

`parse_amount_range(min_value, max_value)` 返回 `(最小分, 最大分)`，两端都可留空；非空须为正数，与当前 `filters.parse_filters` 一致。最小值大于最大值时报 `max_amount` 错误。两端同时非法时收集两端错误。

`parse_budget_limit(value, field="total_cents")` 留空返回 `None`，表示取消预算；非空复用同一金额规则。队友预算表单直接调用 `parse_amount_cents(raw, field="total")` 或 `field="limit_food"` 也受支持，错误提示显示中文，错误键仍保持原字段名。

## 账目表单

```python
from lotterytally.validators import validate_entry_fields, ValidationError

form = {
    "date": "2026-11-07", "type": "expense", "amount": "45.5",
    "category_id": "food", "note": " 晚餐 ",
}
try:
    fields = validate_entry_fields(form)
except ValidationError as exc:
    errors = exc.errors  # {字段名: 中文提示}，可直接回填页面
```

成功返回恰好五个字段：

```python
{
    "date": "2026-11-07", "type": "expense", "amount_cents": 4550,
    "category_id": "food", "note": "晚餐",
}
```

金额输入首选 `amount`，兼容 `amount_yuan`；不会把网页传入的 `amount_cents` 当成已经校验的金额。支持普通字典和有 `.get()` 的表单对象。返回新字典，不修改输入。

日期须为严格的 `YYYY-MM-DD` 和真实日历日，接受 Python 日历支持的 0001–9999 年；拒绝年份零、缺少补零的日期以及不存在的闰日。收支类型规范化为 `income` 或 `expense`，允许首尾空白和英文大小写清洗，其他值报错。备注可空、去除首尾空白；Windows CRLF/CR 换行规范化为 LF 后计数，最多 200 个字符，允许换行和制表符，拒绝其他 ASCII C0 控制字符。

## 类别规则的边界

`validate_entry_fields(form_data, categories=None, existing_entry=None)`：只传表单时，校验类别 ID 非空；传入类别列表时，还校验存在、类型匹配和启用状态。新增不能使用停用类别，编辑可保留原来的停用类别，不能换到另一个停用类别。

正式账目服务应继续在 `storage.update_ledger(change)` 的写入锁内，依据最新账本执行类别判断。当前佘博海的 `entries.py` 正是如此：先调用字段校验，再在写锁内查类别。本模块可选的类别检查不替代服务层的这一步。

## 错误和导入

```python
from lotterytally import validators

try:
    return validators.validate_entry_fields(form_data)
except validators.ValidationError as exc:
    raise EntryValidationError(exc.errors) from exc
```

`ValidationError` 继承 `ValueError`；`errors` 为普通字典，多字段错误一次收集。另提供 `field`、`message`、`to_dict()`。这与筛选模块捕获 `ValueError` 和账目模块透传 `exc.errors` 的写法兼容。

包内统一使用 `lotterytally.validators` 或相对导入，不在仓库根放第二份 `validators.py`。佘博海最新分支在函数内延迟导入 `lotterytally.validators`，包内导入方式已正确，无须再改。

其他辅助接口：`parse_date`、`validate_month`、`month_of`、`normalize_type`、`parse_type_filter`、`validate_note`、`format_cents`。`format_cents(-1500, with_symbol=True)` 返回 `-¥15.00`，不用浮点数格式化。

## 验证

```powershell
python -X utf8 -m unittest discover -s test -v
```

本交付包独立测试为 92 项，包含 10 组 doctest。`test/__init__.py` 使测试目录可按包导入，因此 `-t .` 和 `python -m unittest test.test_validators` 都可使用；这是显式顶层目录的可导入要求，无须归因于标准库重名。

联调脚本会检查共享解析函数及账目、预算引用的模块身份，确保测试调用真实校验源码。验证范围和源码版本以交付包 `verification/` 中的记录为准。
