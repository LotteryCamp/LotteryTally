"""PocketLedger 口袋账本 —— 校验模块。

负责人：何慧渊 102401135
职责：账目日期、收支类型、金额、备注的字段校验，以及金额「元 / 整数分」的双向转换。

对外接口（与《现场编程与技术实施方案》5.2 节锁定接口一致）：

    parse_amount_cents(text)                 金额文本 -> 整数分
    validate_entry_fields(form_data)         账目表单 -> 清洗后的字段

增强交付（复用同一套金额规则，供筛选与预算调用）：

    parse_amount_range(min_text, max_text)   筛选的最小/最大金额
    parse_budget_limit(text)                 预算上限（空值表示不设该项预算）
    parse_type_filter(text)                  筛选的收支类型（空值表示不限）
    validate_month(text)                     预算月份 YYYY-MM
    format_cents(cents)                      整数分 -> 展示用元字符串

设计约定：
1. 本模块只做「格式与边界」校验，不读写存储、不依赖项目内其他模块，
   因此可以单独跑单元测试。
2. 金额一律用字符串 + Decimal 转换，避免 float 的二进制误差
   （0.1 + 0.2 != 0.3 这类问题在账本里不可接受）。
3. 校验失败抛 ValidationError，错误按字段收集在 exc.errors（dict），
   一次返回全部字段问题，方便表单逐项标红。
4. 金额正则使用 re.ASCII：只认半角 0-9，防止全角「４５」被 Python
   的 Unicode \\d 误判为合法数字。
5. 金额和日期不额外添加方案以外的上限，与筛选模块保持同一口径。

导入方式（源码位于包 lotterytally 内）：

    from lotterytally.validators import parse_amount_cents, validate_entry_fields
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal, localcontext

__all__ = [
    "ALLOWED_TYPES",
    "MAX_NOTE_LENGTH",
    "ValidationError",
    "format_cents",
    "month_of",
    "normalize_type",
    "parse_amount_cents",
    "parse_amount_range",
    "parse_budget_limit",
    "parse_date",
    "parse_type_filter",
    "validate_entry_fields",
    "validate_month",
    "validate_note",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 收支类型固定两种，写入账本时用英文，界面展示用中文
ALLOWED_TYPES = ("income", "expense")
TYPE_LABELS = {"income": "收入", "expense": "支出"}

#: 备注长度上限（方案 4.2 节建议 200 个字符）
MAX_NOTE_LENGTH = 200

#: datetime.date 支持的完整日历范围；不另设业务年份限制。
MIN_YEAR = date.min.year
MAX_YEAR = date.max.year

#: 字段名 -> 中文标签，用于拼错误提示
_FIELD_LABELS = {
    "amount": "金额",
    "amount_yuan": "金额",
    "min_amount": "最小金额",
    "max_amount": "最大金额",
    "date": "日期",
    "type": "收支类型",
    "category_id": "类别",
    "note": "备注",
    "month": "月份",
    "total": "预算金额",
    "total_cents": "预算上限",
}

# 允许 45、45.5、45.50；不允许带符号、千分位、单位、超过两位小数。
# 整数位不设业务长度限制，与 filters.py 的独立解析路径一致。
_AMOUNT_RE = re.compile(r"^[0-9]+(?:\.[0-9]{1,2})?$", re.ASCII)
_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$", re.ASCII)
_MONTH_RE = re.compile(r"^([0-9]{4})-([0-9]{2})$", re.ASCII)

#: 备注里允许出现的控制字符（多行备注的换行和缩进），其余控制字符一律拒绝
_ALLOWED_NOTE_CONTROLS = {"\n", "\t"}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """校验失败。errors 是 {字段名: 中文提示}，可一次携带多个字段的错误。

    >>> exc = ValidationError.of("date", "请选择日期")
    >>> exc.errors
    {'date': '请选择日期'}
    >>> exc.field, exc.message
    ('date', '请选择日期')
    """

    def __init__(self, errors, field=None):
        if isinstance(errors, str):
            errors = {field or "form": errors}
        if not errors:
            raise ValueError("ValidationError 至少需要一个字段错误")
        self.errors = dict(errors)
        self.field = field or next(iter(self.errors))
        self.message = self.errors.get(self.field, next(iter(self.errors.values())))
        super().__init__(self.message)

    @classmethod
    def of(cls, field, message):
        """构造单字段错误，最常用的快捷方式。"""
        return cls({field: message}, field=field)

    def to_dict(self):
        """给模板/JSON 用的纯数据形式。"""
        return {"field": self.field, "message": self.message, "errors": dict(self.errors)}

    def __str__(self):
        if len(self.errors) == 1:
            return next(iter(self.errors.values()))
        return "；".join(f"{_label(key)}：{msg}" for key, msg in self.errors.items())


def _label(field):
    if field.startswith("limit_"):
        return "分类预算金额"
    return _FIELD_LABELS.get(field, field)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _to_text(value):
    """把表单/JSON 里可能出现的任意值安全转成去首尾空白的字符串。

    None -> ""，bool 转成 "true"/"false"（随后会被正则拒绝，
    避免 True 被当成 1 元这种静默错误）。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "nan"
        return repr(value)
    return str(value).strip()


