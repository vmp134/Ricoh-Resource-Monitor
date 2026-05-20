import os
import tempfile
import unittest
from datetime import datetime

import database
from tests.test_database import sample_printer, sample_status
from web_app import create_app, validate_signup_email


class WebAppTests(unittest.TestCase):
    def test_signup_validation(self):
        self.assertEqual(validate_signup_email('USER@Rutgers.edu'), ('user@rutgers.edu', None))
        self.assertIsNotNone(validate_signup_email('user@example.com')[1])
        self.assertIsNotNone(validate_signup_email('not-an-email')[1])

    def test_dashboard_api_health_and_signup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'monitor.sqlite')
            database.import_printers([sample_printer()], db_path)
            database.store_statuses([sample_status()], db_path, checked_at=datetime(2026, 5, 20, 9, 0, 0))
            app = create_app(db_path=db_path, start_scheduler=False)
            app.config['TESTING'] = True
            client = app.test_client()

            index_response = client.get('/')
            signup_response = client.post('/signup', data={'email': 'USER@Rutgers.edu'})
            duplicate_response = client.post('/signup', data={'email': 'user@rutgers.edu'})
            api_response = client.get('/api/status')
            health_response = client.get('/health')
            subscribers = database.get_active_subscribers(db_path)

        self.assertEqual(index_response.status_code, 200)
        self.assertIn(b'Test Printer', index_response.data)
        self.assertEqual(signup_response.status_code, 302)
        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(subscribers, ['user@rutgers.edu'])
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.get_json()['statuses'][0]['status']['tray_levels'][0]['paper_size'], '8.5x11')
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.get_json()['database']['database'], 'ok')


if __name__ == '__main__':
    unittest.main()
