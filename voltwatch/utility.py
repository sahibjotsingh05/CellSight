"""Utility-owned scenarios, playback clocks, and fleet registry."""
import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from voltwatch.ingest import ingest_csv, IngestError
from voltwatch.config import FRAME_INTERVAL_S
from voltwatch.playback import PlaybackController

STATIC = Path(__file__).parent / 'static'
ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data'
# CONFIG files are sustained-fault copies of the supplied scenarios (configs/make_configs.py).
CONFIGS = ROOT / 'configs'
UPLOADS = ROOT / 'uploads'
# (CONFIG file scenario, Simulate faults button label), in default bus order.
FAULTS = [('Normal operations', 'Normal operation'), ('Low Voltage', 'Low voltage'),
          ('Multiple module heating', 'Multi-module heating'), ('Thermal rise', 'Critical temperature'),
          ('Sensor failure', 'Missing sensor')]


def scenario_info(scenario):
    return {key: value for key, value in scenario.items() if key != 'frames'}


def upload_path(name, content):
    """A safe, non-clobbering path in uploads/ for an operator file."""
    stem = re.sub(r'[^A-Za-z0-9 ._()-]', '_', Path(name).stem).strip(' .') or 'upload'
    path = UPLOADS / f'{stem}.csv'
    number = 2
    while path.exists() and path.read_bytes() != content:
        path = UPLOADS / f'{stem} ({number}).csv'
        number += 1
    return path


def create_utility(session_id, buses, csv_path=None, speed=1):
    scenarios = {}
    for bus in buses:
        bus.setdefault('vehicle_state', 'moving')
        bus.setdefault('demo_override', False)
    UPLOADS.mkdir(exist_ok=True)
    for folder, source in ((DATA, 'supplied'), (CONFIGS, 'config'), (UPLOADS, 'upload')):
        for path in sorted(folder.glob('*.csv')):
            try:
                scenario = ingest_csv(path.read_bytes(), path.name)
            except IngestError:
                continue  # A bad stored upload must not stop the fleet starting.
            scenario['source'] = source
            fault = next((label for name, label in FAULTS if path.name == f'CONFIG - {name}.csv'), None)
            if source == 'config' and fault:
                scenario['fault'] = fault
            scenarios[scenario['id']] = scenario
    normal = next(s for s in scenarios.values() if 'normal operations' in s['name'].lower())
    if csv_path:
        path = Path(csv_path)
        default = ingest_csv(path.read_bytes(), path.name)
        default['source'] = 'supplied'
        scenarios[default['id']] = default
        starting = [default] * len(buses)
    else:
        # Each bus starts on a different CONFIG file so every scenario is visible at once.
        configs = [next((s for s in scenarios.values() if s['name'] == f'CONFIG - {name}.csv'), normal) for name, _ in FAULTS]
        starting = [configs[i % len(configs)] for i in range(len(buses))]
    for bus, scenario in zip(buses, starting):
        bus['default_scenario_id'] = scenario['id']
    controllers = {bus['bus_id']: PlaybackController(scenario, bus['bus_id'], FRAME_INTERVAL_S)
                   for bus, scenario in zip(buses, starting)}
    for controller in controllers.values():
        controller.session_id = session_id
        if speed != 1:
            controller.control('speed', speed, now=time.monotonic())

    @asynccontextmanager
    async def lifespan(app):
        async def clock():
            while True:
                now = time.monotonic()
                for controller in controllers.values():
                    controller.tick(now)
                await asyncio.sleep(0.05)
        task = asyncio.create_task(clock())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    app.state.controllers = controllers
    app.state.buses = buses

    def controller(bus_id):
        if bus_id not in controllers:
            raise HTTPException(404, 'Unknown bus')
        return controllers[bus_id]

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'utility.html')

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'role': 'utility', 'session_id': session_id}

    @app.get('/api/registry')
    async def registry():
        return {'session_id': session_id, 'buses': buses}

    @app.get('/api/fleet')
    async def fleet():
        return {'session_id': session_id, 'buses': [dict(b, playback=controllers[b['bus_id']].metadata()) for b in buses]}

    @app.get('/api/scenarios')
    async def list_scenarios():
        return {'scenarios': [scenario_info(s) for s in scenarios.values()]}

    @app.post('/api/scenarios')
    async def upload(file: UploadFile = File(...)):
        try:
            content = await file.read()
            path = upload_path(file.filename or 'upload.csv', content)
            scenario = ingest_csv(content, path.name)
        except IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Store only validated files, so the uploads list never holds unusable CSVs.
        path.write_bytes(content)
        scenario['source'] = 'upload'
        scenarios[scenario['id']] = scenario
        return scenario_info(scenario)

    @app.get('/api/bms/{bus_id}/events')
    async def events(bus_id: str, after_seq: int = -1):
        batch = controller(bus_id).events_after(after_seq)
        batch['session_id'] = session_id
        return batch

    @app.post('/api/buses/{bus_id}/scenario')
    async def assign(bus_id: str, body: dict):
        player = controller(bus_id)
        scenario = scenarios.get(body.get('scenario_id'))
        if scenario is None:
            raise HTTPException(404, 'Unknown scenario')
        player.replace(scenario, time.monotonic())
        return player.metadata()

    @app.post('/api/buses/{bus_id}/playback')
    async def playback(bus_id: str, body: dict):
        try:
            player = controller(bus_id)
            player.control(body.get('action'), body.get('value'), now=time.monotonic())
            return player.metadata()
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/buses/{bus_id}/inject')
    async def inject(bus_id: str, body: dict):
        try:
            return controller(bus_id).inject(body.get('module_id'), body.get('preset'), time.monotonic())
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/buses/{bus_id}/vehicle-state')
    async def vehicle_state(bus_id: str, body: dict):
        controller(bus_id)
        bus = next(b for b in buses if b['bus_id'] == bus_id)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.post(bus['base_url'] + '/api/vehicle-state', json=body)
            if response.is_error:
                raise HTTPException(response.status_code, response.json().get('detail', 'Bus rejected state'))
            result = response.json()
            bus['vehicle_state'] = result['vehicle_state']
            bus['demo_override'] = result['demo_override']
            return result
        except httpx.HTTPError as exc:
            raise HTTPException(503, 'Bus unavailable') from exc

    return app
