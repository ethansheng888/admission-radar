from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from admission_radar.config import ConfigError, load_config, resolve_website_recipients
from admission_radar.database import local_now
from admission_radar.fetcher import build_session, fetch_notices
from admission_radar.logging_setup import configure_logging
from admission_radar.mailer import check_email_connection, send_test_email
from admission_radar.monitor import scan
from admission_radar.state import atomic_json, preflight, summary
from admission_radar.sender import InactiveSender, require_sender, sender_status

PROJECT_DIR = Path(__file__).resolve().parent


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (LookupError, OSError):
                pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='监控招生公告；只读预览和状态不需要邮件凭证。')
    p.add_argument('--config', default=str(PROJECT_DIR / 'config.json'))
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--test-email', action='store_true')
    modes.add_argument('--preview', action='store_true', help='仅抓取解析，不写库/日志/状态，不发信')
    modes.add_argument('--status', action='store_true', help='仅查询历史状态，不发信')
    modes.add_argument('--preflight', action='store_true', help='校验已有历史库，不发信')
    modes.add_argument('--check-config', action='store_true', help='校验凭证与分组，仅显示数量')
    modes.add_argument('--smtp-check', action='store_true', help='仅连接认证，不提交邮件')
    modes.add_argument('--sender-check', action='store_true', help='只读共享发送端开关：0=本端主用，3=备用')
    p.add_argument('--test-email-website')
    args = p.parse_args()
    if args.test_email_website and not args.test_email:
        p.error('--test-email-website 必须与 --test-email 一起使用。')
    return args


def run() -> int:
    configure_console_encoding()
    args = parse_args()
    try:
        config = load_config(args.config, network_only=args.preview or args.status or args.preflight or args.sender_check)
        if args.sender_check:
            report = sender_status(config)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report['may_send'] else 3
        if args.preview:
            with build_session(config.request, config.websites) as session:
                report = {}
                for website in config.websites:
                    notices = fetch_notices(session, website, config.request)
                    report[website.id] = [dict(title=n.title, url=n.url, published_date=n.published_date) for n in notices]
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.status:
            report = {'websites': summary(config.database_path)}
            if config.status_path and config.status_path.exists():
                report['last_run'] = json.loads(config.status_path.read_text(encoding='utf-8'))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.preflight:
            preflight(config.database_path, tuple(w.id for w in config.websites))
            print('历史数据库检查通过。')
            return 0
        if args.check_config:
            groups = {w.id: len(resolve_website_recipients(w, config.email.to_addresses)) for w in config.websites}
            print(json.dumps(dict(email_enabled=config.email.enabled, recipient_counts=groups), ensure_ascii=False))
            return 0
        if args.smtp_check:
            if not config.email.enabled:
                raise ConfigError('邮件未启用。')
            check_email_connection(config.email)
            print('SMTP 连接认证成功；没有提交邮件。')
            return 0
        if args.test_email:
            if not config.email.enabled:
                raise ConfigError('邮件未启用。')
            website = next((w for w in config.websites if w.id == args.test_email_website), None)
            if args.test_email_website and website is None:
                raise ConfigError('测试网站不存在。')
            addresses = resolve_website_recipients(website, config.email.to_addresses) if website else config.email.to_addresses
            for address in addresses:
                send_test_email(replace(config.email, to_addresses=(address,)), website.name if website else None)
            print(f'SMTP 接受 {len(addresses)} 封分发测试邮件，请收件人确认收件箱和垃圾箱。')
            return 0
        try:
            require_sender(config)
        except InactiveSender:
            print('本端为备用；不进行正式扫描、状态写入或招生通知发送。')
            return 0
    except ConfigError as exc:
        print(f'配置错误：{exc}', file=sys.stderr)
        return 2
    except Exception as exc:
        print(f'检查失败，类型={type(exc).__name__}', file=sys.stderr)
        return 1

    logger = configure_logging(config.log_path)
    logger.info('招生公告监控启动')
    previous = {}
    if config.status_path and config.status_path.exists():
        try:
            previous = json.loads(config.status_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    report = dict(started_at=local_now(), ended_at=None, exit_code=None)
    if config.status_path:
        atomic_json(config.status_path, report)
    try:
        code, details = scan(config, logger)
        report['scan'] = details
        report['websites'] = summary(config.database_path)
    except ConfigError as exc:
        logger.error('配置错误：%s', exc)
        code = 2
    except Exception as exc:
        logger.error('执行失败，类型=%s', type(exc).__name__)
        report['error_type'] = type(exc).__name__
        code = 1
    report.update(ended_at=local_now(), exit_code=code,
                  consecutive_failures=previous.get('consecutive_failures', 0) + 1 if code else 0)
    if config.status_path:
        atomic_json(config.status_path, report)
    logger.info('招生公告监控结束；退出码=%d', code)
    return code


if __name__ == '__main__':
    raise SystemExit(run())
