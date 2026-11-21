from pathlib import Path
import pytest
from voltwatch.ingest import ingest_csv
from voltwatch.playback import BusState

@pytest.mark.parametrize('name', ['Normal operations', 'Low Voltage', 'Multiple module heating', 'Thermal rise', 'Sensor failure'])
def test_supplied_scenario_acceptance(name):
    path = next(Path('data').glob(f'*{name}*.csv'))
    scenario = ingest_csv(path.read_bytes(), path.name)
    bus = BusState('4101')
    observed = []
    for i, frame in enumerate(scenario['frames']):
        event = {'seq': i, 'run_id': 'run', 'kind': 'CSV_FRAME', 'timestamp': frame['timestamp'], 'frame_index': i, 'readings': frame['readings'], 'injected': False}
        bus.consume({'session_id': 'session', 'run_id': 'run', 'events': [event], 'gap': False, 'metadata': {}})
        observed += [(frame['timestamp'], a['module_id'], a['type'], a['severity']) for a in bus.snapshot()['alerts']]
    hazards = [x for x in observed if x[2] != 'DATA_QUALITY']
    if name == 'Normal operations':
        assert not observed
    elif name == 'Low Voltage':
        assert any(m == 'M03' and t == 'LOW_VOLTAGE' and s == 'CRITICAL' for _, m, t, s in hazards)
        assert all(m == 'M03' and t == 'LOW_VOLTAGE' for _, m, t, s in hazards)
    elif name == 'Multiple module heating':
        for module in ('M04', 'M05', 'M06'):
            assert any(m == module and t.startswith('TEMPERATURE') and stamp < '2026-01-01T12:05:00' for stamp, m, t, _ in hazards)
    elif name == 'Thermal rise':
        assert any(m == 'M07' and stamp < '2026-01-01T12:03:20' for stamp, m, t, _ in hazards)
        assert any(m == 'M07' and s == 'CRITICAL' for _, m, t, s in hazards)
    else:
        assert not hazards
        episodes = [(stamp, m) for stamp, m, t, _ in observed if t == 'DATA_QUALITY']
        assert sum(m == 'M11' for _, m in episodes) == 10
        assert {m for _, m in episodes} == {'M03', 'M06', 'M11'}

def test_each_supplied_scenario_loops_twice():
    from voltwatch.playback import PlaybackController
    for path in Path('data').glob('*.csv'):
        parsed = ingest_csv(path.read_bytes(), path.name)
        p = PlaybackController(parsed, '4101')
        p.tick(0)
        p.tick(720)
        assert p.metadata()['loop_count'] == 2
        assert p.metadata()['frame_index'] == 0
