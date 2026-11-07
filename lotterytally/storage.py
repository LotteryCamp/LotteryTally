"""
storage.py —— 存储与备份模块（存储部分）

负责人：李义东 102401215
接口：
    load_ledger(data_file=None) -> dict            完整账本快照（深拷贝）
    update_ledger(change, data_file=None) -> dict  事务式更新，返回更新后的快照
    save_ledger(ledger, data_file=None)            校验并原子写入
    ensure_ledger(data_file=None) -> str           首次启动初始化默认账本
    validate_ledger(data)                          全量结构校验（更新与恢复共用）
    configure(data_file)                           app 启动时指定数据文件路径
    write_lock()                                   上下文管理器，备份模块组合原子操作用

职责边界：
    只负责账本文件的初始化、读取、校验和写入，不碰任何页面和业务规则。
    按方案第三节约定，所有写入必须经过本模块，其他模块不得直接改 ledger.json。

两条铁律，都来自方案 6.1 节：
    1. update_ledger() 在同一把锁内完成"读取 -> 修改 -> 写入"。只锁最后一步，
       两个请求会都读到旧账本，后保存的把先保存的结果覆盖掉。
    2. 写入用"临时文件 + os.replace()"原子替换。哪怕写到一半断电，
       正式账本要么是旧的完整版，要么是新的完整版，绝不会出现半个文件。

损坏文件保护：
    读到已有但解析失败的 JSON 时抛 LedgerCorruptedError，原文件一个字节都不动，
    交给页面提示用户，绝不能"顺手重建一个空账本"把数据冲掉。
"""

import copy
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime

# 当前账本结构版本。以后字段大改时升版本号，读取时拒绝更高级的文件
SCHEMA_VERSION = 1

# load_ledger 只放行这个列表里的版本。高于当前版本的文件由新版程序创建，
# 旧程序读不懂它的字段，硬读会静默丢数据
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION,)

# 合法的收支类型
VALID_TYPES = ("income", "expense")

# 备注长度上限（方案 4.2 节：建议 200 个字符）
NOTE_MAX_CHARS = 200

# 日期只认 2026-11-07 这种形式，是否真实存在的日历日期再单独验
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
# 预算月份只认 2026-11 这种形式
MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")

# 金额必须是不小于 1 的整数分。bool 是 int 的子类，要单独拦
# （True 会被当成 1 分钱）。这里比导出模块更严：账本是唯一的数据源头，
# 落盘前就该保证是纯正整数分，导出模块的宽容是为了兼容已存在的脏数据


class StorageError(Exception):
    """存储层的通用错误基类，调用方可以统一 except 这一个类型。"""


class LedgerCorruptedError(StorageError):
    """账本文件存在但内容损坏（JSON 解析失败或版本不识别）。

    抛出这个错误时原文件保持原样，供用户手工恢复或用备份功能回滚。
    """


class LedgerValidationError(StorageError, ValueError):
    """账本数据没有通过结构校验。

    继承 ValueError 与包内 ExportError 的约定保持一致：
    调用方既可以精确接住，也可以按 ValueError 统一处理。
    """


# 进程内写入锁。课程版约定单进程运行（方案 6.1 节），进程内锁足够；
# RLock 允许备份模块在 write_lock() 里再调 save_ledger() 而不会死锁
_LOCK = threading.RLock()

# 数据文件路径。默认仓库根目录下的 data/ledger.json（包目录的上一级），
# app.py 启动时用 configure() 指定 --data-file 传入的独立演示文件
_DATA_FILE = None


def configure(data_file):
    """由 app 启动时调用，指定账本文件路径。传 None 恢复默认路径。"""
    global _DATA_FILE
    _DATA_FILE = None if data_file is None else os.path.abspath(str(data_file))


