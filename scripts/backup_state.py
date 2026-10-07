from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import argparse
import os
import sqlite3
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from admission_radar.config import load_config
from admission_radar.state import open_readonly, atomic_json


def backup(source: Path, destination: Path, keep: int = 14, tag: str = "daily") -> Path:
    if keep < 1 or tag not in ("daily", "manual"):
        raise ValueError("invalid retention/tag")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".backup-", dir=destination)
    os.close(fd)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    final = destination / f"{tag}-{now.strftime('%Y%m%dT%H%M%S%f%z')}.db"
    try:
        src = open_readonly(source)
        dst = sqlite3.connect(temp)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("backup integrity failed")
            if dst.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("backup foreign keys failed")
        finally:
            dst.close(); src.close()
        os.chmod(temp, 0o600)
        with open(temp, "rb") as f: os.fsync(f.fileno())
        os.replace(temp, final)
        atomic_json(destination / "last-backup.json", {"completed_at":now.isoformat(),"filename":final.name})
        if tag == "daily":
            for old in sorted(destination.glob("daily-*.db"), reverse=True)[keep:]: old.unlink()
        return final
    finally:
        if os.path.exists(temp): os.unlink(temp)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--destination", required=True, type=Path)
    p.add_argument("--keep", type=int, default=14)
    p.add_argument("--tag", choices=("daily", "manual"), default="daily")
    args = p.parse_args()
    try:
        config = load_config(args.config, network_only=True)
        print("一致性备份完成：" + backup(config.database_path, args.destination, args.keep, args.tag).name)
    except Exception as exc:
        print("备份失败，类型=" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
