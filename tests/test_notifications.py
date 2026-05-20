import unittest
from datetime import datetime, timedelta

from notifications import evaluate_notification_events


def make_status(ink_level=10, tray_deficit=0, ok=True):
    return {
        'ok': ok,
        'printer': {
            'IP': '10.0.0.5',
            'Name': 'Test Printer',
            'Serial': '',
            'EID': '',
            'Default': True,
        },
        'error': None if ok else 'timeout',
        'ink_levels': [{'name': 'Black', 'level': ink_level}],
        'tray_levels': [{
            'name': 'Tray 1',
            'current': 550 - tray_deficit,
            'max': 550,
            'deficit': tray_deficit,
            'is_bypass': False,
        }],
        'alerts': [],
    }


class NotificationTests(unittest.TestCase):
    def test_first_alert_sends(self):
        events, state = evaluate_notification_events(
            [make_status()],
            {'active': {}},
            now=datetime(2026, 5, 20, 9, 0, 0),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'new')
        self.assertEqual(len(state['active']), 1)

    def test_repeated_unchanged_alert_suppresses(self):
        now = datetime(2026, 5, 20, 9, 0, 0)
        events, state = evaluate_notification_events([make_status()], {'active': {}}, now=now)
        self.assertEqual(len(events), 1)

        events, state = evaluate_notification_events(
            [make_status()],
            state,
            now=now + timedelta(hours=1),
        )

        self.assertEqual(events, [])

    def test_changed_condition_sends(self):
        now = datetime(2026, 5, 20, 9, 0, 0)
        events, state = evaluate_notification_events([make_status(ink_level=10)], {'active': {}}, now=now)
        self.assertEqual(len(events), 1)

        events, state = evaluate_notification_events(
            [make_status(ink_level=8)],
            state,
            now=now + timedelta(hours=1),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'changed')

    def test_reminder_sends_after_interval(self):
        now = datetime(2026, 5, 20, 9, 0, 0)
        events, state = evaluate_notification_events([make_status()], {'active': {}}, now=now)
        self.assertEqual(len(events), 1)

        events, state = evaluate_notification_events(
            [make_status()],
            state,
            now=now + timedelta(hours=24),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'reminder')

    def test_resolved_condition_sends_once(self):
        now = datetime(2026, 5, 20, 9, 0, 0)
        events, state = evaluate_notification_events([make_status()], {'active': {}}, now=now)
        self.assertEqual(len(events), 1)

        events, state = evaluate_notification_events(
            [make_status(ink_level=80)],
            state,
            now=now + timedelta(hours=1),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'resolved')
        self.assertEqual(state['active'], {})


if __name__ == '__main__':
    unittest.main()