def _amount_format_message(text):
    """针对不同的非法金额给出可操作的具体提示，而不是统一的「格式错误」。"""
    lowered = text.lower()
    if "e" in lowered:
        return "金额不支持科学计数法，请填 45 或 45.50 这样的普通数字"
    if text.startswith("-"):
        return "金额不能为负数，支出方向由收支类型决定"
    if text.startswith("+"):
        return "金额不需要写正号，直接填数字"
    if "," in text or "，" in text:
        return "金额不要写千分位分隔符，1500 而不是 1,500"
    if "." in text:
        _, _, fraction = text.partition(".")
        if fraction and fraction.isdigit() and len(fraction) > 2:
            return "金额最多保留两位小数"
    if re.search(r"[^0-9.]", text, re.ASCII):
        return "金额只能填数字，不要带单位、空格或其他符号"
    return "金额格式不正确，请填 45 或 45.50 这样的数字"


# ---------------------------------------------------------------------------
# 金额
# ---------------------------------------------------------------------------


def parse_amount_cents(value, field="amount", *, allow_empty=False, allow_zero=False):
    """把用户输入的「元」金额转成整数分。

    参数：
        value        表单里的原始值，通常是字符串
        field        出错时挂在哪个字段上，决定错误提示的措辞
        allow_empty  允许空值，返回 None（筛选用：不填即不限）
        allow_zero   显式允许 0；账目、筛选区间与预算默认均要求正数

    返回：整数分 int；allow_empty 且为空时返回 None。

    >>> parse_amount_cents("45")
    4500
    >>> parse_amount_cents("45.5")
    4550
    >>> parse_amount_cents(" 45.50 ")
    4550
    >>> parse_amount_cents("0.01")
    1
    >>> parse_amount_cents(None, allow_empty=True) is None
    True

    拒绝的口径：零、负数、超过两位小数、科学计数法、千分位、单位文字。
    >>> parse_amount_cents("0")
    Traceback (most recent call last):
    ...
    lotterytally.validators.ValidationError: 金额必须大于 0

    大金额也保持精确，与筛选模块使用同一口径：
    >>> parse_amount_cents("1000000000000")
    100000000000000
    """
    label = _label(field)
    text = _to_text(value)

    if not text:
        if allow_empty:
            return None
        raise ValidationError.of(field, f"请填写{label}")

    if not _AMOUNT_RE.fullmatch(text):
        raise ValidationError.of(field, _amount_format_message(text))

    # 正则已保证最多两位小数；再按位数放大 Decimal 精度，
    # 避免长数字（超过默认 28 位有效数字）在乘 100 时被截断。
    # filters.py 用的是同一套写法，两个模块对大额输入的结果保持一致。
    with localcontext() as context:
        context.prec = max(28, len(text) + 2)
        cents = int(Decimal(text) * 100)

    if cents == 0 and not allow_zero:
        raise ValidationError.of(field, f"{label}必须大于 0")
    return cents


