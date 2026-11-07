"""Generate a disposable, fictional ledger for demonstrations."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


def build_demo_ledger(today: date | None = None) -> dict[str, Any]:
    day = today or date.today()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    month = day.strftime("%Y-%m")

    def entry(kind: str, amount_cents: int, category_id: str, note: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "date": day.isoformat(),
            "type": kind,
            "amount_cents": amount_cents,
            "category_id": category_id,
            "note": note,
            "created_at": now,
            "updated_at": now,
        }

    return {
        "schema_version": 1,
        "categories": [
            {"id": "salary", "name": "工资", "type": "income", "active": True},
            {"id": "food", "name": "饮食", "type": "expense", "active": True},
            {"id": "transport", "name": "交通", "type": "expense", "active": True},
        ],
        "entries": [
            entry("income", 300000, "salary", "演示收入"),
            entry("expense", 30000, "food", "午餐"),
            entry("expense", 17000, "food", "晚餐"),
        ],
        "budgets": {
            month: {"total_cents": 150000, "category_limits": {"food": 50000}}
        },
    }


def write_demo_ledger(output: Path, *, force: bool = False) -> Path:
    target = output.expanduser().resolve()
    if target.exists() and not force:
        raise FileExistsError(f"目标文件已存在：{target}。如确认覆盖，请添加 --force。")

    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_demo_ledger(), ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="生成独立的虚构记账演示数据")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSON 文件路径")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的目标文件")
    args = parser.parse_args()

    try:
        target = write_demo_ledger(args.output, force=args.force)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"演示账本已生成：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
