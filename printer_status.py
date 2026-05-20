import traceback

from ricoh_config import (
    DEFAULT_THRESHOLDS,
    ERROR_BASE_OID,
    INK_LEVELS_BASE_OID,
    INK_SLOTS,
    MODEL_IMAGE_KEYS,
    MODEL_OID,
    TRAY_CURRENT_CAPACITY_BASE_OID,
    TRAY_FEED_DIM_OID,
    TRAY_MAX_CAPACITY_BASE_OID,
    TRAY_NAMES_BASE_OID,
    TRAY_XFEED_DIM_OID,
)


def _decode(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)


def _value(item):
    try:
        return item[1]
    except TypeError:
        return item


class SnmpClient(object):
    def __init__(self, get_func=None, walk_func=None):
        self.get_func = get_func
        self.walk_func = walk_func

    def get(self, ip, community, oid):
        if self.get_func is not None:
            return self.get_func(ip, community, oid)
        from puresnmp import get
        return get(ip, community, oid)

    def walk(self, ip, community, oid):
        if self.walk_func is not None:
            return self.walk_func(ip, community, oid)
        from puresnmp import walk
        return walk(ip, community, oid)


def normalize_tray_name(name):
    return _decode(name).replace('Paper', '').replace('Tray 3 (LCT)', 'LCT').strip()


def parse_paper_size(dim1, dim2):
    dims = sorted([int(dim1), int(dim2)])

    if abs(dims[0] - 85000) < 1000 and abs(dims[1] - 110000) < 1000:
        return '8.5x11'
    if abs(dims[0] - 85000) < 1000 and abs(dims[1] - 140000) < 1000:
        return '8.5x14'
    if abs(dims[0] - 110000) < 1000 and abs(dims[1] - 170000) < 1000:
        return '11x17'

    width = round(dims[0] / 10000.0, 1)
    height = round(dims[1] / 10000.0, 1)
    return str(width) + 'x' + str(height)


def resolve_model_image_key(model, tray_names):
    if model == 'MP C6503':
        for tray_name in tray_names:
            if 'LCT' in tray_name:
                return 'c6503f'
        return 'c6503'
    return MODEL_IMAGE_KEYS.get(model, 'missing_model')


def get_printer_status(printer, snmp_client=None):
    if snmp_client is None:
        snmp_client = SnmpClient()

    ip = printer['IP']
    community = 'public'
    status = {
        'ok': False,
        'printer': printer,
        'ip': ip,
        'name': printer['Name'],
        'model': '',
        'image_key': 'no_connection',
        'alerts': [],
        'ink_levels': [],
        'tray_levels': [],
        'paper_deficit': 0,
        'paper_capacity': 0,
        'paper_percent': 0,
        'error': None,
        'error_traceback': None,
    }

    try:
        model = _decode(snmp_client.get(ip, community, MODEL_OID))
        alerts = [_decode(_value(item)) for item in snmp_client.walk(ip, community, ERROR_BASE_OID)]

        raw_ink_levels = [_value(item) for item in snmp_client.walk(ip, community, INK_LEVELS_BASE_OID)]
        ink_levels = []
        for index, level in enumerate(raw_ink_levels):
            if index >= len(INK_SLOTS) or INK_SLOTS[index] is None:
                continue
            ink_levels.append({
                'index': index,
                'name': INK_SLOTS[index]['name'],
                'style': INK_SLOTS[index]['style'],
                'level': int(level),
            })

        tray_names = [
            normalize_tray_name(_value(item))
            for item in snmp_client.walk(ip, community, TRAY_NAMES_BASE_OID)
        ]
        tray_current_levels = [
            int(_value(item))
            for item in snmp_client.walk(ip, community, TRAY_CURRENT_CAPACITY_BASE_OID)
        ]
        tray_max_levels = [
            int(_value(item))
            for item in snmp_client.walk(ip, community, TRAY_MAX_CAPACITY_BASE_OID)
        ]
        tray_feed_dims = [
            int(_value(item))
            for item in snmp_client.walk(ip, community, TRAY_FEED_DIM_OID)
        ]
        tray_xfeed_dims = [
            int(_value(item))
            for item in snmp_client.walk(ip, community, TRAY_XFEED_DIM_OID)
        ]

        tray_levels = []
        paper_deficit = 0
        paper_capacity = 0
        for index, tray_name in enumerate(tray_names):
            if index >= len(tray_current_levels) or index >= len(tray_max_levels):
                continue
            current_level = tray_current_levels[index]
            max_level = tray_max_levels[index]
            deficit = max_level - current_level
            percent = int((float(current_level) / max_level) * 100) if max_level else 0
            is_bypass = tray_name == 'Bypass Tray'
            is_low = deficit >= DEFAULT_THRESHOLDS['tray_deficit_pages']
            paper_size = 'Unknown'
            if index < len(tray_feed_dims) and index < len(tray_xfeed_dims):
                try:
                    paper_size = parse_paper_size(tray_feed_dims[index], tray_xfeed_dims[index])
                except Exception:
                    paper_size = 'Unknown'
            tray_levels.append({
                'index': index,
                'name': tray_name,
                'paper_size': paper_size,
                'current': current_level,
                'max': max_level,
                'deficit': deficit,
                'percent': percent,
                'is_bypass': is_bypass,
                'is_low': is_low,
            })
            if not is_bypass:
                paper_capacity += max_level
                paper_deficit += deficit

        paper_percent = 0
        if paper_capacity:
            paper_percent = int((float(paper_capacity - paper_deficit) / paper_capacity) * 100)

        status.update({
            'ok': True,
            'model': model,
            'image_key': resolve_model_image_key(model, tray_names),
            'alerts': alerts,
            'ink_levels': ink_levels,
            'tray_levels': tray_levels,
            'paper_deficit': paper_deficit,
            'paper_capacity': paper_capacity,
            'paper_percent': paper_percent,
        })
    except Exception as err:
        status.update({
            'error': str(err),
            'error_traceback': traceback.format_exc(),
        })

    return status


