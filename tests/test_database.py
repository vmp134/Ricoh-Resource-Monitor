import os
import tempfile
import unittest
from datetime import datetime, timedelta

import database
from polling import merged_recipients


def sample_printer():
    return {
        'IP': '10.0.0.5',
        'Name': 'Test Printer',
        'Serial': '',
        'EID': '',
        'Default': True,
    }


def sample_status(printer=None):
    printer = printer or sample_printer()
    return {
        'ok': True,
        'printer': printer,
        'ip': printer['IP'],
        'name': printer['Name'],
        'model': 'MP C6004ex',
        'image_key': 'c6004ex',
        'alerts': [],
        'ink_levels': [{'name': 'Black', 'level': 80}],
        'tray_levels': [{
            'name': 'Tray 1',
            'paper_size': '8.5x11',
            'current': 500,
            'max': 550,
            'deficit': 50,
            'percent': 90,
            'is_bypass': False,
            'is_low': False,
        }],
        'paper_deficit': 50,
        'paper_capacity': 550,
        'paper_percent': 90,
        'error': None,
        'error_traceback': None,
    }


class DatabaseTests(unittest.TestCase):
    def test_init_import_and_latest_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'monitor.sqlite')
            database.init_db(db_path)
            database.import_printers([sample_printer()], db_path)
            database.store_statuses([sample_status()], db_path, checked_at=datetime(2026, 5, 20, 9, 0, 0))

            latest = database.get_latest_statuses(db_path)

        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]['status']['tray_levels'][0]['paper_size'], '8.5x11')
        self.assertEqual(latest[0]['last_checked'], '2026-05-20T09:00:00')

    def test_prune_history_removes_old_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'monitor.sqlite')
            database.import_printers([sample_printer()], db_path)
            now = datetime(2026, 5, 20, 9, 0, 0)
            database.store_statuses([sample_status()], db_path, checked_at=now - timedelta(days=31))
            database.store_statuses([sample_status()], db_path, checked_at=now)
            database.prune_history(db_path, retention_days=30, now=now)

            conn = database.connect(db_path)
            try:
                count = conn.execute('SELECT COUNT(*) AS count FROM printer_status_snapshots').fetchone()['count']
            finally:
                conn.close()

        self.assertEqual(count, 1)

    def test_email_subscriber_and_recipient_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'monitor.sqlite')
            database.add_email_subscriber('User@Rutgers.edu', db_path)
            database.add_email_subscriber('user@rutgers.edu', db_path)

            subscribers = database.get_active_subscribers(db_path)
            recipients = merged_recipients({'recipients': ['helpdesk@rutgers.edu']}, subscribers)

        self.assertEqual(subscribers, ['user@rutgers.edu'])
        self.assertEqual(recipients, ['helpdesk@rutgers.edu', 'user@rutgers.edu'])


if __name__ == '__main__':
    unittest.main()
