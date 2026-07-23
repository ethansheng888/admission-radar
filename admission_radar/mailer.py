from __future__ import annotations

import html
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate

from .config import EmailConfig
from .models import StoredNotice


class EmailError(RuntimeError):
    """邮件发送失败。"""


LOGGER = logging.getLogger(__name__)
DELIVERY_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (3, 10)


def _open_smtp(config: EmailConfig):
    context = ssl.create_default_context()
    if config.security == "ssl":
        return smtplib.SMTP_SSL(
            config.smtp_host,
            config.smtp_port,
            timeout=config.timeout_seconds,
            context=context,
        )

    client = smtplib.SMTP(
        config.smtp_host,
        config.smtp_port,
        timeout=config.timeout_seconds,
    )
    if config.security == "starttls":
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    return client


def _deliver_once(config: EmailConfig, message: EmailMessage) -> None:
    try:
        with _open_smtp(config) as client:
            if config.username:
                client.login(config.username, config.resolved_password())
            refused = client.send_message(message)
            if refused:
                refused_addresses = "、".join(sorted(refused))
                raise EmailError(f"SMTP 拒收以下收件人：{refused_addresses}")
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailError(str(exc)) from exc


def _deliver(config: EmailConfig, message: EmailMessage) -> None:
    last_error: EmailError | None = None
    for attempt in range(1, DELIVERY_ATTEMPTS + 1):
        try:
            _deliver_once(config, message)
            return
        except EmailError as exc:
            last_error = exc
            if attempt >= DELIVERY_ATTEMPTS:
                break
            delay = RETRY_DELAYS_SECONDS[attempt - 1]
            LOGGER.warning(
                "邮件发送第 %d/%d 次失败，%d 秒后重试：%s",
                attempt,
                DELIVERY_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)

    raise EmailError(
        f"连续尝试 {DELIVERY_ATTEMPTS} 次仍失败：{last_error}"
    ) from last_error


def _base_message(config: EmailConfig, subject: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = ", ".join(config.to_addresses)
    message["Date"] = formatdate(localtime=True)
    return message


def send_notices(
    config: EmailConfig,
    website_name: str,
    notices: list[StoredNotice],
) -> None:
    count = len(notices)
    subject = f"{config.subject_prefix} {website_name}新增 {count} 条公告"
    message = _base_message(config, subject)

    plain_lines = [
        f"{website_name}发现 {count} 条新公告：",
        "",
    ]
    html_items: list[str] = []
    for notice in notices:
        date_text = f"（{notice.published_date}）" if notice.published_date else ""
        plain_lines.extend(
            [
                f"- {notice.title}{date_text}",
                f"  {notice.url}",
                "",
            ]
        )
        html_items.append(
            "<li>"
            f'<a href="{html.escape(notice.url, quote=True)}">'
            f"{html.escape(notice.title)}</a>"
            f" {html.escape(date_text)}"
            "</li>"
        )

    plain_lines.append("本邮件由本地“招生公告监控”程序自动发送。")
    html_body = (
        f"<h2>{html.escape(website_name)}发现 {count} 条新公告</h2>"
        f"<ul>{''.join(html_items)}</ul>"
        "<p>点击标题可直接打开学校官网公告。</p>"
        "<p style=\"color:#666\">本邮件由本地“招生公告监控”程序自动发送。</p>"
    )

    message.set_content("\n".join(plain_lines))
    message.add_alternative(html_body, subtype="html")
    _deliver(config, message)


def send_test_email(config: EmailConfig) -> None:
    subject = f"{config.subject_prefix} 邮件配置测试"
    message = _base_message(config, subject)
    message.set_content(
        "邮件配置测试成功。\n\n"
        "今后检测到中央财经大学硕士招生新公告时，程序会发送类似邮件。"
    )
    message.add_alternative(
        "<h2>邮件配置测试成功</h2>"
        "<p>今后检测到中央财经大学硕士招生新公告时，"
        "程序会发送类似邮件。</p>",
        subtype="html",
    )
    _deliver(config, message)