def parse_amount_range(
    min_value=None,
    max_value=None,
    *,
    min_field="min_amount",
    max_field="max_amount",
):
    """筛选页的最小/最大金额：两个都可空，空表示该侧不设限，非空须为正数。

    返回 (low_cents, high_cents)，任一侧为空时对应位置是 None。
    上下界都通过时才校验「最小不得大于最大」，错误一次性返回。

    >>> parse_amount_range("30", "50")
    (3000, 5000)
    >>> parse_amount_range("", "")
    (None, None)
    >>> parse_amount_range("50", "30")
    Traceback (most recent call last):
    ...
    lotterytally.validators.ValidationError: 最小金额不能大于最大金额（50.00 > 30.00）
    """
    errors = {}
    low = high = None

    try:
        low = parse_amount_cents(min_value, min_field, allow_empty=True)
    except ValidationError as exc:
        errors.update(exc.errors)
    try:
        high = parse_amount_cents(max_value, max_field, allow_empty=True)
    except ValidationError as exc:
        errors.update(exc.errors)

    if low is not None and high is not None and low > high:
        # 挂在 max 字段上，界面把提示显示在「最大金额」输入框旁
        errors[max_field] = f"最小金额不能大于最大金额（{format_cents(low)} > {format_cents(high)}）"

    if errors:
        raise ValidationError(errors)
    return low, high


def parse_budget_limit(value, field="total_cents"):
    """预算上限：空值表示不设该项预算（返回 None），填了就必须是正数。

    复用 parse_amount_cents，保证预算和账目对「什么算合法金额」的口径一致。

    >>> parse_budget_limit("500")
    50000
    >>> parse_budget_limit("") is None
    True
    """
    return parse_amount_cents(value, field, allow_empty=True)


def format_cents(cents, *, with_symbol=False):
    """整数分 -> 展示用字符串，固定两位小数。

    >>> format_cents(4500)
    '45.00'
    >>> format_cents(5)
    '0.05'
    >>> format_cents(-1550)
    '-15.50'
    >>> format_cents(4500, with_symbol=True)
    '¥45.00'
    >>> format_cents(-1550, with_symbol=True)
    '-¥15.50'
    """
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise TypeError(f"金额必须是整数分，收到 {type(cents).__name__}")
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    body = f"{value // 100}.{value % 100:02d}"
    return f"{sign}¥{body}" if with_symbol else sign + body


# ---------------------------------------------------------------------------
# 日期与月份
# ---------------------------------------------------------------------------


def parse_date(value, field="date"):
    """校验账目日期，必须是 YYYY-MM-DD 且真实存在的日历日。

    返回规范化后的字符串（模型层统一用字符串存日期）。

    >>> parse_date("2026-11-07")
    '2026-11-07'
    >>> parse_date("2024-02-29")
    '2024-02-29'
    >>> parse_date("2026-02-29")
    Traceback (most recent call last):
    ...
    lotterytally.validators.ValidationError: 日期不存在：2026-02-29，请检查月份和天数
    """
    text = _to_text(value)
    if not text:
        raise ValidationError.of(field, "请选择日期")

    match = _DATE_RE.fullmatch(text)
    if not match:
        raise ValidationError.of(field, "日期格式应为 YYYY-MM-DD，例如 2026-11-07")

    year, month, day = (int(group) for group in match.groups())
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise ValidationError.of(field, f"年份应在 {MIN_YEAR}–{MAX_YEAR} 之间")

    try:
        date(year, month, day)
    except ValueError:
        # 2026-02-29、2026-04-31、2026-13-01 都落在这里
        raise ValidationError.of(field, f"日期不存在：{text}，请检查月份和天数") from None
    return text


