import os
import re
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, send_from_directory, url_for

import database
import polling
from ricoh_config import DEFAULT_REFRESH_SECONDS, SIGNUP_EMAIL_DOMAIN, SQLITE_DB


IMAGE_FILES = {
    'c3504ex': 'c3504ex.png',
    'c6004ex': 'c6004ex.png',
    'c6503': 'c6503.png',
    'c6503f': 'c6503f.png',
    'c4500': 'c4500.png',
    'c6010': 'c6010.png',
    'c3510': 'c3510.png',
    'mfp5800': 'mfp5800.png',
    'mfpm681': 'mfpm681.png',
    'no_connection': 'NoConnection.png',
    'missing_model': 'missing.png',
}


def validate_signup_email(email):
    normalized = email.strip().lower()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', normalized):
        return None, 'Enter a valid email address.'
    if not normalized.endswith('@' + SIGNUP_EMAIL_DOMAIN):
        return None, 'Use an @' + SIGNUP_EMAIL_DOMAIN + ' address.'
    return normalized, None


def create_app(db_path=None, start_scheduler=True):
    app = Flask(__name__)
    app.config['DB_PATH'] = database.get_db_path(db_path)
    app.config['SECRET_KEY'] = os.environ.get('RICOH_SECRET_KEY', 'ricoh-monitor-internal')
    app.config['SCHEDULER'] = None
    app.config['SCHEDULER_ERROR'] = None

    database.seed_printers_if_empty(app.config['DB_PATH'])

    @app.route('/')
    def index():
        return render_template(
            'index.html',
            latest=database.get_latest_statuses(app.config['DB_PATH']),
            health=database.get_health(app.config['DB_PATH']),
        )

    @app.route('/signup', methods=['POST'])
    def signup():
        email, error = validate_signup_email(request.form.get('email', ''))
        if error:
            flash(error, 'error')
        else:
            database.add_email_subscriber(email, app.config['DB_PATH'])
            flash(email + ' is subscribed to printer alerts.', 'success')
        return redirect(url_for('index'))

    @app.route('/api/status')
    def api_status():
        return jsonify({
            'health': database.get_health(app.config['DB_PATH']),
            'statuses': database.get_latest_statuses(app.config['DB_PATH']),
        })

    @app.route('/health')
    def health():
        scheduler = app.config.get('SCHEDULER')
        return jsonify({
            'database': database.get_health(app.config['DB_PATH']),
            'scheduler_running': bool(scheduler and scheduler.running),
            'scheduler_error': app.config.get('SCHEDULER_ERROR'),
        })

    @app.route('/image/<image_key>')
    def image_asset(image_key):
        filename = IMAGE_FILES.get(image_key, IMAGE_FILES['missing_model'])
        return send_from_directory('images', filename)

    if start_scheduler and not os.environ.get('RICOH_DISABLE_SCHEDULER'):
        start_background_scheduler(app)

    return app


def start_background_scheduler(app):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as err:
        app.config['SCHEDULER_ERROR'] = str(err)
        return None

    def scheduled_poll():
        with app.app_context():
            try:
                polling.poll_once(db_path=app.config['DB_PATH'])
                app.config['SCHEDULER_ERROR'] = None
            except Exception as err:
                app.config['SCHEDULER_ERROR'] = str(err)

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        scheduled_poll,
        'interval',
        seconds=DEFAULT_REFRESH_SECONDS,
        next_run_time=datetime.now(),
        id='printer_poll',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    app.config['SCHEDULER'] = scheduler
    return scheduler


if __name__ == '__main__':
    app = create_app(db_path=os.environ.get('RICOH_DB_PATH') or SQLITE_DB)
    app.run(
        host=os.environ.get('RICOH_HOST', '127.0.0.1'),
        port=int(os.environ.get('RICOH_PORT', '5000')),
        debug=False,
    )
