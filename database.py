import json
import os
import sqlite3
from datetime import datetime, timedelta

from printer_status import find_status_issues
from ricoh_config import (
    HISTORY_RETENTION_DAYS,
    PRINTERS_JSON,
    SQLITE_DB,
    default_printers,
    load_printers,
    normalize_printer,
)


def get_db_path(db_path=None):
    return db_path or os.environ.get('RICOH_DB_PATH') or SQLITE_DB


def connect(db_path=None):
    conn = sqlite3.connect(get_db_path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    conn = connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS printers (
                ip TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                serial TEXT NOT NULL DEFAULT '',
                eid TEXT NOT NULL DEFAULT '',
                default_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS latest_printer_status (
                printer_ip TEXT PRIMARY KEY,
                status_json TEXT NOT NULL,
                issues_json TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (printer_ip) REFERENCES printers(ip)
            );

            CREATE TABLE IF NOT EXISTS printer_status_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_ip TEXT NOT NULL,
                status_json TEXT NOT NULL,
                issues_json TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (printer_ip) REFERENCES printers(ip)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_checked_at
                ON printer_status_snapshots(checked_at);

            CREATE TABLE IF NOT EXISTS notification_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_subscribers (
                email TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_text(now=None):
    return (now or datetime.now()).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')


def import_printers(printers, db_path=None, now=None):
    init_db(db_path)
    conn = connect(db_path)
    timestamp = _now_text(now)
    try:
        for printer in printers:
            normalized = normalize_printer(printer)
            conn.execute(
                """
                INSERT INTO printers
                    (ip, name, serial, eid, default_enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ip) DO UPDATE SET
                    name = excluded.name,
                    serial = excluded.serial,
                    eid = excluded.eid,
                    default_enabled = excluded.default_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized['IP'],
                    normalized['Name'],
                    normalized['Serial'],
                    normalized['EID'],
                    1 if normalized['Default'] else 0,
                    timestamp,
                    timestamp,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def import_printers_from_path(path=PRINTERS_JSON, db_path=None):
    if path and os.path.exists(path):
        printers = load_printers(path)
    else:
        printers = default_printers()
    import_printers(printers, db_path)
    return printers


def seed_printers_if_empty(db_path=None, printers_path=PRINTERS_JSON):
    init_db(db_path)
    conn = connect(db_path)
    try:
        count = conn.execute('SELECT COUNT(*) AS count FROM printers').fetchone()['count']
    finally:
        conn.close()
    if count == 0:
        return import_printers_from_path(printers_path, db_path)
    return []


def get_printers(db_path=None, default_only=False):
    init_db(db_path)
    conn = connect(db_path)
    try:
        sql = 'SELECT * FROM printers'
        params = []
        if default_only:
            sql += ' WHERE default_enabled = 1'
        sql += ' ORDER BY name COLLATE NOCASE'
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            'IP': row['ip'],
            'Name': row['name'],
            'Serial': row['serial'],
            'EID': row['eid'],
            'Default': bool(row['default_enabled']),
        }
        for row in rows
    ]


def store_statuses(statuses, db_path=None, checked_at=None):
    init_db(db_path)
    timestamp = _now_text(checked_at)
    conn = connect(db_path)
    try:
        for status in statuses:
            printer = status['printer']
            issues = find_status_issues(status)
            status_json = json.dumps(status, sort_keys=True)
            issues_json = json.dumps(issues, sort_keys=True)
            conn.execute(
                """
                INSERT INTO latest_printer_status
                    (printer_ip, status_json, issues_json, checked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(printer_ip) DO UPDATE SET
                    status_json = excluded.status_json,
                    issues_json = excluded.issues_json,
                    checked_at = excluded.checked_at
                """,
                (printer['IP'], status_json, issues_json, timestamp),
            )
            conn.execute(
                """
                INSERT INTO printer_status_snapshots
                    (printer_ip, status_json, issues_json, checked_at)
                VALUES (?, ?, ?, ?)
                """,
                (printer['IP'], status_json, issues_json, timestamp),
            )
        conn.commit()
    finally:
        conn.close()


def prune_history(db_path=None, retention_days=HISTORY_RETENTION_DAYS, now=None):
    init_db(db_path)
    cutoff = (now or datetime.now()) - timedelta(days=retention_days)
    cutoff_text = _now_text(cutoff)
    conn = connect(db_path)
    try:
        conn.execute(
            'DELETE FROM printer_status_snapshots WHERE checked_at < ?',
            (cutoff_text,),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_statuses(db_path=None):
    init_db(db_path)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT p.*, l.status_json, l.issues_json, l.checked_at
            FROM printers p
            LEFT JOIN latest_printer_status l ON l.printer_ip = p.ip
            ORDER BY p.name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    latest = []
    for row in rows:
        printer = {
            'IP': row['ip'],
            'Name': row['name'],
            'Serial': row['serial'],
            'EID': row['eid'],
            'Default': bool(row['default_enabled']),
        }
        if row['status_json']:
            status = json.loads(row['status_json'])
            issues = json.loads(row['issues_json'])
            checked_at = row['checked_at']
        else:
            status = {
                'ok': False,
                'printer': printer,
                'ip': printer['IP'],
                'name': printer['Name'],
                'model': '',
                'image_key': 'missing_model',
                'alerts': [],
                'ink_levels': [],
                'tray_levels': [],
                'paper_deficit': 0,
                'paper_capacity': 0,
                'paper_percent': 0,
                'error': 'Not checked yet',
                'error_traceback': None,
            }
            issues = []
            checked_at = None
        latest.append({
            'printer': printer,
            'status': status,
            'issues': issues,
            'last_checked': checked_at,
        })
    return latest


def load_notification_state(db_path=None):
    init_db(db_path)
    conn = connect(db_path)
    try:
        row = conn.execute('SELECT state_json FROM notification_state WHERE id = 1').fetchone()
    finally:
        conn.close()
    if row is None:
        return {'active': {}}
    return json.loads(row['state_json'])


def save_notification_state(state, db_path=None, now=None):
    init_db(db_path)
    timestamp = _now_text(now)
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO notification_state (id, state_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (json.dumps(state, sort_keys=True), timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def add_email_subscriber(email, db_path=None, now=None):
    init_db(db_path)
    normalized = email.strip().lower()
    timestamp = _now_text(now)
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO email_subscribers (email, active, created_at, updated_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                active = 1,
                updated_at = excluded.updated_at
            """,
            (normalized, timestamp, timestamp),
        )
        conn.commit()
    finally:
        conn.close()
    return normalized


def get_active_subscribers(db_path=None):
    init_db(db_path)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            'SELECT email FROM email_subscribers WHERE active = 1 ORDER BY email'
        ).fetchall()
    finally:
        conn.close()
    return [row['email'] for row in rows]


def get_health(db_path=None):
    init_db(db_path)
    conn = connect(db_path)
    try:
        latest = conn.execute(
            'SELECT MAX(checked_at) AS last_checked, COUNT(*) AS count FROM latest_printer_status'
        ).fetchone()
        printers = conn.execute('SELECT COUNT(*) AS count FROM printers').fetchone()
        subscribers = conn.execute(
            'SELECT COUNT(*) AS count FROM email_subscribers WHERE active = 1'
        ).fetchone()
    finally:
        conn.close()
    return {
        'database': 'ok',
        'printer_count': printers['count'],
        'latest_status_count': latest['count'],
        'last_checked': latest['last_checked'],
        'active_subscriber_count': subscribers['count'],
    }