def validate_month(value, field="month"):
    """校验预算月份 YYYY-MM，返回规范化字符串。

    >>> validate_month("2026-11")
    '2026-11'
    >>> validate_month("2026-13")
    Traceback (most recent call last):
    ...
    lotterytally.validators.ValidationError: 月份应在 01–12 之间
    """
    text = _to_text(value)
    if not text:
        raise ValidationError.of(field, "请选择月份")

    match = _MONTH_RE.fullmatch(text)
    if not match:
        raise ValidationError.of(field, "月份格式应为 YYYY-MM，例如 2026-11")

    year, month = int(match.group(1)), int(match.group(2))
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise ValidationError.of(field, f"年份应在 {MIN_YEAR}–{MAX_YEAR} 之间")
    if not 1 <= month <= 12:
        raise ValidationError.of(field, "月份应在 01–12 之间")
    return text


def month_of(date_text):
    """从账目日期取出所属月份 YYYY-MM，预算按账目日期归属月份。"""
    parsed = parse_date(date_text)
    return parsed[:7]


# ---------------------------------------------------------------------------
# 收支类型与备注
# ---------------------------------------------------------------------------


def normalize_type(value, field="type"):
    """收支类型只接受 income / expense，容错处理大小写和首尾空白。

    >>> normalize_type(" Income ")
    'income'
    >>> normalize_type("transfer")
    Traceback (most recent call last):
    ...
    lotterytally.validators.ValidationError: 收支类型只能是收入或支出
    """
    text = _to_text(value).lower()
    if not text:
        raise ValidationError.of(field, "请选择收支类型")
    if text not in ALLOWED_TYPES:
        raise ValidationError.of(field, "收支类型只能是收入或支出")
    return text


def parse_type_filter(value, field="type"):
    """筛选页的收支类型：留空表示不限，返回 None。"""
    text = _to_text(value).lower()
    if not text:
        return None
    return normalize_type(text, field)


def validate_note(value, field="note", max_length=MAX_NOTE_LENGTH):
    """备注：可空，去除首尾空白，限制长度，拒绝不可见控制字符。

    >>> validate_note("  晚餐  ")
    '晚餐'
    >>> validate_note(None)
    ''
    """
    # 浏览器的多行表单在 Windows 上常提交 CRLF；统一成 LF 后计数和存储。
    text = _to_text(value).replace("\r\n", "\n").replace("\r", "\n")

    if len(text) > max_length:
        raise ValidationError.of(
            field, f"备注最多 {max_length} 个字符，当前 {len(text)} 个"
        )
    for char in text:
        if ord(char) < 32 and char not in _ALLOWED_NOTE_CONTROLS:
            raise ValidationError.of(field, "备注包含不可见的控制字符，请重新输入")
    return text


# ---------------------------------------------------------------------------
# 账目表单整体校验
# ---------------------------------------------------------------------------


def _keeps_inactive_category(category_id, existing_entry):
    """编辑历史账目时，允许保留原来那个已停用的类别（方案 4.2 节）。"""
    if not existing_entry or not hasattr(existing_entry, "get"):
        return False
    return str(existing_entry.get("category_id")) == category_id


