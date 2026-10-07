from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


def open_readonly(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("生产历史数据库缺失或为空，拒绝重新建立基线。")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""), uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def preflight(path: Path, required_ids: tuple[str, ...]) -> None:
    with open_readonly(path) as c:
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("历史数据库完整性检查失败。")
        if c.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("历史数据库外键检查失败。")
        for wid in required_ids:
            if c.execute("SELECT 1 FROM websites WHERE id=?", (wid,)).fetchone() is None:
                raise ValueError(f"历史数据库缺少已有网站：{wid}")
            if c.execute("SELECT COUNT(*) FROM notices WHERE website_id=?", (wid,)).fetchone()[0] == 0:
                raise ValueError(f"已有网站历史为空：{wid}")


def summary(path: Path) -> dict:
    with open_readonly(path) as c:
        rows = c.execute("""SELECT w.id,w.last_check_at,w.last_success_at,
            CASE WHEN w.last_error IS NULL THEN 0 ELSE 1 END AS fetch_error,
            COUNT(n.id) AS total,COALESCE(SUM(n.is_baseline),0) AS baseline,
            COALESCE(SUM(CASE WHEN n.is_baseline=0 AND n.notified_at IS NULL THEN 1 ELSE 0 END),0) AS pending,
            MIN(CASE WHEN n.is_baseline=0 AND n.notified_at IS NULL THEN n.first_seen_at END) AS oldest_pending_at
            FROM websites w LEFT JOIN notices n ON n.website_id=w.id GROUP BY w.id""").fetchall()
        result = {r["id"]: dict(r) for r in rows}
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='notice_deliveries'").fetchone():
            for r in c.execute("""SELECT n.website_id,COUNT(*) AS pending_deliveries,
                SUM(CASE WHEN d.status='uncertain' THEN 1 ELSE 0 END) AS uncertain_deliveries
                FROM notice_deliveries d JOIN notices n ON n.id=d.notice_id
                WHERE d.status!='accepted' GROUP BY n.website_id"""):
                result[r["website_id"]].update({"pending_deliveries":r["pending_deliveries"], "uncertain_deliveries":r["uncertain_deliveries"]})
        return result


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
