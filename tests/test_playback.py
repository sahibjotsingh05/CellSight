from voltwatch.playback import PlaybackController, BusState
from voltwatch.ingest import ingest_csv


def scenario():
    return ingest_csv(('Timestamp,Module ID,Temperature (C),Voltage (V),Sensor Status\n'
                       '2026-01-01 12:00:00,M01,30,3.7,OK\n'
                       '2026-01-01 12:00:10,M01,31,3.7,OK\n').encode(), 'test.csv')

def test_loop_injection_journal_and_no_read_advancement():
    p = PlaybackController(scenario(), '4101')
    p.tick(0)
    assert p.metadata()['frame_index'] == 0
    p.inject('M01', 'temperature', 1)
    assert p.metadata()['frame_index'] == 0
    p.tick(10)
    p.tick(20)
    batch = p.events_after(-1)
    assert [e['kind'] for e in batch['events']] == ['RUN_START', 'CSV_FRAME', 'INJECTION', 'CSV_FRAME', 'RUN_START', 'CSV_FRAME']
    assert [e['seq'] for e in batch['events']] == list(range(6))
    assert p.events_after(5)['events'] == []

def test_injection_alert_and_three_frame_recovery():
    p = PlaybackController(scenario(), '4101')
    b = BusState('4101')
    p.tick(0)
    p.inject('M01', 'temperature', 1)
    b.consume(p.events_after(-1))
    alert = next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')
    assert alert['severity'] == 'CRITICAL' and alert['injected']
    b.acknowledge(alert['id'])
    assert b.snapshot()['severity'] == 'CRITICAL'

def test_replacement_immediate_and_overflow_gap():
    p = PlaybackController(scenario(), '4101')
    p.tick(0)
    p.replace(scenario(), 1)
    assert p.metadata()['frame_index'] == 0
    for i in range(1001):
        p.inject('M01', 'missing', i + 2)
    assert p.events_after(-1)['gap']
    assert len(p.events_after(-1)['events']) == 1000

def test_paused_replacement_runs_and_preserves_closed_incidents():
    p = PlaybackController(scenario(), '4101'); b = BusState('4101')
    p.tick(0); p.inject('M01', 'temperature', 1)
    b.consume(p.events_after(-1))
    p.control('pause', now=2)
    p.replace(scenario(), 3)
    b.consume(p.events_after(b.sequence))
    assert not p.metadata()['paused']
    assert not any(a['type'] == 'TEMPERATURE' for a in b.snapshot()['alerts'])
    assert any(a['injected'] and a['close_reason'] == 'run_restart' for a in b.snapshot()['recent_alerts'])

def test_seek_replays_prior_injections_but_not_future():
    p = PlaybackController(scenario(), '4101')
    p.tick(0); p.inject('M01', 'temperature', 1); p.tick(10); p.inject('M01', 'missing', 11)
    last = p.seq
    p.control('seek', 0, now=12)
    replay = p.events_after(last)['events']
    assert [e['kind'] for e in replay] == ['RUN_START', 'CSV_FRAME', 'INJECTION']
    assert replay[-1]['readings'][0]['temp_c'] == 65

def test_tick_returns_new_events_without_advancing_on_read():
    p = PlaybackController(scenario(), '4101')
    result = p.tick(0)
    assert [e['kind'] for e in result['events']] == ['RUN_START', 'CSV_FRAME']
    assert p.tick(1)['events'] == []

def test_partial_injection_leaves_other_modules_and_next_due_unchanged():
    p = PlaybackController(scenario(), '4101'); b = BusState('4101')
    p.tick(0); b.consume(p.events_after(-1))
    before = b.snapshot()['modules'][1]
    p.inject('M01', 'temperature', 1); b.consume(p.events_after(b.sequence))
    assert b.snapshot()['modules'][1] == before
    p.tick(9.99); assert p.metadata()['frame_index'] == 0
    p.tick(10); assert p.metadata()['frame_index'] == 1

def test_irregular_timing_pause_speed_and_single_frame_wrap():
    p = PlaybackController(scenario(), '4101')
    p.tick(0); p.control('pause', now=2); p.tick(100)
    assert p.metadata()['frame_index'] == 0
    p.control('speed', 2, now=100); p.control('play', now=100)
    p.tick(103.99); assert p.metadata()['frame_index'] == 0
    p.tick(104); assert p.metadata()['frame_index'] == 1
    single = scenario(); single['frames'] = single['frames'][:1]
    p.replace(single, 200); p.tick(200.49)
    assert p.metadata()['loop_count'] == 0
    p.tick(200.5); assert p.metadata()['loop_count'] == 1

def test_gap_resets_evidence_without_resolving_existing_hazard():
    from test_detection import batch
    b = BusState('4101')
    b.consume(batch(0, 60))
    item = batch(1002, 20); item.update(gap=True, oldest_seq=1002)
    b.consume(item)
    assert b.snapshot()['continuity_gap']
    assert any(a['type'] == 'TEMPERATURE' and a['severity'] == 'CRITICAL' for a in b.snapshot()['alerts'])

def test_gap_across_evicted_run_start_clears_old_run_state():
    p = PlaybackController(scenario(), '4101'); b = BusState('4101')
    p.tick(0); p.inject('M01', 'temperature', 1)
    b.consume(p.events_after(-1))
    old_alert = next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')
    b.acknowledge(old_alert['id'])
    p.replace(scenario(), 2)
    for _ in range(1001):
        p.inject('M01', 'missing', 3)
    b.consume(p.events_after(b.sequence))
    snapshot = b.snapshot()
    assert snapshot['continuity_gap'] and snapshot['warming_up']
    assert b.history()['history_complete'] is False
    assert not any(a['id'] == old_alert['id'] for a in snapshot['alerts'])
    assert not b.history()['timestamps']
    assert any(a['id'] == old_alert['id'] for a in snapshot['recent_alerts'])

