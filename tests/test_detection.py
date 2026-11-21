from voltwatch.playback import BusState
from voltwatch.ingest import blank_reading


def batch(seq, temp, voltage=3.7):
    r = blank_reading('M01')
    r.update(temp_c=temp, voltage_v=voltage, sensor_status='OK')
    r['quality'].update(temp_c='ok' if temp is not None else 'missing', voltage_v='ok')
    return {'session_id': 's', 'run_id': 'r', 'gap': False, 'metadata': {}, 'events': [{'seq': seq, 'run_id': 'r', 'kind': 'CSV_FRAME', 'timestamp': f'2026-01-01T12:00:{seq:02}', 'frame_index': seq, 'readings': [r], 'injected': False}]}

def test_persistence_escalation_and_recovery_after_sensor_loss():
    b = BusState('4101')
    b.consume(batch(0, 40)); assert not b.snapshot()['alerts']
    b.consume(batch(1, 40)); alert = b.snapshot()['alerts'][0]
    assert alert['severity'] == 'WARNING'
    b.consume(batch(2, 55)); b.consume(batch(3, 55))
    assert b.snapshot()['alerts'][0]['id'] == alert['id']
    assert b.snapshot()['alerts'][0]['severity'] == 'CRITICAL'
    b.consume(batch(4, None)); b.consume(batch(5, 20)); b.consume(batch(6, 20))
    assert b.snapshot()['alerts'][0]['active']
    b.consume(batch(7, 20)); assert not b.snapshot()['alerts']

def test_high_voltage_boundary():
    b = BusState('4101')
    b.consume(batch(0, 30, 4.2)); b.consume(batch(1, 30, 4.2))
    assert b.snapshot()['alerts'][0]['severity'] == 'WARNING'
    b.consume(batch(2, 30, 4.201)); b.consume(batch(3, 30, 4.201))
    assert b.snapshot()['alerts'][0]['severity'] == 'CRITICAL'

def test_critical_recovers_to_still_active_warning():
    b = BusState('4101')
    for i, temp in enumerate([60, 45, 45, 45]):
        b.consume(batch(i, temp))
    alert = next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')
    assert alert['severity'] == 'WARNING'

def test_frozen_requires_changing_peers():
    from voltwatch.detection import frozen
    windows = {}
    for i in range(1, 13):
        windows[f'M{i:02}'] = [(f'2026-01-01T12:00:{n:02}', {'temp_c': 30 if i == 1 else 30 + n * 0.1, 'voltage_v': 3.7}) for n in range(6)]
    assert frozen(windows, 'M01', 'temp_c')
    assert not frozen(windows, 'M01', 'voltage_v')

def test_critical_deescalation_preserves_warning_until_its_margin():
    b = BusState('4101')
    for i, temp in enumerate([45, 45, 60, 39, 39, 39]):
        b.consume(batch(i, temp))
    alert = next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')
    assert alert['severity'] == 'WARNING'
    for i in range(6, 9):
        b.consume(batch(i, 38))
    assert not any(a['type'] == 'TEMPERATURE' for a in b.snapshot()['alerts'])

def test_critical_clear_does_not_skip_warning_recovery_frames():
    b = BusState('4101')
    for i, temp in enumerate([45, 45, 60, 52, 52, 37]):
        b.consume(batch(i, temp))
    assert next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')['severity'] == 'WARNING'
    b.consume(batch(6, 37))
    assert any(a['type'] == 'TEMPERATURE' for a in b.snapshot()['alerts'])
    b.consume(batch(7, 37))
    assert not any(a['type'] == 'TEMPERATURE' for a in b.snapshot()['alerts'])
