from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admission_radar.config import WebsiteConfig
from admission_radar.database import RadarDatabase
from admission_radar.models import Notice


class DatabaseTests(unittest.TestCase):
    def test_baseline_then_detects_and_tracks_new_notice(self) -> None:
        website = WebsiteConfig(
            id="cufe-master",
            name="中央财经大学硕士招生（双证）",
            url="https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm",
            parser="cufe_master",
        )
        baseline = [
            Notice("公告 A", "https://gs.cufe.edu.cn/info/1028/1.htm", "2026-01-01"),
            Notice("公告 B", "https://gs.cufe.edu.cn/info/1028/2.htm", "2026-01-02"),
        ]
        new_notice = Notice(
            "公告 C",
            "https://gs.cufe.edu.cn/info/1028/3.htm",
            "2026-01-03",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "radar.db"
            with RadarDatabase(database_path) as database:
                database.initialize()
                database.upsert_website(website)

                first = database.store_scan(website.id, baseline)
                self.assertTrue(first.is_baseline)
                self.assertEqual(len(first.inserted), 2)
                self.assertEqual(database.get_pending_notices(website.id), [])

                second = database.store_scan(website.id, baseline)
                self.assertFalse(second.is_baseline)
                self.assertEqual(second.inserted, ())

                third = database.store_scan(website.id, [new_notice, *baseline])
                self.assertFalse(third.is_baseline)
                self.assertEqual(len(third.inserted), 1)
                pending = database.get_pending_notices(website.id)
                self.assertEqual([item.title for item in pending], ["公告 C"])

                database.mark_notified([pending[0].id])
                self.assertEqual(database.get_pending_notices(website.id), [])


if __name__ == "__main__":
    unittest.main()
