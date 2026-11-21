import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_base():
    for base in range(24000, 30000, 7):
        sockets = []
        try:
            for port in (base, base + 101, base + 200):
                sock = socket.socket()
                sock.bind(('127.0.0.1', port))
                sockets.append(sock)
            return base
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError('No available ports')


def wait_health(port, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=.2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    return False


def wait_closed(port, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return True
        time.sleep(.1)
    return False


def test_owner_kill_closes_all_children():
    base = free_base()
    process = subprocess.Popen([sys.executable, 'run.py', '--buses', '1', '--base-port', str(base)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert all(wait_health(port) for port in (base, base + 101, base + 200))
        lines = [process.stdout.readline() for _ in range(3)]
        pids = [int(line.split('PID ')[1].split(')')[0]) for line in lines if 'PID ' in line]
        assert len(pids) == 2
        deadline = time.monotonic() + 5
        process.kill()
        process.wait(timeout=5)
        assert all(wait_closed(port, max(0, deadline - time.monotonic())) for port in (base, base + 101, base + 200))
        for pid in pids:
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.05)
            else:
                raise AssertionError(f'Child PID {pid} survived owner death deadline')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_busy_port_is_preserved_and_startup_fails():
    base = free_base()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', base + 101))
        sock.listen()
        proc = subprocess.run([sys.executable, 'run.py', '--buses', '1', '--base-port', str(base)], cwd=ROOT, capture_output=True, timeout=12)
        assert proc.returncode != 0
        assert sock.fileno() >= 0
        assert wait_closed(base)
        assert wait_closed(base + 200)


def _ignore_term(event):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    event.set()
    while True:
        time.sleep(.1)


def test_stop_children_kills_stubborn_owned_child():
    import multiprocessing
    from voltwatch.supervisor import ChildHandle, ChildSpec, stop_children
    ctx = multiprocessing.get_context('spawn')
    ready = ctx.Event()
    process = ctx.Process(target=_ignore_term, args=(ready,))
    process.start()
    assert ready.wait(5)
    child = ChildHandle(ChildSpec('bus', 1, '', ''), process, ctx.Event())
    stop_children([child], timeout_s=.3)
    assert not process.is_alive()


def test_graceful_shutdown_and_bus_failure_are_isolated():
    base = free_base()
    proc = subprocess.Popen([sys.executable, 'run.py', '--buses', '1', '--base-port', str(base)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert all(wait_health(port) for port in (base, base + 101, base + 200))
        lines = [proc.stdout.readline() for _ in range(3)]
        bus_pid = int(next(line for line in lines if line.startswith('Bus:')).split('PID ')[1].split(')')[0])
        os.kill(bus_pid, signal.SIGKILL)
        assert wait_closed(base + 101)
        assert wait_health(base, 2) and wait_health(base + 200, 2)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            fleet = httpx.get(f'http://127.0.0.1:{base + 200}/api/fleet').json()
            if fleet['buses'] and fleet['buses'][0]['link_state'] == 'NO_SIGNAL':
                break
            time.sleep(.2)
        else:
            raise AssertionError(f'City did not mark the lost bus NO_SIGNAL: {fleet}')
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=7) == 0
        assert wait_closed(base) and wait_closed(base + 200)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_services_share_version_and_gate_driver_details():
    base = free_base()
    proc = subprocess.Popen([sys.executable, 'run.py', '--buses', '1', '--base-port', str(base)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert all(wait_health(port) for port in (base, base + 101, base + 200))
        utility = f'http://127.0.0.1:{base}'
        bus = f'http://127.0.0.1:{base + 101}'
        city = f'http://127.0.0.1:{base + 200}'
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = httpx.get(bus + '/api/status').json()
            fleet = httpx.get(city + '/api/fleet').json()
            if status['link_state'] == 'LIVE' and fleet['buses'] and fleet['buses'][0].get('event_sequence') == status['event_sequence']:
                break
            time.sleep(.2)
        else:
            raise AssertionError('No matching TCU and city version')
        assert status['session_id'] == httpx.get(utility + '/api/health').json()['session_id']
        assert status['expanded_allowed'] is False
        assert httpx.get(bus + '/expanded', follow_redirects=False).status_code == 303
        result = httpx.post(city + '/api/buses/4101/vehicle-state', json={'vehicle_state': 'parked', 'demo_override': False})
        assert result.status_code == 200 and result.json()['expanded_allowed']
        assert result.json()['position']['speed_kmh'] == 0
        assert httpx.get(utility + '/api/fleet').json()['buses'][0]['vehicle_state'] == 'parked'
        assert httpx.get(bus + '/expanded', follow_redirects=False).status_code == 200
        result = httpx.post(city + '/api/buses/4101/vehicle-state', json={'vehicle_state': 'moving', 'demo_override': False})
        assert result.status_code == 200 and not result.json()['expanded_allowed']
        assert result.json()['position']['speed_kmh'] > 0
        assert httpx.post(city + '/api/buses/4101/inject', json={'module_id': 'M07', 'preset': 'temperature'}).status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = httpx.get(bus + '/api/status').json()
            if status['severity'] == 'CRITICAL':
                break
            time.sleep(.2)
        assert status['severity'] == 'CRITICAL'
        assert any(alert['injected'] for alert in status['alerts'])
        old_run = status['run_id']
        assert httpx.post(city + '/api/buses/4101/playback', json={'action': 'reset'}).status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            history = httpx.get(city + '/api/buses/4101/history').json()
            alerts = httpx.get(city + '/api/buses/4101/alerts').json()
            if history['run_id'] != old_run and alerts['run_id'] == history['run_id']:
                break
            time.sleep(.2)
        assert history['session_id'] == status['session_id'] == alerts['session_id']
        assert history['run_id'] != old_run
        assert history['event_sequence'] >= status['event_sequence']
        assert all(not alert['injected'] for alert in alerts['alerts'])
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=8)


def test_graceful_shutdown_allows_immediate_same_port_restart():
    base = free_base()
    for _ in range(2):
        proc = subprocess.Popen([sys.executable, 'run.py', '--buses', '1', '--base-port', str(base)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert all(wait_health(port) for port in (base, base + 101, base + 200))
            with httpx.Client() as client:
                assert client.get(f'http://127.0.0.1:{base}/api/registry').status_code == 200
                proc.send_signal(signal.SIGTERM)
                assert proc.wait(timeout=8) == 0
            assert all(wait_closed(port) for port in (base, base + 101, base + 200))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
