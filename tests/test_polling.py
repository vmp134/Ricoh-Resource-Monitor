import os
import tempfile
import unittest

import database
import polling
from tests.test_database import sample_printer, sample_status


class PollingTests(unittest.TestCase):
    def test_poll_once_writes_latest_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'monitor.sqlite')
            database.import_printers([sample_printer()], db_path)
            original = polling.get_printer_status
            try:
                polling.get_printer_status = lambda printer: sample_status(printer)
                result = polling.poll_once(db_path=db_path, dry_run=True)
            finally:
                polling.get_printer_status = original

            latest = database.get_latest_statuses(db_path)

        self.assertEqual(result['printers_checked'], 1)
        self.assertEqual(latest[0]['status']['tray_levels'][0]['paper_size'], '8.5x11')


if __name__ == '__main__':
    unittest.main()
