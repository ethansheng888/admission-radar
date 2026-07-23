from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from time import time


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按指定周期更新 GitHub Actions 心跳文件。"
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-age-days", type=int, default=30)
    args = parser.parse_args()

    if args.max_age_days <= 0:
        parser.error("--max-age-days 必须大于 0")

    max_age_seconds = args.max_age_days * 24 * 60 * 60
    should_update = (
        not args.path.exists()
        or time() - args.path.stat().st_mtime >= max_age_seconds
    )
    if not should_update:
        print("heartbeat unchanged")
        return 0

    args.path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.path.write_text(timestamp + "\n", encoding="utf-8")
    print("heartbeat updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