def _default_data_file():
    """默认账本路径：包目录上一级的 data/ledger.json。

    不用当前工作目录：从别的目录启动 python 时 cwd 会漂移，
    账本会莫名其妙多出一份。按包位置定位，路径永远稳定。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "ledger.json")


def _resolve(data_file=None):
    """把调用方传的路径（或全局配置、或默认路径）整理成绝对路径。"""
    path = data_file or _DATA_FILE or _default_data_file()
    return os.path.abspath(str(path))


def new_ledger():
    """构造一份空账本：默认类别齐全，账目和预算为空。

    默认类别取自类别模块（方案 5.2 节：默认类别由类别模块提供）。
    不要在这里自造第二套类别 —— 表单按类别模块的 ID 生成选项，
    账本初始类别若用了别的 ID 体系，第一笔账就会因类别引用不存在而被拒。
    每次调用生成新的字典（含新的类别列表），调用方随便改都不会串到别处。
    """
    from lotterytally import categories

    return {"schema_version": SCHEMA_VERSION,
            "categories": categories.get_default_categories(),
            "entries": [], "budgets": {}}


@contextmanager
def write_lock():
    """持有写入锁的上下文管理器。

    备份模块恢复数据时要把"留存恢复前备份 + 写入恢复数据 + 复核"
    合成一个原子动作，单靠 save_ledger() 的一次调用拼不出来，
    所以把锁开放出来。锁是 RLock，嵌套调用存储函数不会死锁。
    """
    with _LOCK:
        yield


def ensure_ledger(data_file=None):
    """保证账本文件存在；不存在就初始化一份默认账本（T01 首次启动）。

    文件已存在时什么都不做 —— 哪怕内容是坏的也不碰，
    损坏交给 load_ledger 报错，这里绝不能悄悄覆盖。

    返回账本文件的绝对路径。
    """
    path = _resolve(data_file)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _LOCK:
        if not os.path.exists(path):
            _atomic_write(path, new_ledger())
    return path


def load_ledger(data_file=None):
    """读取完整账本，返回一份深拷贝快照。

    文件不存在时自动初始化默认账本（首次启动场景）。
    返回深拷贝而不是内部对象本身：调用方拿到快照后随便改，
    不会污染下一次读取，也不用担心它绕过存储入口写文件。

    文件损坏时抛 LedgerCorruptedError，原文件保持原样。
    """
    path = _resolve(data_file)
    with _LOCK:
        data = _read_unlocked(path)
        if data is None:
            ensure_ledger(path)
            data = _read_unlocked(path)
    # 校验和深拷贝放在锁外做：快照是只读语义，不值得占着写锁
    _check_version(data, path)
    return copy.deepcopy(data)


def update_ledger(change, data_file=None):
    """事务式更新：在同一把锁内读取最新账本、应用修改、校验并保存。

    参数：
        change —— 两种形式：
            callable：接收读取到的账本 dict，直接原地修改。
                      修改通过原地完成；返回 None 即可。
                      返回完整账本 dict（含 schema_version）时按整体替换处理；
                      返回其他 dict（类别、账目模块的信息副本）一律忽略。
            dict    ：用这份新账本整体替换。
        data_file —— 账本文件路径，默认取 configure() 指定的路径。

    返回更新后的账本快照（深拷贝），方便调用方接着展示。

    为什么校验必须在保存前：写进去的每一份数据都要是结构完整的，
    等读出来才发现坏了，就已经落盘了。这里拦在门外，坏数据进不了文件。
    """
    path = _resolve(data_file)
    with _LOCK:
        ledger = _read_unlocked(path)
        if ledger is None:
            ledger = new_ledger()
        else:
            _check_version(ledger, path)

        if callable(change):
            result = change(ledger)
            if result is None:
                pass  # 原地修改生效，直接走校验保存
            elif isinstance(result, dict) and "schema_version" in result:
                # 明确返回了完整账本 dict → 整体替换
                ledger = result
            elif isinstance(result, dict):
                # 类别、账目等业务模块的函数是"原地修改 + 返回信息副本"
                # （如 categories.create_category 返回 category.copy()）。
                # 这种副本不是账本，不能当整体替换用 —— 忽略返回值，
                # 以回调对传入账本的原地修改为准。
                pass
            else:
                raise LedgerValidationError(
                    "update_ledger 的回调返回了 {}，应为 None 或账本 dict".format(
                        type(result).__name__))
        elif isinstance(change, dict):
            ledger = change
        else:
            raise LedgerValidationError(
                "change 必须是回调函数或 dict，收到 {}".format(type(change).__name__))

        validate_ledger(ledger)
        _atomic_write(path, ledger)
    return copy.deepcopy(ledger)


def save_ledger(ledger, data_file=None):
    """校验并写入一份完整账本。供备份恢复等"已拿到完整数据"的场景使用。

    日常的账目增删改请走 update_ledger()，不要先 load 再 save ——
    那样两次调用之间锁已经放开了，并发请求会互相覆盖。
    """
    if not isinstance(ledger, dict):
        raise LedgerValidationError(
            "ledger 必须是 dict，收到 {}".format(type(ledger).__name__))
    validate_ledger(ledger)
    path = _resolve(data_file)
    with _LOCK:
        _atomic_write(path, ledger)
    return copy.deepcopy(ledger)


def _read_unlocked(path):
    """读取并解析账本 JSON。调用方必须已持有锁。

    返回 None 表示文件不存在；解析失败或顶层结构不对时抛
    LedgerCorruptedError，原文件保持原样，由调用方决定怎么提示。
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise LedgerCorruptedError(
            "账本文件 {} 无法解析（{}）。文件保持原样未做改动，"
            "可从备份恢复。".format(path, exc))
    if not isinstance(data, dict):
        raise LedgerCorruptedError(
            "账本文件 {} 的顶层结构不是 JSON 对象，无法识别。".format(path))
    return data


