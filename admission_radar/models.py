from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Notice:
    """从公告列表页提取的一条公告。"""

    title: str
    url: str
    published_date: str | None = None


@dataclass(frozen=True)
class StoredNotice:
    """SQLite 中的一条公告。"""

    id: int
    website_id: str
    title: str
    url: str
    published_date: str | None


@dataclass(frozen=True)
class ScanResult:
    """一次成功抓取写入数据库后的结果。"""

    is_baseline: bool
    inserted: tuple[StoredNotice, ...]
