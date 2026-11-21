from pathlib import Path
import pytest
from voltwatch.ingest import ingest_csv, IngestError

HEADER = 'Timestamp,Module ID,Temperature (C),Voltage (V),Current (A),State of charge (%),State of Health (%),Sensor Status \n'

def test_real_normal_frames():
    path = next(Path('data').glob('*Normal operations*.csv'))
    result = ingest_csv(path.read_bytes(), path.name)
    assert len(result['frames']) == 36
    assert all(len(f['readings']) == 12 for f in result['frames'])
    assert result['frames'][0]['readings'][0]['module_id'] == 'M01'

def test_corruption_and_missing_slots_are_explicit():
    rows = ('2026-01-01 12:00:00,m7,nan,3.7,12,80,95,OK\n'
            '2026-01-01 12:00:00,M07,99,3.7,12,80,95,OK\n'
            'bad,M01,2,3,1,80,90,OK\n'
            '2026-01-01 12:00:00,M13,2,3,1,80,90,OK\n')
    result = ingest_csv(('\ufeff'+HEADER+rows).encode(), 'sample.csv')
    r = result['frames'][0]['readings'][6]
    assert r['temp_c'] is None and r['voltage_v'] == 3.7
    assert result['frames'][0]['readings'][0]['temp_c'] is None
    assert len(result['report']['issues']) >= 3

def test_unusable_file_rejected():
    with pytest.raises(IngestError):
        ingest_csv(b'wrong,columns\na,b\n', 'bad.csv')

def test_aliases_invalid_values_and_mixed_timezones_are_reported():
    data = ('timestamp,module,temp_c,voltage_v\n'
            '2026-01-01T00:00:10+00:00,M01,garbage,3.7\n'
            '2026-01-01T00:00:00,M01,30,3.7\n')
    result = ingest_csv(data.encode(), 'aliases.csv')
    assert len(result['frames']) == 1
    assert any('timezone' in issue.lower() for issue in result['report']['issues'])
    assert any('temp_c' in issue.lower() for issue in result['report']['issues'])

def test_out_of_order_input_reported():
    data = HEADER + '2026-01-01T00:00:10,M01,30,3.7,1,80,90,OK\n2026-01-01T00:00:00,M01,30,3.7,1,80,90,OK\n'
    result = ingest_csv(data.encode(), 'order.csv')
    assert any('order' in i.lower() for i in result['report']['issues'])

def test_oversize_field_and_timezone_overflow_are_readable_errors():
    oversize = HEADER + '2026-01-01,M01,' + '1' * 140000 + ',3.7,1,80,90,OK\n'
    with pytest.raises(IngestError):
        ingest_csv(oversize.encode(), 'oversize.csv')
    overflow = HEADER + '0001-01-01T00:00:00+14:00,M01,30,3.7,1,80,90,OK\n'
    with pytest.raises(IngestError):
        ingest_csv(overflow.encode(), 'overflow.csv')
