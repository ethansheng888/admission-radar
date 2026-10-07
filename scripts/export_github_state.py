"""Export public, legacy-compatible state; refuse ambiguous delivery progress."""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def export(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or source == destination:
        raise ValueError('需要已有源数据库和不同的导出路径。')
    if destination.exists():
        raise ValueError('导出文件已存在；使用新的快照文件名，避免覆盖原状态。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.public-state-', suffix='.db', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as original, \
                closing(sqlite3.connect(':memory:')) as snapshot:
            original.backup(snapshot)  # Committed WAL data is part of the consistent snapshot.
            if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or snapshot.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('源数据库完整性检查失败。')
            if snapshot.execute('PRAGMA user_version').fetchone()[0] > 1:
                raise ValueError('源数据库版本较新，拒绝猜测其投递语义。')
            tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'websites','notices'} <= tables:
                raise ValueError('源数据库缺少公告历史表。')
            if 'notice_deliveries' in tables:
                uncertain = snapshot.execute("SELECT COUNT(*) FROM notice_deliveries WHERE status='uncertain'").fetchone()[0]
                partial = snapshot.execute("""SELECT notice_id FROM notice_deliveries GROUP BY notice_id
                    HAVING SUM(status='accepted')>0 AND SUM(status!='accepted')>0 LIMIT 1""").fetchone()
                inconsistent = snapshot.execute("""SELECT 1 FROM notice_deliveries d JOIN notices n ON n.id=d.notice_id
                    WHERE n.notified_at IS NOT NULL AND d.status!='accepted' LIMIT 1""").fetchone()
                unmarked = snapshot.execute("""SELECT d.notice_id FROM notice_deliveries d JOIN notices n ON n.id=d.notice_id
                    WHERE n.notified_at IS NULL GROUP BY d.notice_id HAVING SUM(d.status!='accepted')=0 LIMIT 1""").fetchone()
                if uncertain or partial or inconsistent or unmarked:
                    raise ValueError('存在不确定、部分成功或不一致投递；需先处理，拒绝导出为旧式群发状态。')
            with closing(sqlite3.connect(temporary)) as target:
                target.execute('PRAGMA foreign_keys=ON')
                for table in ('websites','notices'):
                    definition = snapshot.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
                    target.execute(definition)
                    rows=snapshot.execute('SELECT * FROM '+table).fetchall()
                    width=len(snapshot.execute('PRAGMA table_info('+table+')').fetchall())
                    target.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in range(width))+')', rows)
                for (sql,) in snapshot.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name IN ('websites','notices') AND sql IS NOT NULL"):
                    target.execute(sql)
                if 'sqlite_sequence' in tables:
                    sequence=snapshot.execute("SELECT seq FROM sqlite_sequence WHERE name='notices'").fetchone()
                    if sequence:
                        target.execute("DELETE FROM sqlite_sequence WHERE name='notices'")
                        target.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('notices',?)",sequence)
                target.execute('PRAGMA user_version=0')
                target.commit()
                if target.execute('PRAGMA quick_check').fetchone()[0]!='ok' or target.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('导出校验失败。')
                for table in ('websites','notices'):
                    if target.execute('SELECT * FROM '+table+' ORDER BY id').fetchall() != snapshot.execute('SELECT * FROM '+table+' ORDER BY id').fetchall():
                        raise ValueError('导出未保留原公告字段。')
        # Linking without overwrite is atomic even if a competing export chose the same name.
        temporary.chmod(0o600)
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='导出不含逐人邮箱的 GitHub 接管快照；不发送邮件。')
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    try:
        export(args.source,args.destination)
        print('兼容状态导出成功；尚未上传或切换发送端。')
    except Exception as exc:
        print('兼容状态导出失败：'+str(exc) if isinstance(exc,ValueError) else '兼容状态导出失败，类型='+type(exc).__name__)
        raise SystemExit(1)
