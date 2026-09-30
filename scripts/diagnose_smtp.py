from __future__ import annotations

import argparse
import smtplib
from dataclasses import replace
from pathlib import Path

from admission_radar.config import ConfigError, load_config
from admission_radar.mailer import EmailError, _open_smtp, check_email_connection


def check_login_challenge(config) -> None:
    """比较标准 LOGIN 挑战应答，确认是否为默认认证机制的兼容问题。"""
    client = _open_smtp(config)
    try:
        client.ehlo_or_helo_if_needed()
        supported = client.esmtp_features.get("auth", "").upper().split()
        if "LOGIN" not in supported:
            raise EmailError("SMTP 未声明支持 LOGIN 认证。")
        client.user = config.username
        client.password = config.resolved_password()
        client.auth("LOGIN", client.auth_login, initial_response_ok=False)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailError(f"SMTP LOGIN 登录失败（{type(exc).__name__}）：{exc}") from exc
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="只检查 SMTP 连接和登录，不发送邮件。")
    parser.add_argument("--config", default="config.cloud.json")
    parser.add_argument("--compare-qq", action="store_true", help="比较 QQ 加密端口和认证方式，均不发邮件")
    args = parser.parse_args()
    try:
        config = load_config(Path(args.config))
        if not config.email.enabled:
            raise ConfigError("邮件未启用。")
    except ConfigError as exc:
        print(f"SMTP 配置错误：{exc}", flush=True)
        return 1

    probes = [("当前配置", config.email, check_email_connection)]
    if args.compare_qq:
        if config.email.smtp_host.lower() != "smtp.qq.com":
            print("--compare-qq 仅适用于 smtp.qq.com。", flush=True)
            return 1
        ssl_config = replace(config.email, smtp_port=465, security="ssl")
        probes = [
            ("465 SSL 默认认证", ssl_config, check_email_connection),
            ("465 SSL LOGIN 挑战应答", ssl_config, check_login_challenge),
            ("587 STARTTLS 默认认证", replace(config.email, smtp_port=587, security="starttls"), check_email_connection),
        ]

    successful = 0
    for name, email, check in probes:
        print(f"检查：{name}", flush=True)
        try:
            check(email)
        except EmailError as exc:
            print(f"{name}失败：{exc}", flush=True)
        else:
            successful += 1
            print(f"{name}连接和登录成功。", flush=True)
    print("诊断结束；未发送邮件，未修改公告状态。", flush=True)
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
