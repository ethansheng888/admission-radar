from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .config import WebsiteConfig
from .models import Notice, ScanResult, StoredNotice


def local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RadarDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")

    def __enter__(self) -> "RadarDatabase":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        if self.connection.execute("PRAGMA user_version").fetchone()[0] > 1:
            raise ValueError("数据库版本比当前代码新，拒绝降级写入。")
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS websites (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    parser TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_check_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    website_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_date TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    is_baseline INTEGER NOT NULL DEFAULT 0,
                    notified_at TEXT,
                    FOREIGN KEY (website_id) REFERENCES websites(id)
                        ON DELETE CASCADE,
                    UNIQUE (website_id, url)
                );

                CREATE INDEX IF NOT EXISTS idx_notices_pending
                    ON notices (website_id, notified_at, is_baseline);

                CREATE TABLE IF NOT EXISTS notice_deliveries (
                    notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','uncertain','accepted')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    accepted_at TEXT,
                    error_code TEXT,
                    PRIMARY KEY(notice_id, recipient)
                );
                PRAGMA user_version = 1;
                """
            )

    def upsert_website(self, website: WebsiteConfig) -> None:
        now = local_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO websites (
                    id, name, url, parser, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    parser = excluded.parser,
                    updated_at = excluded.updated_at
                """,
                (
                    website.id,
                    website.name,
                    website.url,
                    website.parser,
                    now,
                    now,
                ),
            )

    def mark_check_started(self, website_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE websites
                SET last_check_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (local_now(), website_id),
            )

    def mark_check_failed(self, website_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE websites
                SET last_error = ?
                WHERE id = ?
                """,
                (error[:2000], website_id),
            )

    def store_scan(
        self,
        website_id: str,
        notices: list[Notice],
    ) -> ScanResult:
        now = local_now()
        inserted: list[StoredNotice] = []

        with self.connection:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM notices WHERE website_id = ?",
                (website_id,),
            ).fetchone()
            is_baseline = int(row["count"]) == 0

            for notice in notices:
                notified_at = now if is_baseline else None
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO notices (
                        website_id,
                        title,
                        url,
                        published_date,
                        first_seen_at,
                        last_seen_at,
                        is_baseline,
                        notified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        website_id,
                        notice.title,
                        notice.url,
                        notice.published_date,
                        now,
                        now,
                        1 if is_baseline else 0,
                        notified_at,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted.append(
                        StoredNotice(
                            id=int(cursor.lastrowid),
                            website_id=website_id,
                            title=notice.title,
                            url=notice.url,
                            published_date=notice.published_date,
                        )
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE notices
                        SET title = ?, published_date = ?, last_seen_at = ?
                        WHERE website_id = ? AND url = ?
                        """,
                        (
                            notice.title,
                            notice.published_date,
                            now,
                            website_id,
                            notice.url,
                        ),
                    )

            self.connection.execute(
                """
                UPDATE websites
                SET last_success_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (now, website_id),
            )

        return ScanResult(
            is_baseline=is_baseline,
            inserted=tuple(inserted),
        )

    def get_pending_notices(self, website_id: str) -> list[StoredNotice]:
        rows = self.connection.execute(
            """
            SELECT id, website_id, title, url, published_date
            FROM notices
            WHERE website_id = ?
              AND is_baseline = 0
              AND notified_at IS NULL
            ORDER BY first_seen_at ASC, id ASC
            """,
            (website_id,),
        ).fetchall()
        return [
            StoredNotice(
                id=int(row["id"]),
                website_id=str(row["website_id"]),
                title=str(row["title"]),
                url=str(row["url"]),
                published_date=(
                    str(row["published_date"])
                    if row["published_date"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def mark_notified(self, notice_ids: list[int]) -> None:
        if not notice_ids:
            return
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connection:
            self.connection.execute(
                f"""
                UPDATE notices
                SET notified_at = ?
                WHERE id IN ({placeholders})
                """,
                (local_now(), *notice_ids),
            )

    def ensure_deliveries(self, website_id: str, recipients: tuple[str, ...]) -> None:
        """Freeze targets only once for pending notices; never backfill successes."""
        if not recipients:
            raise ValueError("待通知收件组不能为空。")
        with self.connection:
            rows = self.connection.execute(
                """SELECT id FROM notices n WHERE website_id=? AND is_baseline=0
                   AND notified_at IS NULL AND NOT EXISTS
                   (SELECT 1 FROM notice_deliveries d WHERE d.notice_id=n.id)""",
                (website_id,),
            ).fetchall()
            self.connection.executemany(
                "INSERT INTO notice_deliveries(notice_id,recipient) VALUES (?,?)",
                [(r["id"], address) for r in rows for address in recipients],
            )

    def pending_delivery_groups(self, website_id: str) -> dict[str, list[StoredNotice]]:
        rows = self.connection.execute(
            """SELECT n.id,n.website_id,n.title,n.url,n.published_date,d.recipient
               FROM notices n JOIN notice_deliveries d ON d.notice_id=n.id
               WHERE n.website_id=? AND n.is_baseline=0 AND n.notified_at IS NULL
                 AND d.status!='accepted' ORDER BY n.first_seen_at,n.id,d.recipient""",
            (website_id,),
        ).fetchall()
        groups: dict[str, list[StoredNotice]] = {}
        for row in rows:
            groups.setdefault(row["recipient"], []).append(StoredNotice(
                id=row["id"], website_id=row["website_id"], title=row["title"],
                url=row["url"], published_date=row["published_date"],
            ))
        return groups

    def start_delivery(self, recipient: str, ids: list[int]) -> None:
        # A crash after SMTP acceptance must remain visible as uncertain.
        with self.connection:
            self.connection.executemany(
                """UPDATE notice_deliveries SET status='uncertain',attempts=attempts+1,
                   last_attempt_at=?,error_code=NULL WHERE notice_id=? AND recipient=?
                   AND status!='accepted'""",
                [(local_now(), nid, recipient) for nid in ids],
            )

    def fail_delivery(self, recipient: str, ids: list[int], error_code: str, *, uncertain: bool) -> None:
        with self.connection:
            self.connection.executemany(
                """UPDATE notice_deliveries SET status=?,error_code=?
                   WHERE notice_id=? AND recipient=? AND status!='accepted'""",
                [("uncertain" if uncertain else "pending", error_code, nid, recipient) for nid in ids],
            )

    def accept_delivery(self, recipient: str, ids: list[int]) -> None:
        now = local_now()
        with self.connection:
            self.connection.executemany(
                """UPDATE notice_deliveries SET status='accepted',accepted_at=?,error_code=NULL
                   WHERE notice_id=? AND recipient=?""", [(now, nid, recipient) for nid in ids],
            )
            self.connection.executemany(
                """UPDATE notices SET notified_at=? WHERE id=? AND notified_at IS NULL
                   AND EXISTS(SELECT 1 FROM notice_deliveries WHERE notice_id=notices.id)
                   AND NOT EXISTS(SELECT 1 FROM notice_deliveries
                                  WHERE notice_id=notices.id AND status!='accepted')""",
                [(now, nid) for nid in ids],
            )
