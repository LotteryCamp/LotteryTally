"""
csv_export.py —— CSV 导出模块

负责人：蔡信坡 162404103
接口：export_csv(entries, categories, sort=False) -> str

职责边界：
    只负责把已经筛选好的账目列表转成 CSV 文本，不读文件、不做筛选、不排版页面。
    调用方（统计路由）把筛选结果和类别表传进来，拿到的字符串直接作为下载响应体。

两个容易踩的坑，这里都处理了：
    1. Excel 打开 UTF-8 CSV 中文乱码 —— 输出以 BOM 开头。
    2. 备注以 = + - @ 开头时被表格软件当公式执行 —— 前面补一个单引号转成文本。
"""

import csv
import io
import re
from datetime import datetime

# 表头。顺序按方案 6.2 节：日期、收支类型、类别、金额、备注
# 金额是第 4 列（从 1 开始数）。放在这个位置，Excel 里直接对整列求和就行
HEADER = ["日期", "收支类型", "类别", "金额（元）", "备注"]

# 表格软件会把这些开头的单元格当公式解析，需要转义
FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")

# 类别找不到时的兜底显示
UNKNOWN_CATEGORY = "未分类"

# 合法的收支类型
VALID_TYPES = ("income", "expense")

# 日期只认 2026-11-07 这种形式，具体是不是真实日期再单独验
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# 下载文件名的默认前缀，纯 ASCII
DEFAULT_FILENAME_PREFIX = "pocketledger"

# 文件名里除字母、数字、下划线、短横外的字符统一替换掉，
# 中文、空格、控制字符、/ 和 \ 这些路径分隔符都在此列
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class ExportError(ValueError):
    """账目数据不合法，或者导出过程中出现无法继续处理的情况。

    继承 ValueError，调用方既可以用 except ExportError 精确接住，
    也可以统一按 ValueError 处理，不会打乱现有的错误分支。
    """


def _normalize_amount(amount_cents, position):
    """把金额统一成整数分。非法值直接报错，不做静默兜底。

    以前这里非法值返回 0.00，那会让一条脏数据悄悄变成"零元支出"混进导出结果，
    统计对不上还查不出是哪一条。宁可报错，让上游去修数据。

    接受：int、整数值的 float、纯数字字符串（JSON 里可能被写成字符串）。
    拒绝：None、小数、布尔、其他文本。
    """
    # bool 是 int 的子类，不单独拦的话 True 会变成 1 分
    if isinstance(amount_cents, bool):
        raise ExportError(
            "{}的金额是布尔值 {!r}，不是有效金额".format(position, amount_cents))

    if isinstance(amount_cents, float):
        # 45.5 分这种没有意义，说明上游单位用错了
        if not amount_cents.is_integer():
            raise ExportError(
                "{}的金额 {} 不是整数分，请确认上游是否误用了元".format(position, amount_cents))
        cents = int(amount_cents)
    elif isinstance(amount_cents, int):
        cents = amount_cents
    elif isinstance(amount_cents, str):
        text = amount_cents.strip()
        if not re.fullmatch(r"-?\d+", text):
            raise ExportError("{}的金额 {!r} 不是整数".format(position, amount_cents))
        cents = int(text)
    else:
        raise ExportError(
            "{}的金额类型不合法：{}".format(position, type(amount_cents).__name__))

    if cents < 0:
        raise ExportError(
            "{}的金额为负数 {}，支出应存为正数并用 type 区分".format(position, cents))
    return cents


