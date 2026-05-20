import json
from datetime import datetime

import database
from notifications import (
    deliver_notification_events,
    evaluate_notification_events,
    load_notification_config,
)
from printer_status import find_status_issues, get_printer_status
from ricoh_config import HISTORY_RETENTION_DAYS


def merged_recipients(config, subscribers):
    recipients = []
    seen = set()
    for address in list(config.get('recipients', [])) + list(subscribers):
        normalized = address.strip().lower()
        if normalized and normalized not in seen:
            recipients.append(normalized)
            seen.add(normalized)
    return recipients


def poll_once(db_path=None, dry_run=False, now=None):
    now = now or datetime.now()
    database.seed_printers_if_empty(db_path)
    printers = database.get_printers(db_path)

    statuses = [get_printer_status(printer) for printer in printers]
    database.store_statuses(statuses, db_path=db_path, checked_at=now)
    database.prune_history(db_path=db_path, retention_days=HISTORY_RETENTION_DAYS, now=now)

    config = load_notification_config()
    if dry_run:
        config['dry_run'] = True
    config['recipients'] = merged_recipients(
        config,
        database.get_active_subscribers(db_path),
    )

    state = database.load_notification_state(db_path)
    events, next_state = evaluate_notification_events(
        statuses,
        state,
        now=now,
        reminder_hours=int(config.get('reminder_hours') or 24),
    )
    delivery_results = deliver_notification_events(events, config, now=now)
    failed_deliveries = [
        result for result in delivery_results
        if not result.get('dry_run') and not result.get('sent') and result.get('error')
    ]
    if not dry_run and not config.get('dry_run', False) and not failed_deliveries:
        database.save_notification_state(next_state, db_path, now=now)

    return {
        'checked_at': now.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S'),
        'printers_checked': len(statuses),
        'statuses': statuses,
        'issues': [
            issue
            for status in statuses
            for issue in find_status_issues(status)
        ],
        'notification_events': events,
        'delivery_results': delivery_results,
    }


def poll_once_json(db_path=None, dry_run=False):
    return json.dumps(poll_once(db_path=db_path, dry_run=dry_run), indent=2, sort_keys=True)
