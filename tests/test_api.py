from voltwatch.route import RouteState


def test_route_parking_stops_position_and_moving_resumes():
    route = RouteState(0)
    start = route.position(100)
    moved = route.position(110)
    assert (start['lat'], start['lng']) != (moved['lat'], moved['lng'])
    route.set_state('parked', False, 110)
    parked = route.position(120)
    assert (parked['lat'], parked['lng']) == (moved['lat'], moved['lng'])
    assert parked['speed_kmh'] == 0
    route.set_state('moving', False, 120)
    resumed = route.position(130)
    assert (resumed['lat'], resumed['lng']) != (parked['lat'], parked['lng'])


def test_utility_api_keeps_bus_playback_independent():
    from fastapi.testclient import TestClient
    from voltwatch.utility import create_utility
    buses = [{'bus_id': '4101', 'name': 'Bus 4101', 'port': 8101, 'base_url': 'http://127.0.0.1:8101', 'state': 'running'},
             {'bus_id': '4102', 'name': 'Bus 4102', 'port': 8102, 'base_url': 'http://127.0.0.1:8102', 'state': 'running'}]
    with TestClient(create_utility('session', buses)) as client:
        fleet = client.get('/api/fleet').json()['buses']
        assert fleet[0]['vehicle_state'] == 'moving'
        assert fleet[0]['demo_override'] is False
        scenarios = client.get('/api/scenarios').json()['scenarios']
        assert len(scenarios) >= 5
        assert all('frames' not in item for item in scenarios)
        initial = client.get('/api/bms/4102/events').json()
        other = next(s for s in scenarios if 'Low Voltage' in s['name'])
        changed = client.post('/api/buses/4101/scenario', json={'scenario_id': other['id']})
        assert changed.status_code == 200
        assert changed.json()['scenario_id'] == other['id']
        after = client.get('/api/bms/4102/events').json()
        assert after['metadata']['scenario_id'] == initial['metadata']['scenario_id']
        assert after['session_id'] == 'session'
        assert client.post('/api/buses/4101/inject', json={'module_id': 'M07', 'preset': 'temperature'}).json()['injected']
        assert client.post('/api/buses/4101/inject', json={'module_id': 'M99', 'preset': 'temperature'}).status_code == 400
        assert client.post('/api/buses/4101/scenario', json={'scenario_id': 'missing'}).status_code == 404
        assert client.post('/api/scenarios', files={'file': ('bad.csv', b'bad,data\n1,2', 'text/csv')}).status_code == 400


def test_bus_history_and_alerts_include_their_snapshot_version():
    from fastapi.testclient import TestClient
    from voltwatch.supervisor import ChildSpec
    from voltwatch.tcu import create_bus

    app = create_bus(ChildSpec('bus', 8101, 'http://127.0.0.1:1', 'test-session', '4101', 'Bus 4101'))
    with TestClient(app) as client:
        status = client.get('/api/status').json()
        history = client.get('/api/history').json()
        alerts = client.get('/api/alerts').json()
        for response in (history, alerts):
            assert {key: response[key] for key in ('session_id', 'run_id', 'event_sequence')} == {
                key: status[key] for key in ('session_id', 'run_id', 'event_sequence')
            }
        assert 'timestamps' in history and 'modules' in history
        assert 'alerts' in alerts and 'recent_alerts' in alerts


def test_city_marks_retained_invalid_position_stale(monkeypatch):
    import time
    import httpx
    from fastapi.testclient import TestClient
    from voltwatch.city import create_city
    from voltwatch.supervisor import ChildSpec

    count = 0
    def response(request):
        nonlocal count
        if request.url.path == '/api/registry':
            return httpx.Response(200, json={'buses': [{'bus_id': '4101', 'name': 'Bus 4101', 'base_url': 'http://bus'}]})
        count += 1
        position = {'lat': 53.54 if count == 1 else 120, 'lng': -113.49, 'observed_at': '2026-11-21T00:00:00+00:00'}
        return httpx.Response(200, json={'bus_id': '4101', 'link_state': 'LIVE', 'position': position})

    original = httpx.AsyncClient
    monkeypatch.setattr('voltwatch.city.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    with TestClient(create_city(ChildSpec('city', 8200, 'http://utility', 'session'))) as client:
        deadline = time.monotonic() + 3
        while count < 2 and time.monotonic() < deadline:
            time.sleep(.05)
        snapshot = client.get('/api/buses/4101').json()
        assert snapshot['position']['lat'] == 53.54
        assert snapshot['position']['observed_at'] == '2026-11-21T00:00:00+00:00'
        assert snapshot['position_stale'] is True
        assert snapshot['position_age_seconds'] >= .4
        assert snapshot['link_state'] == 'LIVE'


def test_bus_marks_bms_link_lost_on_first_failed_poll(monkeypatch):
    import time
    import httpx
    from fastapi.testclient import TestClient
    from voltwatch.ingest import ingest_csv
    from voltwatch.playback import PlaybackController
    from voltwatch.supervisor import ChildSpec
    from voltwatch.tcu import create_bus
    from pathlib import Path

    path = next(Path('data').glob('*Normal operations*.csv'))
    player = PlaybackController(ingest_csv(path.read_bytes(), path.name), '4101')
    player.session_id = 'session'
    player.tick(time.monotonic())
    count = 0
    def response(request):
        nonlocal count
        count += 1
        if count > 1:
            raise httpx.ConnectError('lost', request=request)
        return httpx.Response(200, json=player.events_after(-1))
    original = httpx.AsyncClient
    monkeypatch.setattr('voltwatch.tcu.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    with TestClient(create_bus(ChildSpec('bus', 8101, 'http://utility', 'session', '4101'))) as client:
        deadline = time.monotonic() + 2
        while count < 2 and time.monotonic() < deadline:
            time.sleep(.05)
        assert count >= 2
        assert client.get('/api/status').json()['link_state'] == 'BMS_LINK_LOST'
