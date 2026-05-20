import copy
import json
import os
import pickle
import sys


APP_VERSION = '4.0.0'
DEFAULT_REFRESH_SECONDS = 600
HISTORY_RETENTION_DAYS = 30
SIGNUP_EMAIL_DOMAIN = 'rutgers.edu'

PRINTERS_JSON = 'printers.json'
LEGACY_PRINTERS_PICKLE = 'Printers.pkl'
NOTIFICATION_CONFIG_JSON = 'notification_config.json'
NOTIFICATION_STATE_JSON = 'notification_state.json'
MONITOR_STATUS_JSON = 'monitor_status.json'
SQLITE_DB = 'ricoh_monitor.sqlite'

MODEL_OID = '.1.3.6.1.2.1.43.5.1.1.16.1'
INK_LEVELS_BASE_OID = '.1.3.6.1.2.1.43.11.1.1.9.1'
TRAY_NAMES_BASE_OID = '.1.3.6.1.2.1.43.8.2.1.13'
TRAY_MAX_CAPACITY_BASE_OID = '.1.3.6.1.2.1.43.8.2.1.9.1'
TRAY_CURRENT_CAPACITY_BASE_OID = '.1.3.6.1.2.1.43.8.2.1.10.1'
TRAY_FEED_DIM_OID = '.1.3.6.1.2.1.43.8.2.1.4'
TRAY_XFEED_DIM_OID = '.1.3.6.1.2.1.43.8.2.1.5'
ERROR_BASE_OID = '.1.3.6.1.2.1.43.18.1.1.8.1'

INK_SLOTS = [
    {'name': 'Black', 'style': 'black.Horizontal.TProgressbar'},
    None,  # Waste toner appears as the second SNMP value and is not displayed.
    {'name': 'Cyan', 'style': 'cyan.Horizontal.TProgressbar'},
    {'name': 'Magenta', 'style': 'magenta.Horizontal.TProgressbar'},
    {'name': 'Yellow', 'style': 'yellow.Horizontal.TProgressbar'},
]

MODEL_IMAGE_KEYS = {
    'MP C6004ex': 'c6004ex',
    'MP C3504ex': 'c3504ex',
    'IM C4500': 'c4500',
    'IM C6010': 'c6010',
    'IM C3510': 'c3510',
    'HP Color LaserJet MFP 5800': 'mfp5800',
    'HP Color LaserJet MFP M681': 'mfpm681',
}

DEFAULT_THRESHOLDS = {
    'ink_percent': 20,
    'tray_deficit_pages': 495,
    'paper_fill_percent': 50,
    'reminder_hours': 24,
}

DEFAULT_PRINTERS = [
    {'IP': '172.18.181.227', 'Name': 'CI-121',
     'Serial': 'C068C400217', 'EID': '14072973', 'Default': True},
    {'IP': '172.18.181.228', 'Name': 'CI-202',
     'Serial': 'C068C300002', 'EID': '14072974', 'Default': True},
    {'IP': '172.18.181.232', 'Name': 'CI-214',
     'Serial': 'C758M520307', 'EID': '14072971', 'Default': False},
    {'IP': '172.18.181.244', 'Name': 'CI-301',
     'Serial': 'C728M810465', 'EID': '14143593', 'Default': False},
    {'IP': '172.18.181.231', 'Name': 'CI-335',
     'Serial': 'C068C400148', 'EID': '14072972', 'Default': True},
    {'IP': '172.18.181.230', 'Name': 'CI-DO',
     'Serial': 'C758M520011', 'EID': '14072970', 'Default': True},
    {'IP': '172.18.178.120', 'Name': 'SDW-FL2',
     'Serial': 'C758M520012', 'EID': '14072977', 'Default': False},
    {'IP': '172.18.177.204', 'Name': 'ANX-A',
     'Serial': 'C068C400209', 'EID': '14072979', 'Default': True},
    {'IP': '172.19.55.10', 'Name': 'ANX-B',
     'Serial': 'C068C400222', 'EID': '14072976', 'Default': True},
    {'IP': '172.18.186.18', 'Name': 'RH-204',
     'Serial': 'C727M810074', 'EID': '14381339', 'Default': False},
]


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath('.')
    return os.path.join(base_path, relative_path)


def parse_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip() in ['true', 'True', 'TRUE', 't', 'T', '1', 'yes', 'Yes']


def normalize_printer(printer):
    return {
        'IP': str(printer.get('IP', '')).strip(),
        'Name': str(printer.get('Name', '')).strip(),
        'Serial': str(printer.get('Serial', '')).strip(),
        'EID': str(printer.get('EID', '')).strip(),
        'Default': parse_bool(printer.get('Default', False)),
    }


def default_printers():
    return copy.deepcopy(DEFAULT_PRINTERS)


def load_printers(config_path=PRINTERS_JSON, legacy_path=LEGACY_PRINTERS_PICKLE):
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            printers = json.load(f)
        return [normalize_printer(printer) for printer in printers]

    if legacy_path and os.path.exists(legacy_path):
        with open(legacy_path, 'rb') as f:
            printers = pickle.load(f)
        printers = [normalize_printer(printer) for printer in printers]
        save_printers(printers, config_path)
        return printers

    printers = default_printers()
    save_printers(printers, config_path)
    return printers


def save_printers(printers, config_path=PRINTERS_JSON):
    normalized = [normalize_printer(printer) for printer in printers]
    with open(config_path, 'w') as f:
        json.dump(normalized, f, indent=2, sort_keys=True)
        f.write('\n')
    return normalized
