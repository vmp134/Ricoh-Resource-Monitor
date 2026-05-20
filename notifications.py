import json
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from ricoh_config import DEFAULT_THRESHOLDS, NOTIFICATION_CONFIG_JSON, NOTIFICATION_STATE_JSON


DEFAULT_NOTIFICATION_CONFIG = {
    'enabled': False,
    'dry_run': True,
    'recipients': [],
    'reminder_hours': DEFAULT_THRESHOLDS['reminder_hours'],
    'smtp': {
        'host': '',
        'port': 587,
        'username': '',
        'password': '',
        'from_email': '',
        'use_tls': True,
    },
}


def _parse_bool(value):
    return str(value).strip().lower() in ['1', 'true', 'yes', 'on']


def _merge_dict(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_notification_config(path=NOTIFICATION_CONFIG_JSON):
    config = DEFAULT_NOTIFICATION_CONFIG
    if os.path.exists(path):
        with open(path, 'r') as f:
            config = _merge_dict(config, json.load(f))
    else:
        config = dict(config)
        config['smtp'] = dict(config['smtp'])

    smtp = dict(config.get('smtp', {}))
    recipients_env = os.environ.get('RICOH_ALERT_RECIPIENTS')
    if recipients_env:
        config['recipients'] = [
            item.strip() for item in recipients_env.split(',') if item.strip()
        ]
    if 'RICOH_EMAIL_ENABLED' in os.environ:
        config['enabled'] = _parse_bool(os.environ['RICOH_EMAIL_ENABLED'])
    if 'RICOH_EMAIL_DRY_RUN' in os.environ:
        config['dry_run'] = _parse_bool(os.environ['RICOH_EMAIL_DRY_RUN'])
    if 'RICOH_SMTP_HOST' in os.environ:
        smtp['host'] = os.environ['RICOH_SMTP_HOST']
    if 'RICOH_SMTP_PORT' in os.environ:
        smtp['port'] = int(os.environ['RICOH_SMTP_PORT'])
    if 'RICOH_SMTP_USERNAME' in os.environ:
        smtp['username'] = os.environ['RICOH_SMTP_USERNAME']
    if 'RICOH_SMTP_PASSWORD' in os.environ:
        smtp['password'] = os.environ['RICOH_SMTP_PASSWORD']
    if 'RICOH_SMTP_FROM' in os.environ:
        smtp['from_email'] = os.environ['RICOH_SMTP_FROM']
    if 'RICOH_SMTP_USE_TLS' in os.environ:
        smtp['use_tls'] = _parse_bool(os.environ['RICOH_SMTP_USE_TLS'])
    config['smtp'] = smtp
    return config


def load_notification_state(path=NOTIFICATION_STATE_JSON):
    if not os.path.exists(path):
        return {'active': {}}
    with open(path, 'r') as f:
        return json.load(f)


def save_notification_state(state, path=NOTIFICATION_STATE_JSON):
    with open(path, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


def _parse_time(value):
    if not value:
        return None
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S')


def _format_time(value):
    return value.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')


def evaluate_notification_events(statuses, state, now=None, reminder_hours=None):
    from printer_status import find_status_issues

    if now is None:
        now = datetime.now()
    if reminder_hours is None:
        reminder_hours = DEFAULT_THRESHOLDS['reminder_hours']

    active = dict(state.get('active', {}))
    next_active = dict(active)
    events = []
    current_keys = set()
    reminder_delta = timedelta(hours=reminder_hours)

    for status in statuses:
        for issue in find_status_issues(status):
            key = issue['key']
            current_keys.add(key)
            previous = active.get(key)
            event_type = None

            if previous is None:
                event_type = 'new'
            elif previous.get('fingerprint') != issue.get('fingerprint'):
                event_type = 'changed'
            else:
                last_sent = _parse_time(previous.get('last_sent'))
                if last_sent is None or now - last_sent >= reminder_delta:
                    event_type = 'reminder'

            if event_type:
                events.append({
                    'event_type': event_type,
                    'issue': issue,
                })

            existing = previous or {}
            next_active[key] = {
                'fingerprint': issue.get('fingerprint'),
                'first_seen': existing.get('first_seen') or _format_time(now),
                'last_sent': _format_time(now) if event_type else existing.get('last_sent'),
                'summary': issue.get('summary'),
                'printer_name': issue.get('printer_name'),
                'printer_ip': issue.get('printer_ip'),
            }

    for key, previous in active.items():
        if key in current_keys:
            continue
        events.append({
            'event_type': 'resolved',
            'issue': {
                'key': key,
                'printer_key': previous.get('printer_ip', ''),
                'printer_name': previous.get('printer_name', ''),
                'printer_ip': previous.get('printer_ip', ''),
                'type': 'resolved',
                'severity': 'info',
                'summary': 'Resolved: ' + previous.get('summary', key),
                'fingerprint': '',
            },
        })
        if key in next_active:
            del next_active[key]

    return events, {'active': next_active}


def group_events_by_printer(events):
    groups = {}
    for event in events:
        issue = event['issue']
        key = issue.get('printer_key') or issue.get('printer_ip') or 'unknown'
        groups.setdefault(key, []).append(event)
    return groups


def build_email_message(events, config, now=None):
    if now is None:
        now = datetime.now()
    first_issue = events[0]['issue']
    printer_name = first_issue.get('printer_name') or 'Unknown printer'
    printer_ip = first_issue.get('printer_ip') or 'unknown IP'
    severities = [event['issue'].get('severity') for event in events]
    severity = 'critical' if 'critical' in severities else 'warning'

    subject = 'Ricoh Monitor: ' + printer_name + ' low resources'
    if all(event['event_type'] == 'resolved' for event in events):
        subject = 'Ricoh Monitor: ' + printer_name + ' resolved'
        severity = 'info'

    lines = [
        'Ricoh Resource Monitor alert',
        '',
        'Printer: ' + printer_name,
        'IP: ' + printer_ip,
        'Severity: ' + severity,
        'Time: ' + now.strftime('%Y-%m-%d %H:%M:%S'),
        '',
        'Events:',
    ]
    for event in events:
        lines.append('- ' + event['event_type'] + ': ' + event['issue']['summary'])

    body = '\n'.join(lines) + '\n'
    msg = MIMEText(body)
    smtp = config.get('smtp', {})
    msg['Subject'] = subject
    msg['From'] = smtp.get('from_email') or smtp.get('username') or 'ricoh-monitor@example.invalid'
    msg['To'] = ', '.join(config.get('recipients', []))
    return msg


def send_email(message, config):
    smtp_config = config.get('smtp', {})
    host = smtp_config.get('host')
    port = int(smtp_config.get('port') or 587)
    username = smtp_config.get('username')
    password = smtp_config.get('password')

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if smtp_config.get('use_tls', True):
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def deliver_notification_events(events, config, now=None):
    if not events:
        return []

    dry_run = config.get('dry_run', False)
    enabled = config.get('enabled', False)
    results = []
    for printer_key, group in group_events_by_printer(events).items():
        message = build_email_message(group, config, now)
        if dry_run or not enabled:
            results.append({
                'printer_key': printer_key,
                'sent': False,
                'dry_run': True,
                'subject': message['Subject'],
                'body': message.get_payload(),
            })
        else:
            try:
                send_email(message, config)
                results.append({
                    'printer_key': printer_key,
                    'sent': True,
                    'dry_run': False,
                    'subject': message['Subject'],
                })
            except Exception as err:
                results.append({
                    'printer_key': printer_key,
                    'sent': False,
                    'dry_run': False,
                    'subject': message['Subject'],
                    'error': str(err),
                })
    return results
