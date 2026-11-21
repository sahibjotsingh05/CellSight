"""Validate CSV input and preserve missing readings as gaps."""
import csv
import hashlib
import io
import math
import re
from datetime import datetime, timezone

MODULES = [f'M{i:02}' for i in range(1, 13)]
FIELDS = {'temperature (c)': 'temp_c', 'voltage (v)': 'voltage_v',
          'current (a)': 'current_a', 'state of charge (%)': 'soc_pct',
          'state of health (%)': 'soh_pct'}


class IngestError(ValueError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def blank_reading(module):
    return {'module_id': module, **{v: None for v in FIELDS.values()},
            'sensor_status': 'MISSING', 'quality': {v: 'missing' for v in FIELDS.values()}, 'raw': {}}


def ingest_csv(content: bytes, source_name: str) -> dict:
    report = {'source_name': source_name, 'issues': [], 'row_count': 0, 'frame_count': 0, 'missing_slots': 0}
    try:
        text = content.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        headers = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}
        aliases = {'module': 'module id', 'module_id': 'module id', 'temp_c': 'temperature (c)', 'voltage_v': 'voltage (v)', 'current_a': 'current (a)', 'soc_pct': 'state of charge (%)', 'soh_pct': 'state of health (%)', 'sensor_status': 'sensor status'}
        for alias, canonical in aliases.items():
            if alias in headers and canonical not in headers:
                headers[canonical] = headers[alias]
        if not {'timestamp', 'module id'} <= headers.keys() or not ({'temperature (c)', 'voltage (v)'} & headers.keys()):
            raise IngestError('CSV requires timestamp, module ID and at least one safety channel', report)
        for channel in ('temperature (c)', 'voltage (v)'):
            if channel not in headers:
                report['issues'].append(f'Missing column: {channel}')
        groups = {}
        aware = None
        previous = None
        for line, row in enumerate(reader, 2):
            report['row_count'] += 1
            try:
                timestamp = datetime.fromisoformat((row.get(headers['timestamp']) or '').strip())
                is_aware = timestamp.utcoffset() is not None
                if aware is not None and is_aware != aware:
                    report['issues'].append(f'Line {line}: mixed timezone timestamps quarantined')
                    continue
                aware = is_aware
                if is_aware:
                    timestamp = timestamp.astimezone(timezone.utc)
                if previous is not None and timestamp < previous:
                    report['issues'].append(f'Line {line}: timestamp out of order')
                previous = timestamp
                stamp = timestamp.isoformat()
                match = re.fullmatch(r'M\s*0*(\d+)', (row.get(headers['module id']) or '').strip(), re.I)
                number = int(match.group(1)) if match else 0
                if number not in range(1, 13):
                    raise ValueError('invalid module')
            except (ValueError, TypeError):
                report['issues'].append(f'Line {line}: invalid timestamp or module')
                continue
            module = f'M{number:02}'
            group = groups.setdefault(stamp, {})
            if module in group:
                report['issues'].append(f'Line {line}: duplicate {module} at {stamp}')
                continue
            reading = blank_reading(module)
            reading['raw'] = {str(k).strip(): v for k, v in row.items() if k is not None}
            status = (row.get(headers.get('sensor status', '')) or 'UNKNOWN').strip().upper()
            reading['sensor_status'] = status
            for header, key in FIELDS.items():
                value = (row.get(headers.get(header, '')) or '').strip()
                try:
                    parsed = float(value)
                    if not math.isfinite(parsed) or (key in ('soc_pct', 'soh_pct') and not 0 <= parsed <= 100):
                        raise ValueError
                    reading[key] = parsed
                    reading['quality'][key] = 'ok'
                except ValueError:
                    reading['quality'][key] = 'missing' if not value else 'invalid'
                    if value:
                        report['issues'].append(f'Line {line}: invalid {key}: {value}')
            group[module] = reading
        frames = []
        for stamp in sorted(groups, key=datetime.fromisoformat):
            group = groups[stamp]
            report['missing_slots'] += 12 - len(group)
            frames.append({'timestamp': stamp, 'readings': [group.get(m, blank_reading(m)) for m in MODULES]})
        if not frames or not any(r['temp_c'] is not None or r['voltage_v'] is not None for f in frames for r in f['readings']):
            raise IngestError('CSV has no usable safety readings', report)
        report['frame_count'] = len(frames)
        return {'id': hashlib.sha256(content).hexdigest()[:16], 'name': source_name, 'frames': frames, 'report': report}
    except UnicodeDecodeError as exc:
        raise IngestError('CSV must be UTF-8 text', report) from exc
    except (csv.Error, OverflowError) as exc:
        raise IngestError(f'CSV cannot be read: {exc}', report) from exc
