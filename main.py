from __future__ import annotations

import argparse
import sys
from pathlib import Path

from admission_radar.config import ConfigError, load_config
from admission_radar.database import RadarDatabase
from admission_radar.fetcher import FetchError, build_session, fetch_notices
from admission_radar.logging_setup import configure_logging
from admission_radar.mailer import EmailError, send_notices, send_test_email


PROJECT_DIR = Path(__file__).resolve().parent


def configure_console_encoding() -> None:
    # Windows 任务计划程序经常把输出连接到传统代码页。显式使用 UTF-8，
    # 可让重定向输出和自动化工具正确显示中文；文件日志本身始终是 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (LookupError, OSError):
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="监控招生公告列表页，发现新公告后发送邮件。"
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_DIR / "config.json"),
        help="配置文件路径（默认：项目目录下的 config.json）",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="只发送一封测试邮件，不抓取网页、不修改数据库",
    )
    return parser.parse_args()


def run() -> int:
    configure_console_encoding()
    args = parse_args()
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    logger = configure_logging(config.log_path)
    logger.info("招生公告监控启动")

    if args.test_email:
        if not config.email.enabled:
            logger.error("邮件未启用；请先把 config.json 中 email.enabled 改为 true。")
            return 2
        try:
            send_test_email(config.email)
        except EmailError as exc:
            logger.error("测试邮件发送失败：%s", exc)
            return 1
        logger.info("测试邮件发送成功，请检查收件箱和垃圾邮件文件夹。")
        return 0

    had_error = False
    session = build_session(config.request)
    try:
        with RadarDatabase(config.database_path) as database:
            database.initialize()

            for website in config.websites:
                database.upsert_website(website)
                database.mark_check_started(website.id)
                logger.info("开始检查：%s <%s>", website.name, website.url)

                try:
                    notices = fetch_notices(session, website, config.request)
                    logger.info("成功提取 %d 条公告。", len(notices))
                    result = database.store_scan(website.id, notices)
                except (FetchError, OSError, ValueError) as exc:
                    had_error = True
                    database.mark_check_failed(website.id, str(exc))
                    logger.error("检查失败：%s；%s", website.name, exc)
                    continue
                except Exception as exc:  # 保留完整日志，单个网站失败不影响其他网站。
                    had_error = True
                    database.mark_check_failed(website.id, str(exc))
                    logger.exception("检查时发生未预期错误：%s", website.name)
                    continue

                if result.is_baseline:
                    logger.info(
                        "首次运行：已为“%s”建立 %d 条公告基线，本次不发邮件。",
                        website.name,
                        len(result.inserted),
                    )
                    continue

                if result.inserted:
                    logger.info(
                        "发现 %d 条数据库中未见过的公告。",
                        len(result.inserted),
                    )
                    for notice in result.inserted:
                        logger.info("新增公告：%s | %s", notice.title, notice.url)
                else:
                    logger.info("暂无新公告：%s", website.name)

                # 邮件失败时不标记已通知，下次运行会自动重试。
                pending = database.get_pending_notices(website.id)
                if not pending:
                    continue
                if not config.email.enabled:
                    logger.warning(
                        "有 %d 条公告待通知，但邮件功能尚未启用。",
                        len(pending),
                    )
                    continue

                try:
                    send_notices(config.email, website.name, pending)
                    database.mark_notified([notice.id for notice in pending])
                    logger.info("已发送邮件，包含 %d 条新公告。", len(pending))
                except EmailError as exc:
                    had_error = True
                    logger.error(
                        "邮件发送失败，将在下次运行时重试：%s",
                        exc,
                    )
    finally:
        session.close()

    logger.info("招生公告监控结束%s", "（存在错误）" if had_error else "")
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(run())
