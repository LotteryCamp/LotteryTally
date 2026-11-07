"""
backup.py —— 存储与备份模块（备份部分）

负责人：李义东 102401215
接口：
    build_download_payload(data_file=None) -> (bytes, filename)  生成下载的 JSON 备份
    create_backup(data_file=None, backup_dir=None, prefix=...) -> str  本地留存备份
    list_backups(backup_dir=None) -> list[str]                   列出现有备份
    restore_upload(payload, data_file=None, backup_dir=None) -> dict
                                                                 校验并恢复上传的 JSON

职责边界：
    只负责"账本文件的复制、下载与恢复"，页面（templates/backup.html）和路由
    由 app 层接入。所有写入仍通过 storage 模块完成，本模块不直接写 ledger.json。

恢复流程严格按方案 6.1 节的顺序执行：
    1. 检查上传大小（课堂版上限 2 MB）
    2. 校验 JSON 和 schema_version、字段、金额、日期、重复 ID、
       类别引用、收支类型和预算月份（复用 storage.validate_ledger）
    3. 拿到写入锁后先备份当前账本 —— 恢复出问题还能回到恢复前的状态（T13）
    4. 写入恢复数据并重新读取核对
    5. 返回恢复结果摘要（记录数、留存备份文件名），由页面提示用户
"""

import json
import os
import re
import shutil
from datetime import datetime

from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, url_for)

from lotterytally import storage

# 上传恢复的大小上限。方案 6.1 节建议课堂版 2 MB：
# 本地单文件账本远到不了这个量级，设上限能挡住误传超大文件拖死浏览器
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# 备份文件的时间戳格式，精确到秒；同秒内的多次备份靠序号后缀区分
TIME_FORMAT = "%Y%m%d_%H%M%S"

# 默认备份目录：账本所在目录下的 backups 子目录，
# 账本走 --data-file 换了位置时备份也跟着走，不会散落两处
BACKUP_DIR_NAME = "backups"

# 留存备份的文件名前缀：恢复前的自动留存用 pre_restore，
# 手动备份用 backup，页面上能一眼分清来历
MANUAL_BACKUP_PREFIX = "backup"
PRE_RESTORE_PREFIX = "pre_restore"

# 下载文件名的默认前缀，纯 ASCII（同导出模块的理由：中文下载名易乱码）
DEFAULT_FILENAME_PREFIX = "pocketledger_backup"

# 文件名里除字母、数字、下划线、短横外的字符统一替换，
# 与 csv_export.suggest_filename 同一套净化规则
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class BackupError(storage.StorageError):
    """备份或恢复流程中无法继续的错误（文件太大、当前账本缺失等）。

    继承 StorageError，调用方统一 except storage.StorageError 就能接住
    备份和存储两层的错误。
    """


def _default_backup_dir(data_file=None):
    """默认备份目录：账本文件所在目录下的 backups 子目录。"""
    path = storage._resolve(data_file)
    return os.path.join(os.path.dirname(path), BACKUP_DIR_NAME)


def _timestamp_name(prefix):
    """生成带时间戳的备份文件名，如 backup_20261107_162000.json。"""
    return "{}_{}.json".format(prefix, datetime.now().strftime(TIME_FORMAT))


def _resolve_backup_dir(backup_dir=None, data_file=None):
    path = str(backup_dir) if backup_dir else _default_backup_dir(data_file)
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)


def build_download_payload(data_file=None):
    """生成下载用的 JSON 备份内容。

    返回 (json_bytes, filename)：字节数组直接作为下载响应体，
    文件名带时间戳、纯 ASCII，多次下载不会互相覆盖。

    走 storage.load_ledger() 而不是直接复制文件：
    快照经存储入口读取，缺文件时也会先初始化，下载到的
    一定是结构完整、版本可识别的账本。
    """
    ledger = storage.load_ledger(data_file)
    text = json.dumps(ledger, ensure_ascii=False, indent=2)
    filename = _timestamp_name(DEFAULT_FILENAME_PREFIX)
    return text.encode("utf-8"), filename


