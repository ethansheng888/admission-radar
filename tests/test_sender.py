from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from admission_radar.config import ConfigError, load_config
from admission_radar.database import RadarDatabase
from admission_radar.monitor import scan
from admission_radar.sender import InactiveSender, SenderPolicyError, read_policy, validate_policy_url
import test_vps as vps_fixtures
import main

URL = 'https://api.github.com/repos/ethansheng888/admission-radar/contents/config.sender.json?ref=main'


class SenderPolicyTests(unittest.TestCase):
    def reply(self, policy):
        response = MagicMock(status_code=200)
        response.content = b'{}'
        response.json.return_value = dict(type='file', encoding='base64', sha='a'*40,
                                         content=base64.b64encode(json.dumps(policy).encode()).decode())
        return response

    def test_reads_shared_switch_without_disabling_tls_or_following_redirects(self):
        with patch('admission_radar.sender.requests.Session') as create:
            session = create.return_value.__enter__.return_value
            session.get.return_value = self.reply(dict(schema_version=1, active_sender='vps'))
            result = read_policy(URL)
            self.assertEqual(result['active_sender'], 'vps')
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
            self.assertNotEqual(session.get.call_args.kwargs.get('verify', True), False)

    def test_unknown_missing_or_future_policy_refuses_sending(self):
        for policy in ({}, dict(schema_version=1, active_sender='both'),
                       dict(schema_version=2, active_sender='github'),
                       dict(schema_version=True, active_sender='vps')):
            with self.subTest(policy=policy), patch('admission_radar.sender.requests.Session') as create:
                create.return_value.__enter__.return_value.get.return_value = self.reply(policy)
                with self.assertRaises(SenderPolicyError): read_policy(URL)

    def test_each_policy_read_uses_a_fresh_cache_key(self):
        with patch('admission_radar.sender.requests.Session') as create, \
                patch('admission_radar.sender.time.time_ns', side_effect=[100,101]):
            session=create.return_value.__enter__.return_value
            session.get.return_value=self.reply(dict(schema_version=1,active_sender='github'))
            read_policy(URL); read_policy(URL)
            self.assertEqual([call.kwargs['params']['admission_read'] for call in session.get.call_args_list], ['100','101'])

    def test_network_errors_do_not_echo_tokens_or_server_bodies(self):
        secret = 'fake-private-token'
        with patch.dict(os.environ, {'ADMISSION_RADAR_POLICY_TOKEN': secret}), patch('admission_radar.sender.requests.Session') as create:
            create.return_value.__enter__.return_value.get.side_effect = RuntimeError(secret)
            with self.assertRaises(SenderPolicyError) as cm: read_policy(URL)
            self.assertNotIn(secret, str(cm.exception))

    def test_404_or_redirect_is_not_an_automatic_takeover(self):
        for status in (302, 404, 429, 500):
            with self.subTest(status=status), patch('admission_radar.sender.requests.Session') as create:
                response = MagicMock(status_code=status)
                create.return_value.__enter__.return_value.get.return_value = response
                with self.assertRaises(SenderPolicyError): read_policy(URL)
                response.json.assert_not_called()

    def test_only_fixed_github_contents_endpoint_is_allowed(self):
        for url in ('http://api.github.com/repos/a/b/contents/config.sender.json?ref=main',
                    'https://api.github.com@evil.example/repos/a/b/contents/config.sender.json?ref=main',
                    URL.replace('ref=main', 'ref=untrusted'), URL+'#ignored'):
            with self.subTest(url=url), self.assertRaises(ValueError): validate_policy_url(url)

    def test_sender_check_requires_no_mail_secrets_and_creates_no_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'config.json'
            path.write_text(json.dumps(dict(database_path='absent.db', log_path='absent.log', status_path='absent.json',
                sender_id='github', sender_policy_url=URL,
                email=dict(enabled=True, username='${SMTP_USERNAME}', password_env='SMTP_PASSWORD'),
                websites=[dict(id='cufe-master', name='央财', url='https://gs.cufe.edu.cn/', parser='cufe_master')])) )
            with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['main.py', '--config', str(path), '--sender-check']), \
                    patch('admission_radar.sender.read_policy', return_value=dict(active_sender='vps', policy_sha='a'*40)), patch('builtins.print'):
                self.assertEqual(main.run(), 3)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['config.json'])

    def test_half_configured_guard_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps(dict(sender_id='vps', websites=[dict(id='cufe-master', name='央财', url='https://gs.cufe.edu.cn/', parser='cufe_master')])))
            with self.assertRaises(ConfigError): load_config(path, network_only=True)


class SenderScanTests(unittest.TestCase):
    def setUp(self):
        # Reuse only fixture construction, without inheriting/duplicating its tests.
        self.fixture = vps_fixtures.VPSReliabilityTests('test_existing_success_and_baseline_are_not_backfilled')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = replace(self.fixture.config, sender_id='vps', sender_policy_url=URL)

    def test_standby_cannot_open_database_or_send(self):
        with patch('admission_radar.sender.read_policy', return_value=dict(active_sender='github', policy_sha='a'*40)), \
                patch('admission_radar.monitor.RadarDatabase') as database, patch('admission_radar.monitor.send_notices') as send:
            with self.assertRaises(InactiveSender): scan(self.config, logging.getLogger('sender-test'))
            database.assert_not_called(); send.assert_not_called()

    def test_switch_change_stops_next_recipient_before_marking_it_uncertain(self):
        with patch('admission_radar.monitor.require_sender', side_effect=[None,None,InactiveSender('standby')]), \
                patch('admission_radar.monitor.build_session'), \
                patch('admission_radar.monitor.fetch_notices', return_value=[self.fixture.old,self.fixture.new]), \
                patch('admission_radar.monitor.send_notices') as send:
            with self.assertRaises(InactiveSender): scan(self.config,logging.getLogger('sender-test'))
            self.assertEqual(send.call_count,1)
        with sqlite3.connect(self.config.database_path) as connection:
            self.assertEqual(connection.execute('SELECT recipient,status,attempts FROM notice_deliveries ORDER BY recipient').fetchall(),
                             [('a@example.com','accepted',1),('b@example.com','pending',0)])

    def test_policy_failure_blocks_smtp_and_keeps_pending(self):
        with patch('admission_radar.sender.read_policy',side_effect=SenderPolicyError('unavailable')), \
                patch('admission_radar.monitor.send_notices') as send:
            with self.assertRaises(SenderPolicyError): scan(self.config,logging.getLogger('sender-test'))
            send.assert_not_called()
        with RadarDatabase(self.config.database_path) as database:
            self.assertEqual(len(database.get_pending_notices(self.fixture.website.id)),1)
