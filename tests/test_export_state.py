from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

import test_vps as vps_fixtures
from admission_radar.database import RadarDatabase
from scripts.export_github_state import export


class StateExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=vps_fixtures.VPSReliabilityTests('test_existing_success_and_baseline_are_not_backfilled')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source=self.fixture.config.database_path
        self.output=self.fixture.root/'public.db'

    def test_consistent_wal_export_preserves_ids_fields_and_sequence_without_addresses(self):
        with RadarDatabase(self.source) as database:
            database.ensure_deliveries(self.fixture.website.id,self.fixture.email.to_addresses)
            expected={table:[tuple(row) for row in database.connection.execute('SELECT * FROM '+table+' ORDER BY id')] for table in ('websites','notices')}
            database.connection.execute("UPDATE sqlite_sequence SET seq=100 WHERE name='notices'")
            database.connection.commit()
            export(self.source,self.output)
        with sqlite3.connect(self.output) as exported:
            for table,rows in expected.items():
                self.assertEqual(exported.execute('SELECT * FROM '+table+' ORDER BY id').fetchall(),rows)
            self.assertNotIn('notice_deliveries',{r[0] for r in exported.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual(exported.execute("SELECT seq FROM sqlite_sequence WHERE name='notices'").fetchone()[0],100)
            self.assertEqual(exported.execute('PRAGMA user_version').fetchone()[0],0)
        for address in self.fixture.email.to_addresses:
            self.assertNotIn(address.encode(),self.output.read_bytes())

    def test_uncertain_or_partial_accepted_progress_cannot_be_silently_downgraded(self):
        with RadarDatabase(self.source) as database:
            database.ensure_deliveries(self.fixture.website.id,self.fixture.email.to_addresses)
            ids=[n.id for n in database.get_pending_notices(self.fixture.website.id)]
            database.start_delivery('a@example.com',ids)
            with self.assertRaises(ValueError): export(self.source,self.output)
            self.assertFalse(self.output.exists())
            database.accept_delivery('a@example.com',ids)
            with self.assertRaises(ValueError): export(self.source,self.output)
            self.assertFalse(self.output.exists())

    def test_missing_state_or_existing_destination_does_not_overwrite_history(self):
        with self.assertRaises(ValueError):export(self.fixture.root/'missing.db',self.output)
        self.assertFalse(self.output.exists())
        self.output.write_bytes(b'previous-state')
        with self.assertRaises(ValueError):export(self.source,self.output)
        self.assertEqual(self.output.read_bytes(),b'previous-state')