def create_backup(data_file=None, backup_dir=None, prefix=MANUAL_BACKUP_PREFIX):
    """把当前账本复制一份到备份目录，返回备份文件名。

    整个复制过程持有存储写入锁：读到一半有请求写入账本的话，
    复制出来的会是半新半旧的混合体，比没有备份还糟糕。

    账本文件还不存在时（从未启动过）抛 BackupError，不凭空备份空账本。
    """
    source = storage._resolve(data_file)
    if not os.path.exists(source):
        raise BackupError("账本文件 {} 还不存在，没有可备份的内容".format(source))

    target_dir = _resolve_backup_dir(backup_dir, data_file)
    name = _timestamp_name(prefix)
    target = os.path.join(target_dir, name)

    # 同一秒内备份两次会得到同一个文件名，第二次会把第一次悄悄覆盖掉。
    # 重名时追加序号，保证每次备份都真实留存
    serial = 2
    with storage.write_lock():
        while os.path.exists(target):
            name = "{}_{}-{}.json".format(
                prefix, datetime.now().strftime(TIME_FORMAT), serial)
            target = os.path.join(target_dir, name)
            serial += 1
        shutil.copyfile(source, target)
    return name


def list_backups(backup_dir=None, data_file=None):
    """列出备份目录里的备份文件名，按时间倒序（最新的在前）。"""
    directory = str(backup_dir) if backup_dir else _default_backup_dir(data_file)
    if not os.path.isdir(directory):
        return []
    names = [n for n in os.listdir(directory)
             if n.endswith(".json") and os.path.isfile(os.path.join(directory, n))]
    return sorted(names, reverse=True)


