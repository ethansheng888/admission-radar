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
    client = None
    phase = "连接 SMTP"
    try:
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
            phase = "协商 STARTTLS"
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        return client
    except (OSError, smtplib.SMTPException) as exc:
        if client is not None:
            client.close()
        raise EmailError(
            f"{phase}失败（{config.smtp_host}:{config.smtp_port}，"
            f"{type(exc).__name__}）：{exc}"
        ) from exc


def _authenticate(client, config: EmailConfig) -> None:
    try:
        client.ehlo_or_helo_if_needed()
        if config.username:
            client.login(config.username, config.resolved_password())
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailError(f"SMTP 登录失败（{type(exc).__name__}）：{exc}") from exc


def check_email_connection(config: EmailConfig) -> None:
    """检查连接和认证，不发送邮件，也不修改公告状态。"""
    client = _open_smtp(config)
    try:
        _authenticate(client, config)
    finally:
        client.close()


def _deliver_once(config: EmailConfig, message: EmailMessage) -> None:
    client = _open_smtp(config)
    try:
        _authenticate(client, config)
        try:
            refused = client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailError(
                f"SMTP 提交邮件失败（{type(exc).__name__}）：{exc}"
            ) from exc
        if refused:
            refused_addresses = "、".join(sorted(refused))
            raise EmailError(f"SMTP 拒收以下收件人：{refused_addresses}")

        # send_message 已确认服务器接受邮件；QUIT 失败不能把它变成
        # 发送失败，否则下次运行会重复发送已被接受的通知。
        try:
            client.quit()
        except (OSError, smtplib.SMTPException) as exc:
            LOGGER.warning("邮件已被 SMTP 接受，退出连接时发生错误：%s", exc)
    finally:
        client.close()


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
