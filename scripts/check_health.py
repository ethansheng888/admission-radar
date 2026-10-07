from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from admission_radar.config import load_config, resolve_website_recipients
from admission_radar.mailer import send_health_email
from admission_radar.state import summary, atomic_json


def health_issues(config, backup_dir: Path, now: datetime) -> list[str]:
    issues: list[str] = []
    try:
        rows = summary(config.database_path)
        for website in config.websites:
            row = rows.get(website.id)
            if row is None:
                issues.append(f"{website.id}:missing-history")
                continue
            success = row['last_success_at']
            if not success or now - datetime.fromisoformat(success) > timedelta(hours=3):
                issues.append(f"{website.id}:fetch-stale")
            pending = row['oldest_pending_at']
            if pending and now - datetime.fromisoformat(pending) > timedelta(hours=3):
                issues.append(f"{website.id}:pending-stale")
    except Exception:
        issues.append("database:unavailable")
    try:
        run = json.loads(config.status_path.read_text())
        if run.get('consecutive_failures', 0) >= 3:
            issues.append("scan:consecutive-failures")
        end = run.get('ended_at') or run.get('started_at')
        if not end or now - datetime.fromisoformat(end) > timedelta(hours=3):
            issues.append("scan:stale")
    except Exception:
        issues.append("scan:status-unavailable")
    try:
        report = json.loads((backup_dir / 'last-backup.json').read_text())
        target = backup_dir / Path(report['filename']).name
        if not target.is_file() or now - datetime.fromisoformat(report['completed_at']) > timedelta(hours=28):
            issues.append("backup:stale")
    except Exception:
        issues.append("backup:unavailable")
    return sorted(issues)


def check(config, backup_dir: Path, *, alert: bool) -> int:
    now = datetime.now(timezone.utc)
    issues = health_issues(config, backup_dir, now)
    state_path = config.database_path.parent / 'health.json'
    try: previous = json.loads(state_path.read_text())
    except (OSError, ValueError): previous = {}
    # Only changes notify; failed submission does not advance notified_issues.
    notified = previous.get('notified_issues', [])
    if alert and issues != notified:
        owner = next((w for w in config.websites if w.id == 'cufe-master'), None)
        if owner is None: raise ValueError('missing owner group')
        for address in resolve_website_recipients(owner, config.email.to_addresses):
            send_health_email(replace(config.email, to_addresses=(address,)), issues)
        notified = issues
    atomic_json(state_path, dict(checked_at=now.isoformat(), issues=issues,
                                notified_issues=notified))
    print(json.dumps(dict(healthy=not issues, issues=issues), ensure_ascii=False))
    return 1 if issues else 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--backup-dir', type=Path, required=True)
    p.add_argument('--alert', action='store_true')
    args = p.parse_args()
    try:
        config = load_config(args.config, network_only=not args.alert)
        raise SystemExit(check(config, args.backup_dir, alert=args.alert))
    except Exception as exc:
        print('健康检查失败，类型=' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