def restore_upload(payload, data_file=None, backup_dir=None):
    """校验并恢复上传的备份，返回结果摘要 dict。

    参数：
        payload   —— 上传文件的原始字节（Flask 的 FileStorage.read() 结果）
        data_file —— 账本文件路径，默认取 storage.configure() 指定的路径
        backup_dir —— 恢复前留存的备份存放目录

    返回：
        {"entries": 记录数, "categories": 类别数,
         "pre_restore_backup": 恢复前备份的文件名或 None}

    任何一步失败（太大、JSON 损坏、校验不过、写入失败）都会抛异常，
    且保证现有账本一个字节都不会变 —— 这是 T14 的验收要求。
    """
    if payload is None:
        raise BackupError("没有收到上传的文件")

    # 第 1 步：大小检查。先挡超大文件，再去解析，
    # 避免有人传个几百 MB 的"JSON"把内存吃光
    size = len(payload)
    if size > MAX_UPLOAD_BYTES:
        raise BackupError(
            "上传文件 {} 字节，超过上限 {} 字节（{} MB）".format(
                size, MAX_UPLOAD_BYTES, MAX_UPLOAD_BYTES // (1024 * 1024)))
    if size == 0:
        raise BackupError("上传的文件是空的")

    # 第 2 步：解析并全量校验。utf-8-sig 兼容带 BOM 的文件
    # （Windows 记事本保存的 JSON 常带 BOM），解析失败统一报数据不合法
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BackupError("上传文件不是 UTF-8 编码的 JSON")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackupError("上传文件不是合法的 JSON（{}），现有账本未改动".format(exc))

    try:
        storage.validate_ledger(data)
    except storage.LedgerValidationError as exc:
        # 重新抛成 BackupError 保持"恢复相关的错误"是同一种类型，
        # 页面只需要 except BackupError 一种
        raise BackupError("备份内容没有通过校验：{}".format(exc))

    target_dir = _resolve_backup_dir(backup_dir, data_file)

    # 第 3、4 步必须在同一把锁里完成：先留存恢复前备份，再写入，
    # 最后复核。锁是 RLock，内部调用的存储函数可以安全重入。
    # 如果"留存备份"和"写入"分开拿锁，两个并发恢复会互相把对方的
    # "恢复前状态"备份错，T13 就对不上了。
    with storage.write_lock():
        pre_restore_name = None
        if os.path.exists(storage._resolve(data_file)):
            pre_restore_name = create_backup(
                data_file=data_file, backup_dir=target_dir,
                prefix=PRE_RESTORE_PREFIX)

        storage.save_ledger(data, data_file=data_file)

        # 第 5 步前半：重新读取核对。写入声称成功但读出来对不上
        # （磁盘问题、被别的进程动过），必须当作失败报告，不能让页面
        # 显示"恢复成功"而账本里其实是别的东西
        reread = storage.load_ledger(data_file=data_file)
        if reread != data:
            # 复核失败说明写入环节出了问题，账本里现在不是（或不只是）
            # 上传的内容。手上有恢复前备份，就地回滚，不能把一个
            # "不知道是什么"的文件留给用户当账本。
            rollback_note = ""
            if pre_restore_name:
                try:
                    shutil.copyfile(os.path.join(target_dir, pre_restore_name),
                                    storage._resolve(data_file))
                    rollback_note = "已回滚到恢复前的状态，"
                except OSError as exc:
                    rollback_note = "回滚失败（{}），".format(exc)
            raise BackupError(
                "恢复后复核失败：重新读出的账本与上传内容不一致。"
                "{}恢复前的备份留存为 {}".format(rollback_note, pre_restore_name))

    return {
        "entries": len(data.get("entries", [])),
        "categories": len(data.get("categories", [])),
        "pre_restore_backup": pre_restore_name,
    }


# ---------------------------------------------------------------------------
# 备份页面路由（方案 5.1 节：/backup 三个路由归存储负责人）
# ---------------------------------------------------------------------------
# 路由只做"取参数 → 调上面的纯函数 → 渲染/重定向"，校验和落盘规则
# 全部在 restore_upload / build_download_payload 里，便于脱离 Flask 测试。

backup_bp = Blueprint("backup", __name__, template_folder="templates")


def _configured_data_file():
    """取应用配置的数据文件路径；测试或脱离 app 运行时回退到存储默认值。"""
    return current_app.config.get("LEDGER_DATA_PATH")


@backup_bp.get("/backup")
def backup_page():
    """备份页：下载入口、恢复上传表单和现有本地备份列表分开呈现。"""
    backups = list_backups(data_file=_configured_data_file())
    return render_template("backup.html", backups=backups)


@backup_bp.get("/backup/download")
def download_backup():
    """下载完整 JSON 账本备份。文件名纯 ASCII，带时间戳防覆盖。"""
    payload, filename = build_download_payload(data_file=_configured_data_file())
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename={}".format(filename)},
    )


@backup_bp.post("/backup/restore")
def restore_backup():
    """接收上传的备份并校验恢复（T13/T14）。

    任何失败（文件过大、JSON 损坏、校验不过）都只 flash 错误并重定向，
    现有账本由 restore_upload 保证原样不动；成功后提示恢复的记录数
    和恢复前留存的备份文件名。
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("请先选择要恢复的 JSON 备份文件", "error")
        return redirect(url_for("backup.backup_page"))

    try:
        result = restore_upload(upload.read(), data_file=_configured_data_file())
    except storage.StorageError as exc:
        # BackupError 继承自 StorageError，两层错误统一在这里变成页面提示
        flash("恢复失败：{}".format(exc), "error")
        return redirect(url_for("backup.backup_page"))

    message = "已恢复 {} 条账目、{} 个类别".format(
        result["entries"], result["categories"])
    if result["pre_restore_backup"]:
        message += "；恢复前的账本已留存为 {}".format(result["pre_restore_backup"])
    flash(message, "success")
    return redirect(url_for("backup.backup_page"))