def _check_version(data, path):
    """版本检查（任务卡的"版本检查"项）：不识别的版本直接拒绝。"""
    version = data.get("schema_version")
    if version is None:
        raise LedgerCorruptedError(
            "账本文件 {} 缺少 schema_version 字段，无法确认数据格式。".format(path))
    if isinstance(version, bool) or not isinstance(version, int):
        raise LedgerCorruptedError(
            "账本文件 {} 的 schema_version 是 {!r}，不是整数。".format(path, version))
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise LedgerCorruptedError(
            "账本文件 {} 的数据版本是 {}，当前程序只支持 {}。"
            "该文件可能由更新版本的程序创建。".format(
                path, version, SUPPORTED_SCHEMA_VERSIONS[0]))


def _atomic_write(path, data):
    """把账本原子地写入 path：临时文件写完整后 os.replace() 替换。

    临时文件建在账本同一目录 —— os.replace 要求同一文件系统，
    跨盘的临时文件会让替换操作退化成不可原子的复制。
    写完 flush + fsync 再替换，断电时也不会留下半个文件。
    """
    parent = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # 替换失败时清理临时文件，不留垃圾；正式账本未被动过
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------
# 恢复备份（T14）和日常更新共用同一套校验：恢复前校验不过就整体拒绝；
# 日常写入校验不过同样整体拒绝，账本里不会出现"半合法"的数据。

