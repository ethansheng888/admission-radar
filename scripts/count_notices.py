from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="输出招生公告 SQLite 数据库中的公告总数。"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    if not args.database.exists():
        print(0)
        return 0

    try:
        with sqlite3.connect(args.database) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM notices"
            ).fetchone()
    except sqlite3.Error as exc:
        parser.error(f"无法读取状态数据库：{exc}")

    print(int(row[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
