from __future__ import annotations
import json
import logging
import os
import shutil
import smtplib
import sqlite3
import ssl
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch
import certifi
import requests

from admission_radar.config import AppConfig, EmailConfig, RequestConfig, WebsiteConfig, load_config
from admission_radar.database import RadarDatabase
from admission_radar.fetcher import FetchError, ScopedTLSAdapter, build_session
from admission_radar.mailer import EmailError, _deliver, _deliver_once, send_test_email
from admission_radar.models import Notice
from admission_radar.monitor import scan
from admission_radar.state import preflight
from scripts.backup_state import backup
from scripts.check_health import check
import main


class VPSReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.website = WebsiteConfig('cufe-master', '中央财经大学', 'https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm', 'cufe_master')
        self.email = EmailConfig(True, 'smtp.example.com', 465, 'ssl', 'sender@example.com', 'fake-test-password', '', 'sender@example.com', ('a@example.com','b@example.com'), '[监控]', 30)
        self.config = AppConfig(self.root/'config.json', self.root/'radar.db', self.root/'monitor.log', RequestConfig(20,2,1.0,'Test'), self.email, (self.website,), track_recipient_deliveries=True)
        self.old = Notice('旧公告', 'https://gs.cufe.edu.cn/info/1028/1.htm')
        self.new = Notice('新公告', 'https://gs.cufe.edu.cn/info/1028/2.htm')
        with RadarDatabase(self.config.database_path) as db:
            db.initialize(); db.upsert_website(self.website)
            db.store_scan(self.website.id, [self.old])
            db.store_scan(self.website.id, [self.old,self.new])

    def test_partial_failure_only_retries_failed_recipient(self):
        with patch('admission_radar.monitor.build_session'), patch('admission_radar.monitor.fetch_notices',return_value=[self.old,self.new]), patch('admission_radar.monitor.send_notices',side_effect=[None,EmailError('test')]) as send:
            code,_ = scan(self.config, logging.getLogger('test'))
            self.assertEqual(code,1); self.assertEqual(send.call_count,2)
        with RadarDatabase(self.config.database_path) as db:
            self.assertEqual(list(db.pending_delivery_groups(self.website.id)),['b@example.com'])
        with patch('admission_radar.monitor.build_session'), patch('admission_radar.monitor.fetch_notices',return_value=[self.old,self.new]), patch('admission_radar.monitor.send_notices') as send:
            code,_ = scan(self.config, logging.getLogger('test'))
            self.assertEqual(code,0)
            self.assertEqual(send.call_args.args[0].to_addresses,('b@example.com',))
            self.assertEqual(send.call_count,1)
        with RadarDatabase(self.config.database_path) as db:
            self.assertEqual(db.get_pending_notices(self.website.id),[])

    def test_pending_is_sent_even_when_fetch_fails(self):
        with patch('admission_radar.monitor.build_session'), patch('admission_radar.monitor.fetch_notices',side_effect=FetchError('test')), patch('admission_radar.monitor.send_notices') as send:
            code,report = scan(self.config, logging.getLogger('test'))
            self.assertEqual(code,1); self.assertEqual(send.call_count,2)
            self.assertFalse(report[self.website.id]['fetch_success'])
        with RadarDatabase(self.config.database_path) as db:
            self.assertEqual(db.get_pending_notices(self.website.id),[])

    def test_existing_success_and_baseline_are_not_backfilled(self):
        with RadarDatabase(self.config.database_path) as db:
            db.mark_notified([n.id for n in db.get_pending_notices(self.website.id)])
            db.ensure_deliveries(self.website.id, self.email.to_addresses)
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM notice_deliveries').fetchone()[0],0)

    def test_frozen_targets_are_not_changed_by_new_recipient(self):
        with RadarDatabase(self.config.database_path) as db:
            db.ensure_deliveries(self.website.id,self.email.to_addresses)
            db.ensure_deliveries(self.website.id,('c@example.com',))
            self.assertEqual(set(db.pending_delivery_groups(self.website.id)),set(self.email.to_addresses))

    def test_crash_after_start_preserves_uncertain_delivery(self):
        with RadarDatabase(self.config.database_path) as db:
            db.ensure_deliveries(self.website.id,self.email.to_addresses)
            ids=[n.id for n in db.get_pending_notices(self.website.id)]
            db.start_delivery('a@example.com',ids)
        with RadarDatabase(self.config.database_path) as db:
            self.assertEqual(db.connection.execute("SELECT status FROM notice_deliveries WHERE recipient='a@example.com'").fetchone()[0],'uncertain')
            self.assertIn('a@example.com',db.pending_delivery_groups(self.website.id))

    def test_public_cloud_mode_does_not_store_recipient_addresses(self):
        with patch('admission_radar.monitor.build_session'), patch('admission_radar.monitor.fetch_notices',return_value=[self.old,self.new]), patch('admission_radar.monitor.send_notices'):
            code,_=scan(replace(self.config,track_recipient_deliveries=False),logging.getLogger('test'))
            self.assertEqual(code,0)
        with RadarDatabase(self.config.database_path) as db:
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM notice_deliveries').fetchone()[0],0)

    def test_quit_failure_after_acceptance_does_not_retry(self):
        client=MagicMock(); client.send_message.return_value={}
        client.quit.side_effect=smtplib.SMTPServerDisconnected('fake sensitive response')
        with patch('admission_radar.mailer._open_smtp',return_value=client):
            _deliver(self.email,MagicMock())
        self.assertEqual(client.send_message.call_count,1)

    def test_authentication_error_does_not_echo_server_response(self):
        client=MagicMock(); client.login.side_effect=smtplib.SMTPAuthenticationError(535,b'fake-test-password')
        with patch('admission_radar.mailer._open_smtp',return_value=client):
            with self.assertRaises(EmailError) as cm: _deliver_once(self.email,MagicMock())
        self.assertNotIn('fake-test-password',str(cm.exception))

    def test_uncertain_submission_is_not_retried_immediately(self):
        client=MagicMock(); client.send_message.side_effect=smtplib.SMTPServerDisconnected('lost ack')
        with patch('admission_radar.mailer._open_smtp',return_value=client):
            with self.assertRaises(EmailError) as cm: _deliver(self.email,MagicMock())
        self.assertTrue(cm.exception.uncertain)
        self.assertEqual(client.send_message.call_count,1)

    def test_bjtu_test_message_uses_correct_name(self):
        with patch('admission_radar.mailer._deliver') as deliver:
            send_test_email(self.email,'北京交通大学硕士招生')
        message=deliver.call_args.args[1]
        content=message.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('北京交通大学',content); self.assertNotIn('中央财经大学',content)

    def test_preflight_refuses_missing_or_empty_school_history(self):
        with self.assertRaises(ValueError): preflight(self.root/'absent.db',('cufe-master',))
        self.assertFalse((self.root/'absent.db').exists())
        with self.assertRaises(ValueError): preflight(self.config.database_path,('bjtu-master',))

    def test_backup_captures_committed_wal_and_preserves_old_backups_on_failure(self):
        with RadarDatabase(self.config.database_path) as db:
            db.store_scan(self.website.id,[self.old,self.new,Notice('WAL公告','https://gs.cufe.edu.cn/info/1028/3.htm')])
            target=backup(self.config.database_path,self.root/'backups',keep=1)
            with sqlite3.connect(target) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM notices').fetchone()[0],3)
            with self.assertRaises(ValueError): backup(self.root/'missing.db',self.root/'backups',keep=1)
            self.assertTrue(target.exists())

    def test_preview_without_secrets_does_not_write_db_log_or_status(self):
        path=self.root/'preview.json'
        path.write_text(json.dumps(dict(database_path='never.db',log_path='never.log',status_path='never.json',email=dict(enabled=True,username='${SMTP_USERNAME}',password_env='SMTP_PASSWORD'),websites=[dict(id='cufe-master',name='中财',url=self.website.url,parser='cufe_master')])))
        with patch.dict(os.environ,{},clear=True), patch('sys.argv',['main.py','--config',str(path),'--preview']), patch('main.build_session'), patch('main.fetch_notices',return_value=[self.old]), patch('builtins.print'):
            self.assertEqual(main.run(),0)
        for name in ('never.db','never.log','never.json'): self.assertFalse((self.root/name).exists())

    def test_scoped_tls_context_keeps_verification_and_does_not_affect_bjtu(self):
        w=replace(self.website,allow_legacy_server_connect=True,tls_intermediate_path=Path(certifi.where()))
        with build_session(self.config.request,(w,)) as session:
            adapter=session.get_adapter(w.url)
            self.assertIsInstance(adapter,ScopedTLSAdapter)
            self.assertNotIsInstance(session.get_adapter('https://yzb.bjtu.edu.cn/sszs/index.htm'),ScopedTLSAdapter)
            self.assertTrue(adapter.context.options & ssl.OP_NO_RENEGOTIATION)
            self.assertEqual(adapter.context.verify_mode,ssl.CERT_REQUIRED)
            prepared=requests.Request('GET',w.url).prepare()
            _,options=adapter.build_connection_pool_key_attributes(prepared,True)
            self.assertIs(options['ssl_context'],adapter.context)
            with self.assertRaises(FetchError): adapter.build_connection_pool_key_attributes(prepared,False)

    def test_schema_upgrade_preserves_all_legacy_fields(self):
        source=self.config.database_path
        with sqlite3.connect(source) as c:
            c.execute('DROP TABLE notice_deliveries')
            c.execute('PRAGMA user_version=0')
        target=self.root/'legacy.db'; shutil.copyfile(source,target)
        with sqlite3.connect(source.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
            expected={table:c.execute('SELECT * FROM '+table+' ORDER BY id').fetchall() for table in ('websites','notices')}
        with RadarDatabase(target) as db:
            db.initialize()
            for table,rows in expected.items():
                self.assertEqual([tuple(r) for r in db.connection.execute('SELECT * FROM '+table+' ORDER BY id')],rows)
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM notice_deliveries').fetchone()[0],0)

    def test_two_schools_do_not_mix_recipients_or_names(self):
        bjtu=WebsiteConfig('bjtu-master','北京交通大学','https://yzb.bjtu.edu.cn/sszs/index.htm','bjtu_master','BJTU_RECIPIENT')
        with RadarDatabase(self.config.database_path) as db:
            db.upsert_website(bjtu);db.store_scan(bjtu.id,[self.old]);db.store_scan(bjtu.id,[self.old,self.new])
        cufe=replace(self.website,recipient_env='CUFE_RECIPIENTS')
        cfg=replace(self.config,websites=(cufe,bjtu))
        with patch.dict(os.environ,{'CUFE_RECIPIENTS':'a@example.com,b@example.com','BJTU_RECIPIENT':'friend@example.com'}),patch('admission_radar.monitor.build_session'),patch('admission_radar.monitor.fetch_notices',return_value=[self.old,self.new]),patch('admission_radar.monitor.send_notices') as send:
            self.assertEqual(scan(cfg,logging.getLogger('test'))[0],0)
        targets=[(call.args[0].to_addresses,call.args[1]) for call in send.call_args_list]
        self.assertEqual(targets,[(('a@example.com',),'中央财经大学'),(('b@example.com',),'中央财经大学'),(('friend@example.com',),'北京交通大学')])

    def test_health_is_quiet_when_unchanged_and_notifies_recovery_once(self):
        with patch('scripts.check_health.health_issues',return_value=['scan:stale']),patch('scripts.check_health.send_health_email') as send,patch('builtins.print'):
            self.assertEqual(check(self.config,self.root/'backups',alert=True),1)
            self.assertEqual(send.call_count,2)
            self.assertEqual(check(self.config,self.root/'backups',alert=True),1)
            self.assertEqual(send.call_count,2)
        with patch('scripts.check_health.health_issues',return_value=[]),patch('scripts.check_health.send_health_email') as send,patch('builtins.print'):
            self.assertEqual(check(self.config,self.root/'backups',alert=True),0)
            self.assertEqual(send.call_count,2)
            self.assertEqual(check(self.config,self.root/'backups',alert=True),0)
            self.assertEqual(send.call_count,2)

    def test_failed_health_notification_is_not_marked_as_sent(self):
        with patch('scripts.check_health.health_issues',return_value=['scan:stale']),patch('scripts.check_health.send_health_email',side_effect=EmailError('fake test failure')):
            with self.assertRaises(EmailError):check(self.config,self.root/'backups',alert=True)
        self.assertFalse((self.root/'health.json').exists())


if __name__=='__main__': unittest.main()