def validate_entry_fields(form_data, categories=None, existing_entry=None):
    """校验一条账目的全部字段，返回可直接写入模型的字典。

    参数：
        form_data       表单数据（dict 或 werkzeug 的 MultiDict，需支持 .get）
        categories      可选，类别列表。传入时额外校验「类别存在 / 类型一致 / 未停用」
        existing_entry  可选，编辑场景传入原账目，用于放行原停用类别

    返回：{"date", "type", "amount_cents", "category_id", "note"}
    抛出：ValidationError，exc.errors 收集所有字段的错误，不提前中断。

    >>> clean = validate_entry_fields(
    ...     {"date": "2026-11-07", "type": "expense", "amount": "45.5",
    ...      "category_id": "food", "note": " 晚餐 "})
    >>> clean["amount_cents"], clean["note"]
    (4550, '晚餐')
    """
    if form_data is None or not hasattr(form_data, "get"):
        form_data = {}

    errors = {}
    cleaned = {"date": "", "type": "", "amount_cents": None, "category_id": "", "note": ""}

    # 1) 日期
    try:
        cleaned["date"] = parse_date(form_data.get("date"))
    except ValidationError as exc:
        errors.update(exc.errors)

    # 2) 收支类型
    entry_type = None
    try:
        entry_type = normalize_type(form_data.get("type"))
        cleaned["type"] = entry_type
    except ValidationError as exc:
        errors.update(exc.errors)

    # 3) 金额：表单字段名兼容 amount 与 amount_yuan
    raw_amount = form_data.get("amount")
    if raw_amount is None or raw_amount == "":
        raw_amount = form_data.get("amount_yuan")
    try:
        cleaned["amount_cents"] = parse_amount_cents(raw_amount, "amount")
    except ValidationError as exc:
        errors.update(exc.errors)

    # 4) 类别
    category_id = _to_text(form_data.get("category_id"))
    if not category_id:
        errors["category_id"] = "请选择类别"
    elif categories is not None:
        bucket = {
            str(item.get("id")): item
            for item in categories
            if isinstance(item, dict) and item.get("id")
        }
        category = bucket.get(category_id)
        if category is None:
            errors["category_id"] = "所选类别不存在，请重新选择"
        elif entry_type and category.get("type") != entry_type:
            errors["category_id"] = "类别与收支类型不一致，请重新选择"
        elif not category.get("active", True) and not _keeps_inactive_category(
            category_id, existing_entry
        ):
            errors["category_id"] = "该类别已停用，请选择其他类别"
    if "category_id" not in errors:
        cleaned["category_id"] = category_id

    # 5) 备注
    try:
        cleaned["note"] = validate_note(form_data.get("note"))
    except ValidationError as exc:
        errors.update(exc.errors)

    if errors:
        raise ValidationError(errors)
    return cleaned


if __name__ == "__main__":  # 手工冒烟：python validators.py，输出可直接作为演示/博客截图
    print("== 1. 金额转换：合法与非法 ==")
    samples = ["45", "45.5", "45.50", "0.01", "0", "-1", "12.345", "1e3", "1,000", "45元", "４５", ""]
    for text in samples:
        try:
            print(f"  {text!r:>10} -> {parse_amount_cents(text)} 分")
        except ValidationError as exc:
            print(f"  {text!r:>10} -> 拒绝：{exc}")

    print("\n== 2. 合法账目：字段清洗 ==")
    good = {
        "date": "2026-11-07",
        "type": "expense",
        "amount": "45.5",
        "category_id": "food",
        "note": "  晚餐  ",
    }
    print(f"  表单 {good}")
    print(f"  清洗后 {validate_entry_fields(good)}")

    print("\n== 3. 多个字段同时出错：一次返回全部提示 ==")
    bad = {
        "date": "2026-02-30",
        "type": "transfer",
        "amount": "12.345",
        "category_id": "",
        "note": "账" * 201,
    }
    try:
        validate_entry_fields(bad)
    except ValidationError as exc:
        for name, message in exc.errors.items():
            print(f"  {name:>11}: {message}")

    print("\n== 4. 金额规则复用：筛选区间与预算上限 ==")
    print(f"  筛选 30–50 元 -> {parse_amount_range('30', '50')} 分（边界包含）")
    print(f"  筛选只填下限 -> {parse_amount_range('30', '')} 分")
    try:
        parse_amount_range("50", "30")
    except ValidationError as exc:
        print(f"  上下界颠倒 -> 拒绝：{exc}")
    print(f"  预算上限 500 元 -> {parse_budget_limit('500')} 分；留空 -> {parse_budget_limit('')}（不设预算）")

    print("\n== 5. 展示格式 ==")
    print(f"  {format_cents(51500)} 元 / {format_cents(-1500, with_symbol=True)}（超支 15 元）")
