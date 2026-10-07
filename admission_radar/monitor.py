from __future__ import annotations

from dataclasses import replace
from .config import AppConfig, resolve_website_recipients
from .database import RadarDatabase
from .fetcher import build_session, fetch_notices
from .mailer import EmailError, send_notices
from .state import preflight
from .sender import require_sender


def scan(config: AppConfig, logger) -> tuple[int, dict]:
    require_sender(config)
    recipients = {w.id: resolve_website_recipients(w, config.email.to_addresses)
                  for w in config.websites} if config.email.enabled else {}
    if config.require_existing_database:
        preflight(config.database_path, tuple(w.id for w in config.websites))
    had_error = False
    report: dict = {}
    session = build_session(config.request, config.websites)
    try:
        with RadarDatabase(config.database_path) as database:
            database.initialize()
            for website in config.websites:
                database.upsert_website(website)
                database.mark_check_started(website.id)
                report[website.id] = {"fetch_success": False, "accepted_batches": 0, "email_errors": []}
                logger.info("开始检查：%s", website.name)
                try:
                    notices = fetch_notices(session, website, config.request)
                    result = database.store_scan(website.id, notices)
                    report[website.id].update(fetch_success=True, extracted=len(notices), new=len(result.inserted), baseline=result.is_baseline)
                    logger.info("%s：提取 %d 条，新增 %d 条，基线=%s", website.id, len(notices), len(result.inserted), result.is_baseline)
                except Exception as exc:
                    had_error = True
                    error = type(exc).__name__
                    database.mark_check_failed(website.id, error)
                    report[website.id]["fetch_error"] = error
                    logger.error("%s 抓取失败，类型=%s；仍处理已保存的 pending", website.id, error)

                if not config.email.enabled:
                    continue
                if not config.track_recipient_deliveries:
                    # Public GitHub state must not acquire private email addresses.
                    pending = database.get_pending_notices(website.id)
                    if pending:
                        require_sender(config)
                        try:
                            send_notices(replace(config.email, to_addresses=recipients[website.id]), website.name, pending)
                            database.mark_notified([n.id for n in pending])
                            report[website.id]["accepted_batches"] += 1
                        except EmailError as exc:
                            had_error = True
                            report[website.id]["email_errors"].append(exc.error_code)
                            logger.error("%s 邮件失败：%s", website.id, exc.error_code)
                    continue
                database.ensure_deliveries(website.id, recipients[website.id])
                for recipient, pending in database.pending_delivery_groups(website.id).items():
                    require_sender(config)
                    ids = [n.id for n in pending]
                    database.start_delivery(recipient, ids)
                    try:
                        send_notices(replace(config.email, to_addresses=(recipient,)), website.name, pending)
                    except EmailError as exc:
                        had_error = True
                        database.fail_delivery(recipient, ids, exc.error_code, uncertain=exc.uncertain)
                        report[website.id]["email_errors"].append(exc.error_code)
                        logger.error("%s 邮件失败：%s；不确定=%s；等待后续重试", website.id, exc.error_code, exc.uncertain)
                    except Exception as exc:
                        had_error = True
                        # Unknown exception may occur after submission: preserve uncertainty.
                        database.fail_delivery(recipient, ids, type(exc).__name__, uncertain=True)
                        report[website.id]["email_errors"].append(type(exc).__name__)
                        logger.error("%s 邮件异常类型=%s；保留不确定状态", website.id, type(exc).__name__)
                    else:
                        database.accept_delivery(recipient, ids)
                        report[website.id]["accepted_batches"] += 1
                        logger.info("%s：SMTP 接受一个收件人批次，含 %d 条公告", website.id, len(ids))
    finally:
        session.close()
    return (1 if had_error else 0), report
