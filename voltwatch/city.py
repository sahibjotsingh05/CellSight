"""Collect bus-owned snapshots and proxy operator requests."""
import asyncio
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from voltwatch.supervisor import watch_owner

STATIC = Path(__file__).parent / 'static'


def create_city(spec):
    buses = {}
    seen = {}
    position_seen = {}
    position_invalid = {}
    failed = set()
    utility_online = False

    @asynccontextmanager
    async def lifespan(app):
        async def poll():
            nonlocal utility_online
            async with httpx.AsyncClient(timeout=1.5) as client:
                async def poll_bus(bus):
                    bus_id = bus['bus_id']
                    try:
                        response = await client.get(bus['base_url'] + '/api/status')
                        response.raise_for_status()
                        data = response.json()
                        position = data.get('position') or {}
                        lat, lng = position.get('lat'), position.get('lng')
                        valid = (isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180 and bool(position.get('observed_at')))
                        previous = buses.get(bus_id, {}).get('position') or {}
                        position_invalid[bus_id] = not valid
                        if not valid:
                            data['position'] = previous or None
                        elif position.get('observed_at') != previous.get('observed_at'):
                            position_seen[bus_id] = time.monotonic()
                        buses[bus_id] = data
                        seen[bus_id] = time.monotonic()
                        failed.discard(bus_id)
                    except (httpx.HTTPError, ValueError):
                        failed.add(bus_id)
                while True:
                    try:
                        response = await client.get(spec.utility_url + '/api/registry')
                        response.raise_for_status()
                        roster = response.json()['buses']
                        utility_online = True
                        for bus in roster:
                            buses.setdefault(bus['bus_id'], {'bus_id': bus['bus_id'], 'name': bus['name'], 'link_state': 'NO_SIGNAL', 'position': None})
                        app.state.roster = {bus['bus_id']: bus for bus in roster}
                        await asyncio.gather(*(poll_bus(bus) for bus in roster))
                    except (httpx.HTTPError, ValueError, KeyError):
                        utility_online = False
                        await asyncio.gather(*(poll_bus(bus) for bus in app.state.roster.values()))
                    await asyncio.sleep(0.5)
        task = asyncio.create_task(poll())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan)
    app.state.roster = {}
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    def fleet_snapshot(bus_id):
        result = dict(buses[bus_id])
        age = time.monotonic() - seen.get(bus_id, 0)
        if age >= 30:
            result['link_state'] = 'OFFLINE' if bus_id in seen else 'NO_SIGNAL'
        elif bus_id in failed or age >= 3:
            result['link_state'] = 'NO_SIGNAL'
        result['age_seconds'] = round(age, 1) if bus_id in seen else None
        position_age = time.monotonic() - position_seen[bus_id] if bus_id in position_seen else None
        observed = (result.get('position') or {}).get('observed_at')
        try:
            observed_time = datetime.fromisoformat(observed)
            if observed_time.tzinfo is None:
                observed_time = observed_time.replace(tzinfo=timezone.utc)
            wall_age = max(0, (datetime.now(timezone.utc) - observed_time).total_seconds())
            position_age = max(position_age or 0, wall_age)
        except (ValueError, TypeError, OverflowError):
            position_age = None
        result['position_age_seconds'] = round(position_age, 1) if position_age is not None else None
        result['position_stale'] = (position_age is None or position_age >= 3 or position_invalid.get(bus_id, True) or bus_id in failed)
        if result.get('link_state') in ('NO_SIGNAL', 'OFFLINE'):
            result['expanded_allowed'] = False
        return result

    async def proxy(url, request):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.request(request.method, url, content=await request.body(), headers={'content-type': request.headers.get('content-type', 'application/json')}, params=request.query_params)
            return Response(response.content, status_code=response.status_code, media_type=response.headers.get('content-type'))
        except httpx.HTTPError as exc:
            raise HTTPException(503, 'Service unavailable') from exc

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'role': 'city', 'session_id': spec.session_id}

    @app.get('/api/fleet')
    async def fleet():
        return {'buses': [fleet_snapshot(bus_id) for bus_id in buses], 'updated_at': datetime.now(timezone.utc).isoformat(), 'utility_online': utility_online}

    @app.get('/api/buses/{bus_id}')
    async def bus_detail(bus_id: str):
        if bus_id not in buses:
            raise HTTPException(404, 'Unknown bus')
        return fleet_snapshot(bus_id)

    @app.api_route('/api/buses/{bus_id}/{path:path}', methods=['GET', 'POST'])
    async def bus_proxy(bus_id: str, path: str, request: Request):
        if bus_id not in app.state.roster:
            raise HTTPException(404, 'Unknown bus')
        if path in ('history', 'alerts') or path.startswith('alerts/'):
            return await proxy(app.state.roster[bus_id]['base_url'] + '/api/' + path, request)
        if path in ('scenario', 'playback', 'inject', 'vehicle-state') and request.method == 'POST':
            return await proxy(spec.utility_url + f'/api/buses/{bus_id}/{path}', request)
        raise HTTPException(404, 'Unknown endpoint')

    @app.api_route('/api/scenarios', methods=['GET', 'POST'])
    async def scenarios(request: Request):
        return await proxy(spec.utility_url + '/api/scenarios', request)

    return app


def serve_city(spec, stop_event):
    server = uvicorn.Server(uvicorn.Config(create_city(spec), host='127.0.0.1', port=spec.port, log_level='warning', workers=1))
    watch_owner(server, stop_event)
    server.run()