def find_status_issues(status, thresholds=None):
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    printer = status['printer']
    printer_key = printer['IP']
    issues = []

    if not status.get('ok'):
        error_text = status.get('error') or 'unknown error'
        issues.append({
            'key': printer_key + '|unreachable|connection',
            'printer_key': printer_key,
            'printer_name': printer['Name'],
            'printer_ip': printer['IP'],
            'type': 'unreachable',
            'severity': 'critical',
            'summary': 'Printer unreachable: ' + error_text,
            'fingerprint': error_text,
        })
        return issues

    for ink in status.get('ink_levels', []):
        if ink['level'] <= thresholds['ink_percent']:
            summary = ink['name'] + ' toner is at ' + str(ink['level']) + '%'
            issues.append({
                'key': printer_key + '|ink_low|' + ink['name'],
                'printer_key': printer_key,
                'printer_name': printer['Name'],
                'printer_ip': printer['IP'],
                'type': 'ink_low',
                'severity': 'warning',
                'summary': summary,
                'fingerprint': summary,
            })

    for tray in status.get('tray_levels', []):
        if tray.get('is_bypass'):
            continue
        if tray['deficit'] >= thresholds['tray_deficit_pages']:
            summary = (
                tray['name'] + ' needs ' + str(tray['deficit']) +
                ' pages (' + str(tray['current']) + '/' + str(tray['max']) + ')'
            )
            issues.append({
                'key': printer_key + '|tray_low|' + tray['name'],
                'printer_key': printer_key,
                'printer_name': printer['Name'],
                'printer_ip': printer['IP'],
                'type': 'tray_low',
                'severity': 'warning',
                'summary': summary,
                'fingerprint': summary,
            })

    for alert in status.get('alerts', []):
        if not alert:
            continue
        summary = 'Printer alert: ' + alert
        issues.append({
            'key': printer_key + '|printer_alert|' + alert,
            'printer_key': printer_key,
            'printer_name': printer['Name'],
            'printer_ip': printer['IP'],
            'type': 'printer_alert',
            'severity': 'warning',
            'summary': summary,
            'fingerprint': alert,
        })

    return issues


def statuses_with_issues(statuses, thresholds=None):
    return [
        {'status': status, 'issues': find_status_issues(status, thresholds)}
        for status in statuses
    ]
