import unittest

from printer_status import SnmpClient, find_status_issues, get_printer_status, parse_paper_size
from ricoh_config import (
    ERROR_BASE_OID,
    INK_LEVELS_BASE_OID,
    MODEL_OID,
    TRAY_CURRENT_CAPACITY_BASE_OID,
    TRAY_FEED_DIM_OID,
    TRAY_MAX_CAPACITY_BASE_OID,
    TRAY_NAMES_BASE_OID,
    TRAY_XFEED_DIM_OID,
)


class FakeSnmp(object):
    def __init__(self, model=b'MP C6004ex', alerts=None, ink=None,
                 tray_names=None, tray_current=None, tray_max=None,
                 tray_feeds=None, tray_xfeeds=None, error=None):
        self.model = model
        self.alerts = alerts or []
        self.ink = ink or [80, 0, 75, 70, 65]
        self.tray_names = tray_names or [b'Paper Tray 1', b'Bypass Tray']
        self.tray_current = tray_current or [500, 10]
        self.tray_max = tray_max or [550, 100]
        self.tray_feeds = tray_feeds or [110000, 110000]
        self.tray_xfeeds = tray_xfeeds or [85000, 85000]
        self.error = error

    def get(self, ip, community, oid):
        if self.error:
            raise self.error
        if oid == MODEL_OID:
            return self.model
        raise AssertionError('unexpected get oid ' + oid)

    def walk(self, ip, community, oid):
        if self.error:
            raise self.error
        if oid == ERROR_BASE_OID:
            return [('oid', item) for item in self.alerts]
        if oid == INK_LEVELS_BASE_OID:
            return [('oid', item) for item in self.ink]
        if oid == TRAY_NAMES_BASE_OID:
            return [('oid', item) for item in self.tray_names]
        if oid == TRAY_CURRENT_CAPACITY_BASE_OID:
            return [('oid', item) for item in self.tray_current]
        if oid == TRAY_MAX_CAPACITY_BASE_OID:
            return [('oid', item) for item in self.tray_max]
        if oid == TRAY_FEED_DIM_OID:
            return [('oid', item) for item in self.tray_feeds]
        if oid == TRAY_XFEED_DIM_OID:
            return [('oid', item) for item in self.tray_xfeeds]
        raise AssertionError('unexpected walk oid ' + oid)


class PrinterStatusTests(unittest.TestCase):
    def setUp(self):
        self.printer = {
            'IP': '10.0.0.5',
            'Name': 'Test Printer',
            'Serial': 'serial',
            'EID': 'eid',
            'Default': True,
        }

    def status_for(self, fake):
        return get_printer_status(
            self.printer,
            SnmpClient(get_func=fake.get, walk_func=fake.walk),
        )

    def test_normal_printer_status(self):
        status = self.status_for(FakeSnmp())

        self.assertTrue(status['ok'])
        self.assertEqual(status['model'], 'MP C6004ex')
        self.assertEqual(status['image_key'], 'c6004ex')
        self.assertEqual(len(status['ink_levels']), 4)
        self.assertEqual(status['paper_deficit'], 50)
        self.assertEqual(status['paper_capacity'], 550)
        self.assertEqual(status['tray_levels'][0]['paper_size'], '8.5x11')
        self.assertEqual(find_status_issues(status), [])

    def test_low_ink_creates_issue(self):
        status = self.status_for(FakeSnmp(ink=[15, 0, 75, 70, 65]))
        issues = find_status_issues(status)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['type'], 'ink_low')
        self.assertIn('Black toner is at 15%', issues[0]['summary'])

    def test_low_tray_ignores_bypass_tray(self):
        status = self.status_for(FakeSnmp(
            tray_names=[b'Paper Tray 1', b'Bypass Tray'],
            tray_current=[0, 0],
            tray_max=[550, 100],
        ))
        issues = find_status_issues(status)

        self.assertEqual(status['paper_deficit'], 550)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['type'], 'tray_low')
        self.assertIn('Tray 1', issues[0]['summary'])

    def test_unreachable_printer_creates_critical_issue(self):
        status = self.status_for(FakeSnmp(error=RuntimeError('timeout')))
        issues = find_status_issues(status)

        self.assertFalse(status['ok'])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['type'], 'unreachable')
        self.assertEqual(issues[0]['severity'], 'critical')

    def test_c6503_with_lct_uses_lct_image(self):
        status = self.status_for(FakeSnmp(
            model=b'MP C6503',
            tray_names=[b'Paper Tray 1', b'Paper Tray 3 (LCT)'],
            tray_current=[500, 1000],
            tray_max=[550, 1500],
        ))

        self.assertEqual(status['image_key'], 'c6503f')

    def test_parse_paper_size_standard_and_custom_sizes(self):
        self.assertEqual(parse_paper_size(85000, 110000), '8.5x11')
        self.assertEqual(parse_paper_size(110000, 85000), '8.5x11')
        self.assertEqual(parse_paper_size(85000, 140000), '8.5x14')
        self.assertEqual(parse_paper_size(110000, 170000), '11x17')
        self.assertEqual(parse_paper_size(90000, 120000), '9.0x12.0')


if __name__ == '__main__':
    unittest.main()
