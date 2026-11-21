"""One bus: consume the utility journal and serve its own decisions."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from voltwatch.playback import BusState
from voltwatch.route import RouteState
from voltwatch.supervisor import watch_owner

STATIC = Path(__file__).parent / 'static'


def create_bus(spec):
    state = BusState(spec.bus_id)
    route = RouteState(spec.route_index)
    last_success = None
    poll_failed = True
    last_position = route.position(time.monotonic())
    playback = {}

    @asynccontextmanager
    async def lifespan(app):
        async def poll():
            nonlocal last_success, last_position, playback, poll_failed
            async with httpx.AsyncClient(timeout=1.5) as client:
                while True:
                    now = time.monotonic()
                    last_position = route.position(now)
                    try:
                        seq = state.snapshot().get('event_sequence', -1)
                        response = await client.get(f'{spec.utility_url}/api/bms/{spec.bus_id}/events', params={'after_seq': seq})
                        response.raise_for_status()
                        batch = response.json()
                        state.consume(batch)
                        playback = batch.get('metadata', {})
                        last_success = time.monotonic()
                        poll_failed = False
                    except (httpx.HTTPError, ValueError):
                        poll_failed = True
                    await asyncio.sleep(0.5)
        task = asyncio.create_task(poll())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    def snapshot():
        result = dict(state.snapshot())
        fresh = not poll_failed and last_success is not None and time.monotonic() - last_success < 3
        result.update({'session_id': spec.session_id, 'name': spec.name or f'Bus {spec.bus_id}',
                       'playback': playback, 'link_state': 'LIVE' if fresh else 'BMS_LINK_LOST',
                       'position': last_position, 'route': route.info, 'vehicle_state': route.vehicle_state,
                       'demo_override': route.demo_override,
                       'expanded_allowed': fresh and (route.vehicle_state == 'parked' or route.demo_override)})
        return result

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'driver.html')

    @app.get('/expanded')
    async def expanded():
        if not snapshot()['expanded_allowed']:
            return RedirectResponse('/', status_code=303)
        return FileResponse(STATIC / 'driver.html')

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'role': 'bus', 'session_id': spec.session_id, 'bus_id': spec.bus_id}

    @app.get('/api/status')
    async def status():
        return snapshot()

    @app.get('/api/modules')
    async def modules():
        return {'modules': snapshot().get('modules', [])}

    def version(current):
        return {key: current[key] for key in ('session_id', 'run_id', 'event_sequence')}

    @app.get('/api/history')
    async def history():
        current = snapshot()
        return {**state.history(), **version(current)}

    @app.get('/api/alerts')
    async def alerts():
        current = snapshot()
        return {'alerts': current.get('alerts', []), 'recent_alerts': current.get('recent_alerts', []), **version(current)}

    @app.post('/api/alerts/{alert_id}/ack')
    async def acknowledge(alert_id: str):
        try:
            state.acknowledge(alert_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(404, 'Unknown alert') from exc
        return snapshot()

    @app.post('/api/vehicle-state')
    async def vehicle_state(body: dict):
        nonlocal last_position
        value = body.get('vehicle_state')
        override = body.get('demo_override', False)
        if value not in ('moving', 'parked') or not isinstance(override, bool):
            raise HTTPException(400, 'Expected moving or parked and a boolean demo_override')
        now = time.monotonic()
        route.set_state(value, override, now)
        last_position = route.position(now)
        return snapshot()

    return app


def serve_bus(spec, stop_event):
    server = uvicorn.Server(uvicorn.Config(create_bus(spec), host='127.0.0.1', port=spec.port, log_level='warning', workers=1))
    watch_owner(server, stop_event)
    server.run()
