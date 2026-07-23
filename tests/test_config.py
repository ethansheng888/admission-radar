from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from admission_radar.config import (
    ConfigError,
    WebsiteConfig,
    load_config,
    resolve_website_recipients,
)


def cloud_config() -> dict:
    return {
        "database_path": "state/radar.db",
        "log_path": "logs/cloud.log",
        "email": {
            "enabled": True,
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "security": "ssl",
            "username": "${SMTP_USERNAME}",
            "password": "",
            "password_env": "SMTP_PASSWORD",
            "from_address": "${SMTP_USERNAME}",
            "to_addresses": ["${SMTP_USERNAME}"],
        },
        "websites": [
            {
                "id": "cufe-master",
                "name": "中财硕士招生",
                "url": "https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm",
                "parser": "cufe_master",
            }
        ],
    }


class ConfigTests(unittest.TestCase):
    def test_resolves_cloud_email_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(cloud_config(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "SMTP_USERNAME": "sender@example.com",
                    "SMTP_PASSWORD": "secret-value",
                },
                clear=False,
            ):
                config = load_config(config_path)
                self.assertEqual(config.email.username, "sender@example.com")
                self.assertEqual(
                    config.email.to_addresses,
                    ("sender@example.com",),
                )
                self.assertEqual(
                    config.email.resolved_password(),
                    "secret-value",
                )

    def test_missing_environment_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(cloud_config(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "SMTP_USERNAME": "",
                    "SMTP_PASSWORD": "secret-value",
                },
                clear=False,
            ):
                with self.assertRaises(ConfigError):
                    load_config(config_path)

    def test_routes_each_website_to_its_own_recipient(self) -> None:
        website = WebsiteConfig(
            id="bjtu-master",
            name="北京交通大学硕士招生",
            url="https://yzb.bjtu.edu.cn/sszs/index.htm",
            parser="bjtu_master",
            recipient_env="BJTU_RECIPIENT",
        )
        with patch.dict(
            os.environ,
            {"BJTU_RECIPIENT": "friend@example.com"},
            clear=False,
        ):
            recipients = resolve_website_recipients(
                website,
                ("owner@example.com",),
            )
        self.assertEqual(recipients, ("friend@example.com",))

    def test_website_without_override_uses_default_recipient(self) -> None:
        website = WebsiteConfig(
            id="cufe-master",
            name="中央财经大学硕士招生",
            url="https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm",
            parser="cufe_master",
        )
        recipients = resolve_website_recipients(
            website,
            ("owner@example.com",),
        )
        self.assertEqual(recipients, ("owner@example.com",))


if __name__ == "__main__":
    unittest.main()