def _require_cents(value, position, allow_zero=False):
    """校验整数分金额：必须是 int（不许 bool、float、字符串）且非负。

    账本层比导出层严格 —— 导出要兼容历史上各种写法，这里守的是入口，
    严格一点，脏数据就永远进不来。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerValidationError(
            "{}的金额必须是整数分，收到 {!r}（{}）".format(
                position, value, type(value).__name__))
    if value < 0 or (value == 0 and not allow_zero):
        raise LedgerValidationError("{}的金额为 {}，非法".format(position, value))
    return value


def _validate_category(item, position, seen_ids, seen_names):
    """校验单个类别。类型、ID 唯一、同类型下名称唯一（方案 4.2 节）。"""
    if not isinstance(item, dict):
        raise LedgerValidationError("{}不是 JSON 对象".format(position))

    cid = item.get("id")
    if not isinstance(cid, str) or not cid.strip():
        raise LedgerValidationError("{}的 id 缺失或为空".format(position))
    if cid in seen_ids:
        raise LedgerValidationError("类别 ID {!r} 重复（{}）".format(cid, position))
    seen_ids.add(cid)

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise LedgerValidationError("类别 {!r} 的名称缺失或为空".format(cid))
    entry_type = item.get("type")
    if entry_type not in VALID_TYPES:
        raise LedgerValidationError(
            "类别 {!r} 的收支类型是 {!r}，只能是 income 或 expense".format(cid, entry_type))

    name_key = (entry_type, name.strip())
    if name_key in seen_names:
        raise LedgerValidationError(
            "同一收支类型下类别名称 {!r} 重复，表单会出现多个同名选项".format(name))
    seen_names.add(name_key)

    active = item.get("active", True)
    if not isinstance(active, bool):
        raise LedgerValidationError(
            "类别 {!r} 的 active 应为布尔值，收到 {!r}".format(cid, active))


def _validate_entry(item, position, index, category_types, seen_ids):
    """校验单条账目：ID 唯一、真实日历日期、正整数分、类别引用且类型一致。"""
    if not isinstance(item, dict):
        raise LedgerValidationError("{}不是 JSON 对象".format(position))

    eid = item.get("id")
    if not isinstance(eid, str) or not eid.strip():
        raise LedgerValidationError("{}的 id 缺失或为空".format(position))
    if eid in seen_ids:
        raise LedgerValidationError("账目 ID {!r} 重复（第 {} 条）".format(eid, index))
    seen_ids.add(eid)

    raw_date = item.get("date")
    if not isinstance(raw_date, str) or not DATE_PATTERN.fullmatch(raw_date.strip()):
        raise LedgerValidationError(
            "账目 {!r} 的日期格式不对：{!r}，应为 YYYY-MM-DD".format(eid, raw_date))
    try:
        datetime.strptime(raw_date.strip(), "%Y-%m-%d")
    except ValueError:
        raise LedgerValidationError(
            "账目 {!r} 的日期不存在：{!r}".format(eid, raw_date))

    entry_type = item.get("type")
    if entry_type not in VALID_TYPES:
        raise LedgerValidationError(
            "账目 {!r} 的收支类型是 {!r}，只能是 income 或 expense".format(eid, entry_type))

    _require_cents(item.get("amount_cents"), "账目 {!r}".format(eid))

    cid = item.get("category_id")
    if not isinstance(cid, str) or not cid:
        raise LedgerValidationError("账目 {!r} 的 category_id 缺失".format(eid))
    if cid not in category_types:
        raise LedgerValidationError(
            "账目 {!r} 引用的类别 {!r} 不存在".format(eid, cid))
    if category_types[cid] != entry_type:
        raise LedgerValidationError(
            "账目 {!r} 的类型是 {}，但类别 {!r} 是 {}，类型不一致".format(
                eid, entry_type, cid, category_types[cid]))

    note = item.get("note")
    if note is not None:
        if not isinstance(note, str):
            raise LedgerValidationError("账目 {!r} 的备注不是字符串".format(eid))
        if len(note) > NOTE_MAX_CHARS:
            raise LedgerValidationError(
                "账目 {!r} 的备注超过 {} 个字符".format(eid, NOTE_MAX_CHARS))

    for ts_field in ("created_at", "updated_at"):
        ts = item.get(ts_field)
        if ts is not None and not isinstance(ts, str):
            raise LedgerValidationError(
                "账目 {!r} 的 {} 不是字符串".format(eid, ts_field))


def _validate_budget(month, config, category_types):
    """校验单月预算：月份格式合法、上限非负整数、分类引用真实类别。"""
    if not isinstance(month, str) or not MONTH_PATTERN.fullmatch(month):
        raise LedgerValidationError(
            "预算月份格式不对：{!r}，应为 YYYY-MM".format(month))
    yy, mm = month.split("-")
    if not 1 <= int(mm) <= 12:
        raise LedgerValidationError("预算月份不存在：{!r}".format(month))
    if not isinstance(config, dict):
        raise LedgerValidationError(
            "{} 的预算配置不是 JSON 对象".format(month))

    total = config.get("total_cents")
    if total is not None:
        _require_cents(total, "{} 的总预算".format(month), allow_zero=True)

    limits = config.get("category_limits", {})
    if not isinstance(limits, dict):
        raise LedgerValidationError(
            "{} 的 category_limits 不是 JSON 对象".format(month))
    for cid, limit in limits.items():
        if cid not in category_types:
            raise LedgerValidationError(
                "{} 的分类预算引用了不存在的类别 {!r}".format(month, cid))
        _require_cents(limit, "{} 的类别 {!r} 预算".format(month, cid),
                       allow_zero=True)


def validate_ledger(data):
    """对完整账本做全量结构校验，不过关抛 LedgerValidationError。

    覆盖方案 6.1 节恢复流程第 2 步要求的全部检查项：
    JSON 顶层结构、schema_version、类别字段与唯一性、账目字段、金额、
    真实日历日期、重复 ID、类别引用、收支类型和预算月份。

    校验通过时原样返回 data，方便链式书写。
    """
    if not isinstance(data, dict):
        raise LedgerValidationError(
            "账本顶层结构必须是 JSON 对象，收到 {}".format(type(data).__name__))

    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise LedgerValidationError(
            "schema_version 缺失或不是整数：{!r}".format(version))
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise LedgerValidationError(
            "数据版本 {} 不受支持，当前程序支持版本 {}".format(
                version, SUPPORTED_SCHEMA_VERSIONS[0]))

    # 第一遍：类别。先建 id -> 类型 的映射，账目和预算的引用检查都靠它
    raw_categories = data.get("categories")
    if not isinstance(raw_categories, list):
        raise LedgerValidationError("categories 必须是数组")
    category_types = {}
    seen_ids, seen_names = set(), set()
    for index, item in enumerate(raw_categories):
        _validate_category(item, "categories[{}]".format(index),
                           seen_ids, seen_names)
        category_types[item["id"]] = item["type"]

    # 第二遍：账目
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise LedgerValidationError("entries 必须是数组")
    seen_entry_ids = set()
    for index, item in enumerate(raw_entries):
        _validate_entry(item, "entries[{}]".format(index), index + 1,
                        category_types, seen_entry_ids)

    # 第三遍：预算
    raw_budgets = data.get("budgets")
    if not isinstance(raw_budgets, dict):
        raise LedgerValidationError("budgets 必须是 JSON 对象")
    for month, config in raw_budgets.items():
        _validate_budget(month, config, category_types)

    return data
