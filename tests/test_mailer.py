from __future__ import annotations

import unittest
from email.message import EmailMessage
from unittest.mock import patch

from admission_radar.config import EmailConfig
from admission_radar.mailer import EmailError, _deliver, send_notices
from admission_radar.models import StoredNotice


class MailerTests(unittest.TestCase):
    @staticmethod
    def make_config() -> EmailConfig:
        return EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            security="ssl",
            username="sender@example.com",
            password="test-only",
            password_env="",
            from_address="sender@example.com",
            to_addresses=("receiver@example.com",),
            subject_prefix="[招生公告监控]",
            timeout_seconds=30,
        )

    def test_notice_email_contains_clickable_link_and_full_title(self) -> None:
        config = self.make_config()
        notice = StoredNotice(
            id=1,
            website_id="cufe-master",
            title="中央财经大学测试公告",
            url="https://gs.cufe.edu.cn/info/1028/9999.htm",
            published_date="2026-07-23",
        )

        with patch("admission_radar.mailer._deliver") as deliver:
            send_notices(config, "中央财经大学硕士招生（双证）", [notice])

        deliver.assert_called_once()
        message = deliver.call_args.args[1]
        self.assertIn("新增 1 条公告", str(message["Subject"]))
        self.assertIn(notice.title, message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn(
            notice.url,
            message.get_body(preferencelist=("html",)).get_content(),
        )

    def test_delivery_retries_transient_failures(self) -> None:
        message = EmailMessage()
        message["To"] = "receiver@example.com"

        with (
            patch(
                "admission_radar.mailer._deliver_once",
                side_effect=[EmailError("temporary"), None],
            ) as deliver_once,
            patch("admission_radar.mailer.time.sleep") as sleep,
        ):
            _deliver(self.make_config(), message)

        self.assertEqual(deliver_once.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_delivery_fails_after_three_attempts(self) -> None:
        message = EmailMessage()
        message["To"] = "receiver@example.com"

        with (
            patch(
                "admission_radar.mailer._deliver_once",
                side_effect=EmailError("offline"),
            ) as deliver_once,
            patch("admission_radar.mailer.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(EmailError, "连续尝试 3 次仍失败"):
                _deliver(self.make_config(), message)

        self.assertEqual(deliver_once.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
