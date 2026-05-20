# Ricoh Resource Monitor

Internal web dashboard for monitoring Ricoh and HP printer resources over SNMP.
The Flask app polls printers from a central machine, stores cached status in
SQLite, renders a read-only dashboard, and sends throttled SMTP notifications
for low resources.

## Technology

- Python
- Flask
- SQLite
- APScheduler
- waitress
- puresnmp

## Run Locally

Install dependencies:

```sh
python -m pip install -r requirements.txt
```

Initialize the database and import printer defaults:

```sh
python manage.py init-db
```

Run one polling pass:

```sh
python manage.py poll-once --dry-run
```

Start the web app:

```sh
python web_app.py
```

For a Windows production-style process, use waitress:

```sh
waitress-serve --call web_app:create_app
```

The app listens on `127.0.0.1:5000` by default. Set `RICOH_HOST`,
`RICOH_PORT`, or `RICOH_DB_PATH` to override local deployment settings.

## Management Commands

```sh
python manage.py init-db
python manage.py import-printers printers.json
python manage.py poll-once
python manage.py test-email user@rutgers.edu
```

The web dashboard is read-only for printer data. Printer changes are imported
from JSON through `manage.py import-printers`.

## Email Alerts

Use `notification_config.example.json` as the template for
`notification_config.json`, or configure SMTP with environment variables:

|Variable|Purpose|
|-|-|
|`RICOH_EMAIL_ENABLED`|Set to `true` to send real email|
|`RICOH_EMAIL_DRY_RUN`|Set to `true` to print/log emails without sending|
|`RICOH_ALERT_RECIPIENTS`|Comma-separated recipient list|
|`RICOH_SMTP_HOST`|SMTP host|
|`RICOH_SMTP_PORT`|SMTP port|
|`RICOH_SMTP_USERNAME`|SMTP username|
|`RICOH_SMTP_PASSWORD`|SMTP password|
|`RICOH_SMTP_FROM`|Sender email address|
|`RICOH_SMTP_USE_TLS`|Set to `true` for STARTTLS|

Dashboard signup auto-subscribes valid `@rutgers.edu` addresses. Notification
recipients are the configured recipient list plus active email subscribers.

## Polling And Storage

The Flask process starts an APScheduler job that polls every 600 seconds. Page
loads read cached SQLite data only; browser requests do not perform SNMP calls.

SQLite tables store configured printers, latest status, 30 days of status
history, notification throttle state, email subscribers, and app settings.

## SNMP OIDs

|OID|Value|Method|
|-|-|-|
|Printer Model|`.1.3.6.1.2.1.43.5.1.1.16.1`|Get|
|Ink Levels|`.1.3.6.1.2.1.43.11.1.1.9.1`|Walk|
|Tray Names|`.1.3.6.1.2.1.43.8.2.1.13`|Walk|
|Current Tray Fill|`.1.3.6.1.2.1.43.8.2.1.10.1`|Walk|
|Max Tray Fill|`.1.3.6.1.2.1.43.8.2.1.9.1`|Walk|
|Tray Feed Dimension|`.1.3.6.1.2.1.43.8.2.1.4`|Walk|
|Tray XFeed Dimension|`.1.3.6.1.2.1.43.8.2.1.5`|Walk|
|Printer Errors|`.1.3.6.1.2.1.43.18.1.1.8.1`|Walk|

Tray dimensions are reported in 1/10000 inch units. The status layer normalizes
orientation and labels common sizes as `8.5x11`, `8.5x14`, and `11x17`.

## Tests

```sh
python -m unittest discover -s tests
```
