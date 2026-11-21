"""Run the foreground utility and its owned bus and city servers."""
import argparse
import signal
import socket
import sys
import threading
import time
import uuid

import httpx
import uvicorn

from voltwatch.supervisor import ChildSpec, start_children, stop_children
from voltwatch.utility import create_utility
from voltwatch.config import MAX_SPEED
from voltwatch.route import route_for


def main():
    parser = argparse.ArgumentParser(description='Run the VoltWatch local fleet')
    parser.add_argument('--buses', type=int, default=5)
    parser.add_argument('--base-port', type=int, default=8000)
    parser.add_argument('--csv')
    parser.add_argument('--speed', type=float, default=1)
    args = parser.parse_args()
    if not 1 <= args.buses <= 20 or not 1 <= args.base_port <= 65335 or not 0 < args.speed <= MAX_SPEED:
        parser.error('Use 1–20 buses, a valid base port, and speed greater than 0 and at most 20')

    session_id = str(uuid.uuid4())
    utility_url = f'http://127.0.0.1:{args.base_port}'
    # Each bus is assigned a real ETS route corridor, cycling when the fleet exceeds the route list.
    buses = [{'bus_id': str(4101 + i), 'name': f"Route {route_for(i)['number']} {route_for(i)['name']}",
              'route': route_for(i), 'port': args.base_port + 101 + i,
              'base_url': f'http://127.0.0.1:{args.base_port + 101 + i}', 'state': 'starting'}
             for i in range(args.buses)]
    specs = [ChildSpec('bus', bus['port'], utility_url, session_id, bus['bus_id'], bus['name'], i)
             for i, bus in enumerate(buses)]
    specs.append(ChildSpec('city', args.base_port + 200, utility_url, session_id))
    children = []
    server = None
    thread = None
    try:
        app = create_utility(session_id, buses, args.csv, args.speed)
        for port in [args.base_port] + [spec.port for spec in specs]:
            try:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(('127.0.0.1', port))
            except OSError as exc:
                raise RuntimeError(f'Port {port} unavailable: {exc}') from exc
        # Handle stop signals from the start so a stop during startup still cleans up.
        stopping = threading.Event()
        def shutdown(signum, frame):
            stopping.set()
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        children = start_children(specs)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.base_port, log_level='warning', workers=1))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        pending = {('utility', args.base_port)} | {(spec.role, spec.port) for spec in specs}
        with httpx.Client(timeout=.3) as client:
            while pending and time.monotonic() < deadline and not stopping.is_set():
                for role, port in list(pending):
                    try:
                        response = client.get(f'http://127.0.0.1:{port}/api/health')
                        if response.status_code == 200 and response.json().get('session_id') == session_id:
                            pending.remove((role, port))
                    except (httpx.HTTPError, ValueError):
                        pass
                for child in children:
                    if not child.process.is_alive():
                        raise RuntimeError(f'{child.spec.role} on port {child.spec.port} exited during startup')
                if not thread.is_alive():
                    raise RuntimeError(f'Utility on port {args.base_port} exited during startup')
                if pending:
                    time.sleep(.1)
        if stopping.is_set():
            return 0
        if pending:
            raise RuntimeError('Health deadline exceeded: ' + ', '.join(f'{role} port {port}' for role, port in sorted(pending)))
        for bus in buses:
            bus['state'] = 'running'
        print(f'Utility: {utility_url}', flush=True)
        for child in children:
            print(f'{child.spec.role.title()}: http://127.0.0.1:{child.spec.port} (PID {child.process.pid})', flush=True)
        while not stopping.wait(.25) and thread.is_alive():
            for child in children:
                if child.spec.role == 'bus' and not child.process.is_alive():
                    next(bus for bus in buses if bus['bus_id'] == child.spec.bus_id)['state'] = 'offline'
        if not thread.is_alive() and not stopping.is_set():
            raise RuntimeError('Utility server exited unexpectedly')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'VoltWatch startup/runtime error: {exc}', file=sys.stderr, flush=True)
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        stop_children(children)
        if thread is not None:
            thread.join(timeout=2)


if __name__ == '__main__':
    sys.exit(main())