def cents_to_yuan(cents):
    """整数分转成两位小数的元字符串，全程不经过 float。

    用整除和取余拼字符串，避免 0.1 + 0.2 这类浮点误差污染导出结果。
    传入非法值时抛 ExportError —— 不返回 "0.00" 糊弄过去。
    """
    cents = _normalize_amount(cents, "该记录")
    return "{}.{:02d}".format(cents // 100, cents % 100)


def _escape_cell(value):
    """把单元格内容转成安全文本。

    以公式触发符开头的，前面补单引号。这是 OWASP 推荐的 CSV 注入防护做法，
    补完之后表格软件会按纯文本显示，不会去执行。

    不能顺手把内容 strip 一遍：备注和类别名是用户输入的原文，前导空格、
    尾随换行都属于数据本身，剪掉就等于改写了导出的账目内容。判断公式前缀
    时只看去掉前导空格后的副本，返回的仍是原文。
    """
    text = "" if value is None else str(value)
    # 只对副本去前导空格，text 本身不动
    probe = text.lstrip(" ")
    if probe and probe[0] in FORMULA_PREFIX:
        return "'" + text
    return text


def _category_map(categories):
    """把类别表转成 id -> 名称 的字典，导出时按 id 换成人类可读的名字。"""
    mapping = {}
    for item in categories or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if cid:
            mapping[cid] = item.get("name") or cid
    return mapping


def _normalize_entries(entries):
    """排序之前先逐条验一遍，把记录整理成可直接输出的结构。

    为什么要在排序前验：排序键会取 date 和 created_at，
    如果某条记录不是字典或者日期是 None，要等排序时才抛 TypeError，
    那时已经拿不到是第几条出的问题了。所以先验、先报，报错里带序号。
    """
    if entries is None:
        return []

    try:
        items = list(entries)
    except TypeError:
        raise ExportError("账目列表不是可迭代对象：{}".format(type(entries).__name__))

    rows = []
    for index, entry in enumerate(items):
        position = "第 {} 条记录".format(index + 1)

        if not isinstance(entry, dict):
            raise ExportError(
                "{}不是字典，实际类型是 {}".format(position, type(entry).__name__))

        # 日期：格式对，而且要真实存在（挡掉 2026-02-30）
        raw_date = entry.get("date")
        if not isinstance(raw_date, str):
            raise ExportError("{}的日期不是字符串：{!r}".format(position, raw_date))
        date_text = raw_date.strip()
        if not DATE_PATTERN.fullmatch(date_text):
            raise ExportError(
                "{}的日期格式不对：{!r}，应为 YYYY-MM-DD".format(position, raw_date))
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            raise ExportError("{}的日期不存在：{!r}".format(position, raw_date))

        # 收支类型
        entry_type = entry.get("type")
        if entry_type not in VALID_TYPES:
            raise ExportError(
                "{}的收支类型不合法：{!r}，只能是 income 或 expense".format(position, entry_type))

        rows.append({
            "date": date_text,
            # 录入时间可能缺失（老数据或手工构造的样例），缺失就当空串参与排序
            "created_at": str(entry.get("created_at") or ""),
            "id": str(entry.get("id") or ""),
            "type": entry_type,
            "amount_cents": _normalize_amount(entry.get("amount_cents"), position),
            "category_id": entry.get("category_id") or "",
            "note": entry.get("note"),
        })
    return rows


def _sort_key(row):
    """排序键：账目日期 > 录入时间 > id，全部倒序。

    和流水页「最近记录」用的是同一套口径（方案 6.2 节），
    这样页面看到的顺序和导出的顺序一致，演示时对得上。
    """
    return (row["date"], row["created_at"], row["id"])


def export_csv(entries, categories, sort=False):
    """生成 CSV 文本。

    参数：
        entries    —— 账目 dict 列表，字段见方案 4.1 节（amount_cents 为整数分）
        categories —— 类别 dict 列表，用于把 category_id 换成名称
        sort       —— 是否重新排序。默认 False，保持调用方传进来的顺序。

                      默认不重排是有原因的：页面上的筛选结果已经按用户选的规则
                      （可能是金额排序，也可能是日期排序）排好了，导出必须和眼睛
                      看到的顺序一致。这里再排一遍，就会出现「页面按金额排、
                      下载下来按日期排」的对不上问题。
                      只有调用方明确要标准化顺序时才传 True。

    返回：
        带 UTF-8 BOM 的 CSV 字符串。空列表时返回只有表头的文件，
        保证前端「导出」按钮在任何筛选条件下都能下载到东西。

    异常：
        ExportError —— 任意一条记录不合法。错误信息里带记录序号，方便定位。
    """
    rows = _normalize_entries(entries)

    if sort:
        rows.sort(key=_sort_key, reverse=True)

    cat_map = _category_map(categories)

    buf = io.StringIO()
    # 换行用 \r\n，Excel 和 WPS 都能正确识别
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADER)

    for row in rows:
        cid = row["category_id"]
        # 类别被删除或 ID 写错时退回到原始 ID，宁可难看也不要丢掉这条账的信息
        category_name = cat_map.get(cid) or cid or UNKNOWN_CATEGORY

        writer.writerow([
            _escape_cell(row["date"]),
            "收入" if row["type"] == "income" else "支出",
            _escape_cell(category_name),
            "{}.{:02d}".format(row["amount_cents"] // 100, row["amount_cents"] % 100),
            _escape_cell(row["note"]),
        ])

    # \ufeff 是 BOM，Flask 用 utf-8 编码后正好是 EF BB BF，Excel 才认得出是 UTF-8
    return "\ufeff" + buf.getvalue()


def suggest_filename(prefix=DEFAULT_FILENAME_PREFIX):
    """生成下载文件名，带时间戳避免多次导出互相覆盖。

    文件名只用 ASCII：中文文件名在部分浏览器和 Windows 压缩包里会变乱码，
    这一步求稳。

    前缀由调用方传入，可能是类别名之类的东西，先净化再用：除字母、数字、
    下划线、短横外的字符一律换成下划线，净化后为空就退回默认前缀。
    不净化的话，suggest_filename("账本") 会给出非 ASCII 文件名，
    传进来一个带 / 或 \\ 的前缀还会让下载名带出目录，跟"安全的纯 ASCII
    文件名"这个承诺对不上。
    """
    safe = _UNSAFE_FILENAME_CHARS.sub("_", str(prefix or "")).strip("_")
    if not safe:
        safe = DEFAULT_FILENAME_PREFIX
    safe = safe[:40]
    return "{}_{}.csv".format(safe, datetime.now().strftime("%Y%m%d_%H%M%S"))