def test_synthetic_missing_does_not_break_csv_persistence():
    from test_detection import batch
    b = BusState('4101')
    b.consume(batch(0, 40))
    injection = {'session_id': 's', 'gap': False, 'metadata': {}, 'events': [{'seq': 1, 'run_id': 'r', 'kind': 'INJECTION', 'timestamp': '2026-01-01T12:00:00', 'frame_index': 0, 'injected': True, 'readings': [{'module_id': 'M01', 'temp_c': None, 'voltage_v': None, 'sensor_status': 'MISSING', 'quality': {'temp_c': 'missing', 'voltage_v': 'missing'}}]}]}
    b.consume(injection)
    b.consume(batch(2, 40))
    assert any(a['type'] == 'TEMPERATURE' and a['severity'] == 'WARNING' and not a['injected'] for a in b.snapshot()['alerts'])

def test_temp_injection_does_not_retag_voltage_incident():
    from test_detection import batch
    b = BusState('4101')
    b.consume(batch(0, 30, 3.5)); b.consume(batch(1, 30, 3.5))
    event = {'session_id': 's', 'gap': False, 'metadata': {}, 'events': [{'seq': 2, 'run_id': 'r', 'kind': 'INJECTION', 'timestamp': '2026-01-01T12:00:01', 'frame_index': 1, 'injected': True, 'readings': [{'module_id': 'M01', 'temp_c': 65, 'quality': {'temp_c': 'ok'}}]}]}
    b.consume(event)
    alerts = b.snapshot()['alerts']
    assert not next(a for a in alerts if a['type'] == 'LOW_VOLTAGE')['injected']
    assert next(a for a in alerts if a['type'] == 'TEMPERATURE')['injected']

def test_extreme_voltages_do_not_generate_nonfinite_summary():
    import json
    from test_detection import batch
    b = BusState('4101')
    payload = batch(0, 30, 1e308)
    second = dict(payload['events'][0]['readings'][0], module_id='M02', voltage_v=-1e308)
    payload['events'][0]['readings'].append(second)
    b.consume(payload)
    result = b.snapshot()
    assert result['summary']['voltage_spread'] is None
    assert result['summary']['voltage_spread_quality'] == 'overflow'
    json.dumps(result, allow_nan=False)

def test_dense_playback_caps_work_per_tick_without_skipping_events():
    p = PlaybackController(scenario(), '4101')
    p.scenario['frames'] = [dict(p.scenario['frames'][0], timestamp=f'2026-01-01T00:00:{i // 1000:02}.{i % 1000:03}') for i in range(1000)]
    p.tick(0)
    first = p.tick(2)
    assert len(first['events']) <= 256
    assert p.metadata()['catching_up']
    while p.metadata()['catching_up']:
        p.tick(2)
    assert p.metadata()['frame_index'] >= 0
    assert p.seq > 1000

def test_voltage_only_injection_does_not_retag_temperature():
    from test_detection import batch
    b = BusState('4101'); b.consume(batch(0, 60))
    item = {'session_id': 's', 'gap': False, 'metadata': {}, 'events': [{'seq': 1, 'run_id': 'r', 'kind': 'INJECTION', 'timestamp': '2026-01-01T12:00:00', 'frame_index': 0, 'injected': True, 'readings': [{'module_id': 'M01', 'voltage_v': 3.7, 'quality': {'voltage_v': 'ok'}}]}]}
    b.consume(item)
    assert not next(a for a in b.snapshot()['alerts'] if a['type'] == 'TEMPERATURE')['injected']

def test_temp_injection_does_not_retag_existing_missing_voltage_quality():
    from test_detection import batch
    b = BusState('4101')
    b.consume(batch(0, 25, None))
    before = next(a for a in b.snapshot()['alerts'] if a['type'] == 'DATA_QUALITY')
    assert not before['injected']
    item = {'session_id': 's', 'gap': False, 'metadata': {}, 'events': [{'seq': 1, 'run_id': 'r', 'kind': 'INJECTION', 'timestamp': '2026-01-01T12:00:00', 'frame_index': 0, 'injected': True, 'readings': [{'module_id': 'M01', 'temp_c': 65, 'quality': {'temp_c': 'ok'}}]}]}
    b.consume(item)
    assert not next(a for a in b.snapshot()['alerts'] if a['type'] == 'DATA_QUALITY')['injected']

def test_invalid_speed_preserves_playback_timing():
    import pytest
    p = PlaybackController(scenario(), '4101')
    p.tick(0)
    due = p.due
    for value in (0, -1, 21, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            p.control('speed', value, now=1)
        assert p.speed == 1 and p.due == due
    p.control('speed', 20, now=1)
    assert p.speed == 20

def test_intact_restart_clears_history_gap():
    from test_detection import batch
    b = BusState('4101')
    b.consume(batch(0, 30))
    gap = batch(1002, 30); gap.update(gap=True, oldest_seq=1002)
    b.consume(gap)
    assert b.snapshot()['continuity_gap'] and not b.history()['history_complete']
    restart = batch(1004, 30)
    restart['events'][0]['run_id'] = 'new-run'
    restart['events'].insert(0, {'seq': 1003, 'run_id': 'new-run', 'kind': 'RUN_START', 'readings': []})
    b.consume(restart)
    assert not b.snapshot()['continuity_gap'] and not b.snapshot()['warming_up']
    assert b.history()['history_complete']
