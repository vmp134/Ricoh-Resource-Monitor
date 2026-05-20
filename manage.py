import argparse
import json
import sys
from datetime import datetime

import database
import polling
from notifications import build_email_message, load_notification_config, send_email
from web_app import validate_signup_email


def cmd_init_db(args):
    database.init_db(args.db)
    imported = database.seed_printers_if_empty(args.db, args.printers)
    print('Database initialized.')
    if imported:
        print('Imported ' + str(len(imported)) + ' printers.')
    return 0


def cmd_import_printers(args):
    printers = database.import_printers_from_path(args.path, args.db)
    print('Imported ' + str(len(printers)) + ' printers.')
    return 0


def cmd_poll_once(args):
    result = polling.poll_once(db_path=args.db, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_test_email(args):
    email, error = validate_signup_email(args.email)
    if error:
        print(error, file=sys.stderr)
        return 2

    config = load_notification_config()
    config['recipients'] = [email]
    events = [{
        'event_type': 'test',
        'issue': {
            'key': 'test',
            'printer_key': 'test',
            'printer_name': 'Ricoh Monitor',
            'printer_ip': 'test',
            'type': 'test',
            'severity': 'info',
            'summary': 'SMTP test from Ricoh Resource Monitor.',
            'fingerprint': 'test',
        },
    }]
    message = build_email_message(events, config, now=datetime.now())
    if config.get('dry_run') or not config.get('enabled'):
        print('DRY RUN: ' + message['Subject'])
        print(message.get_payload())
    else:
        send_email(message, config)
        print('Sent test email to ' + email)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description='Ricoh Monitor management commands.')
    parser.add_argument('--db', default=None, help='SQLite database path.')
    subparsers = parser.add_subparsers(dest='command')

    init_db = subparsers.add_parser('init-db', help='Create tables and seed printers if needed.')
    init_db.add_argument('--printers', default='printers.json', help='Printer JSON import path.')
    init_db.set_defaults(func=cmd_init_db)

    import_printers = subparsers.add_parser('import-printers', help='Import printer JSON into SQLite.')
    import_printers.add_argument('path', help='Printer JSON file path.')
    import_printers.set_defaults(func=cmd_import_printers)

    poll_once = subparsers.add_parser('poll-once', help='Poll printers once and store results.')
    poll_once.add_argument('--dry-run', action='store_true', help='Do not send real email.')
    poll_once.set_defaults(func=cmd_poll_once)

    test_email = subparsers.add_parser('test-email', help='Send or dry-run an SMTP test email.')
    test_email.add_argument('email', help='Recipient @rutgers.edu address.')
    test_email.set_defaults(func=cmd_test_email)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
