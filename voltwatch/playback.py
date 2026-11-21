"""Timestamp-paced playback and per-bus incident state."""
import math
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from statistics import median
from uuid import uuid4
from .config import (JOURNAL_LIMIT, TEMP_WARNING, TEMP_CRITICAL, TEMP_IMMEDIATE,
                     LOW_WARNING, LOW_CRITICAL, HIGH_WARNING, HIGH_CRITICAL,
                     RISE_WARNING, RISE_CRITICAL, RISE_CRITICAL_TEMP, RISE_RECOVERY,
                     TEMP_RECOVERY_MARGIN, VOLTAGE_RECOVERY_MARGIN, PERSISTENCE_FRAMES,
                     RISE_WINDOWS, RECOVERY_FRAMES, WINDOW_SIZE, MAX_EVENTS_PER_TICK, MAX_SPEED)
from .detection import slope, frozen
from .ingest import MODULES, blank_reading


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class PlaybackController:
    def __init__(self, scenario, bus_id, frame_interval=None):
        # frame_interval set: fixed pacing, elapsed time from 0, and seamless loops (the running app).
        # None: pace by source timestamps and start a fresh run at each loop.
        self.scenario, self.bus_id = scenario, bus_id
        self.frame_interval = frame_interval
        self.frames_played = 0
        self.session_id = str(uuid4())
        self.run_id = str(uuid4())
        self.journal = deque(maxlen=JOURNAL_LIMIT)
        self.seq = -1
        self.index = -1
        self.loop_count = 0
        self.speed = 1.0
        self.paused = False
        self.due = None
        self.remaining = None
        self.catching_up = False

    def _emit(self, kind, timestamp=None, readings=None, injected=False):
        self.seq += 1
        event = {'seq': self.seq, 'run_id': self.run_id, 'kind': kind, 'timestamp': timestamp,
                 'readings': deepcopy(readings or []), 'frame_index': self.index, 'injected': injected,
                 'elapsed_s': self.elapsed_s()}
        self.journal.append(event)
        return event

    def elapsed_s(self):
        return max(0, self.frames_played - 1) * (self.frame_interval or 1.0)

    def _gap(self, index):
        if self.frame_interval:
            return self.frame_interval
        frames = self.scenario['frames']
        if index + 1 < len(frames):
            return max(0, (datetime.fromisoformat(frames[index + 1]['timestamp']) - datetime.fromisoformat(frames[index]['timestamp'])).total_seconds())
        gaps = [(datetime.fromisoformat(b['timestamp']) - datetime.fromisoformat(a['timestamp'])).total_seconds() for a, b in zip(frames, frames[1:])]
        return median([g for g in gaps if g > 0]) if any(g > 0 for g in gaps) else 1.0

    def _start(self, now):
        self.run_id = str(uuid4())
        self.index = -1
        self.frames_played = 0
        self._emit('RUN_START')
        self._advance(now)

    def _advance(self, when):
        if self.index + 1 >= len(self.scenario['frames']):
            self.loop_count += 1
            if not self.frame_interval:
                self._start(when)
                return
            # Seamless loop: keep the run, its alerts and elapsed time; only restart the file cursor.
            self.index = -1
            self._emit('LOOP')
        self.index += 1
        self.frames_played += 1
        frame = self.scenario['frames'][self.index]
        self._emit('CSV_FRAME', frame['timestamp'], frame['readings'])
        self.due = when + self._gap(self.index) / self.speed

    def tick(self, now):
        prior = self.seq
        if self.due is None:
            self._start(now)
        while not self.paused and now >= self.due:
            cost = 2 if self.index + 1 >= len(self.scenario['frames']) else 1
            if self.seq - prior + cost > MAX_EVENTS_PER_TICK:
                break
            self._advance(self.due)
        self.catching_up = not self.paused and now >= self.due
        return self.events_after(prior)

    def replace(self, scenario, now):
        if not scenario.get('frames'):
            raise ValueError('Scenario contains no frames')
        self.scenario = scenario
        self.loop_count = 0
        self.paused = False
        self.remaining = None
        self._start(now)
        self.catching_up = False
        return self.metadata()

    def inject(self, module_id, preset, now):
        if module_id not in MODULES or preset not in ('temperature', 'missing'):
            raise ValueError('Invalid module or injection preset')
        if self.index < 0:
            self.tick(now)
        reading = {'module_id': module_id}
        if preset == 'temperature':
            reading.update(temp_c=65.0, quality={'temp_c': 'ok'})
        else:
            reading.update(temp_c=None, voltage_v=None, sensor_status='MISSING', quality={'temp_c': 'missing', 'voltage_v': 'missing'})
        return self._emit('INJECTION', self.scenario['frames'][self.index]['timestamp'], [reading], True)

    def control(self, action, value=None, now=None):
        import time
        now = time.monotonic() if now is None else now
        if self.due is None:
            self.tick(now)
        if action == 'pause':
            if not self.paused:
                self.remaining = max(0, self.due - now)
                self.paused = True
        elif action == 'play':
            if self.paused:
                self.due = now + (self.remaining if self.remaining is not None else 0)
                self.paused = False
        elif action == 'step':
            self.paused = True
            self._advance(now)
            self.remaining = self._gap(self.index) / self.speed
        elif action == 'reset':
            self.loop_count = 0
            self._start(now)
            if self.paused:
                self.remaining = self._gap(self.index) / self.speed
        elif action == 'speed':
            speed = float(value)
            if not 0 < speed <= MAX_SPEED:
                raise ValueError(f'Speed must be greater than 0 and at most {MAX_SPEED:g}')
            if self.paused:
                self.remaining = (self.remaining or 0) * self.speed / speed
            else:
                self.due = now + max(0, self.due - now) * self.speed / speed
            self.speed = speed
        elif action == 'seek':
            target = int(value)
            if target < 0 or target >= len(self.scenario['frames']):
                raise ValueError('Invalid frame index')
            injections = [deepcopy(e) for e in self.journal if e['run_id'] == self.run_id and e['kind'] == 'INJECTION' and e['frame_index'] <= target]
            self._start(now)
            for index in range(target + 1):
                if index:
                    self._advance(now)
                for event in injections:
                    if event['frame_index'] == index:
                        self._emit('INJECTION', event['timestamp'], event['readings'], True)
            if self.paused:
                self.remaining = self._gap(self.index) / self.speed
        else:
            raise ValueError('Unknown playback action')
        self.catching_up = not self.paused and self.due is not None and now >= self.due
        return self.metadata()

    def metadata(self):
        return {'scenario_id': self.scenario['id'], 'scenario_name': self.scenario['name'], 'frame_index': self.index,
                'frame_count': len(self.scenario['frames']), 'source_time': self.scenario['frames'][self.index]['timestamp'] if self.index >= 0 else None,
                'loop_count': self.loop_count, 'speed': self.speed, 'elapsed_s': self.elapsed_s(), 'paused': self.paused, 'run_id': self.run_id,
                'catching_up': self.catching_up}

    def events_after(self, after_seq):
        oldest = self.journal[0]['seq'] if self.journal else self.seq + 1
        return {'session_id': self.session_id, 'run_id': self.run_id, 'events': [deepcopy(e) for e in self.journal if e['seq'] > after_seq],
                'last_seq': self.seq, 'oldest_seq': oldest, 'gap': after_seq < oldest - 1, 'metadata': self.metadata()}


class BusState:
    def __init__(self, bus_id):
        self.bus_id = bus_id
        self.session_id = None
        self.run_id = None
        self.sequence = -1
        self.playback = {}
        self.recent = deque(maxlen=JOURNAL_LIMIT)
        self.updated_at = None
        self._reset()

    def _reset(self):
        self.continuity_gap = False
        self.readings = {m: blank_reading(m) for m in MODULES}
        self.windows = {m: [] for m in MODULES}
        self.counts = {}
        self.active = {}
        self.times = []
        self.elapsed = []
        self.chart = {m: {'temp_c': [], 'voltage_v': []} for m in MODULES}
        self.source_time = None

    def _incident(self, module, kind, severity, value, threshold, stamp, injected=False, message=None):
        key = (module, kind)
        if key not in self.active:
            self.active[key] = {'id': f'{self.bus_id}:{self.run_id}:{module}:{kind}', 'module_id': module, 'type': kind,
                                'first_seen': stamp, 'acknowledged': False, 'injected': False}
        incident = self.active[key]
        incident.update(severity=severity, value=value, threshold=threshold, last_seen=stamp, active=True,
                        message=message or f'{module} {kind.lower().replace("_", " ")}: {value}',
                        injected=incident['injected'] or injected)

    def _close(self, key, stamp):
        if key in self.active:
            incident = self.active.pop(key)
            incident['active'] = False
            incident['last_seen'] = stamp
            self.recent.appendleft(incident)

    def _rule(self, module, kind, value, stamp, injected, conditions, recovery):
        key = (module, kind)
        warning, critical, immediate, warn_limit, crit_limit = conditions
        if injected:
            if immediate:
                self._incident(module, kind, 'CRITICAL', value, crit_limit, stamp, True)
            return
        state = self.counts.setdefault(key, {'warning': 0, 'critical': 0, 'clear': 0, 'warning_clear': 0})
        if value is None:
            state.update(warning=0, critical=0, clear=0, warning_clear=0)
            return
        state['warning'] = state['warning'] + 1 if warning else 0
        state['critical'] = state['critical'] + 1 if critical else 0
        state['warning_clear'] = state['warning_clear'] + 1 if recovery('WARNING') else 0
        severity = 'CRITICAL' if immediate or state['critical'] >= PERSISTENCE_FRAMES else 'WARNING' if state['warning'] >= PERSISTENCE_FRAMES else None
        current = self.active.get(key)
        if current and current['severity'] == 'CRITICAL':
            severity = 'CRITICAL'
        if severity:
            self._incident(module, kind, severity, value, crit_limit if severity == 'CRITICAL' else warn_limit, stamp, injected)
        if key in self.active and not injected:
            state['clear'] = state['clear'] + 1 if recovery(self.active[key]['severity']) else 0
            if state['clear'] >= RECOVERY_FRAMES:
                if self.active[key]['severity'] == 'CRITICAL' and state['warning_clear'] < RECOVERY_FRAMES:
                    self._incident(module, kind, 'WARNING', value, warn_limit, stamp, injected)
                    state.update(critical=0, clear=state['warning_clear'])
                else:
                    self._close(key, stamp)
                    state.update(warning=0, critical=0, clear=0, warning_clear=0)

    def _process(self, event):
        kind, stamp = event['kind'], event.get('timestamp')
        if kind == 'RUN_START':
            for key in list(self.active):
                self.active[key]['close_reason'] = 'run_restart'
                self._close(key, self.source_time)
            self._reset()
            return
        if kind == 'LOOP':
            # Source timestamps restart, so trend windows must not straddle the wrap.
            self.windows = {m: [] for m in MODULES}
            return
        if kind not in ('CSV_FRAME', 'INJECTION'):
            return
        injected = kind == 'INJECTION'
        self.source_time = stamp
        for patch in event['readings']:
            module = patch['module_id']
            if module not in self.readings:
                continue
            reading = self.readings[module]
            for k, v in patch.items():
                if k == 'quality':
                    reading['quality'].update(v)
                else:
                    reading[k] = deepcopy(v)
            temp, voltage = reading.get('temp_c'), reading.get('voltage_v')
            if not injected:
                self.windows[module].append((stamp, deepcopy(reading)))
                self.windows[module] = self.windows[module][-WINDOW_SIZE:]
            if not injected or 'temp_c' in patch:
                self._rule(module, 'TEMPERATURE', temp, stamp, injected,
                           (temp is not None and temp >= TEMP_WARNING, temp is not None and temp >= TEMP_CRITICAL, temp is not None and temp >= TEMP_IMMEDIATE, TEMP_WARNING, TEMP_CRITICAL),
                           lambda sev: temp is not None and temp <= (TEMP_CRITICAL - TEMP_RECOVERY_MARGIN if sev == 'CRITICAL' else TEMP_WARNING - TEMP_RECOVERY_MARGIN))
            self._rule(module, 'LOW_VOLTAGE', voltage, stamp, injected,
                       (voltage is not None and voltage <= LOW_WARNING, voltage is not None and voltage <= LOW_CRITICAL, False, LOW_WARNING, LOW_CRITICAL),
                       lambda sev: voltage is not None and voltage >= (LOW_CRITICAL + VOLTAGE_RECOVERY_MARGIN if sev == 'CRITICAL' else LOW_WARNING + VOLTAGE_RECOVERY_MARGIN))
            self._rule(module, 'HIGH_VOLTAGE', voltage, stamp, injected,
                       (voltage is not None and voltage >= HIGH_WARNING, voltage is not None and voltage > HIGH_CRITICAL, False, HIGH_WARNING, HIGH_CRITICAL),
                       lambda sev: voltage is not None and voltage <= (HIGH_CRITICAL - VOLTAGE_RECOVERY_MARGIN if sev == 'CRITICAL' else HIGH_WARNING - VOLTAGE_RECOVERY_MARGIN))
            quality_bad = temp is None or voltage is None or reading.get('sensor_status') not in ('OK',)
            bad_patch = (any(field in patch and patch[field] is None for field in ('temp_c', 'voltage_v'))
                         or ('sensor_status' in patch and patch['sensor_status'] != 'OK')
                         or any(patch.get('quality', {}).get(field) in ('missing', 'invalid') for field in ('temp_c', 'voltage_v')))
            if quality_bad and (not injected or bad_patch):
                self._incident(module, 'DATA_QUALITY', 'UNKNOWN', None, None, stamp, injected,
                               f'{module} has missing, invalid, or unverified sensor data')
            elif not quality_bad:
                self._close((module, 'DATA_QUALITY'), stamp)
            if not injected:
                points = [(t, r.get('temp_c')) for t, r in self.windows[module]]
                rate = slope(points)
                key = (module, 'TEMPERATURE_RISE')
                state = self.counts.setdefault(key, {'warning': 0, 'critical': 0, 'clear': 0})
                state['warning'] = state['warning'] + 1 if rate is not None and rate >= RISE_WARNING else 0
                state['critical'] = state['critical'] + 1 if rate is not None and temp is not None and temp >= RISE_CRITICAL_TEMP and rate >= RISE_CRITICAL else 0
                severity = 'CRITICAL' if state['critical'] >= RISE_WINDOWS else 'WARNING' if state['warning'] >= RISE_WINDOWS else None
                if key in self.active and self.active[key]['severity'] == 'CRITICAL':
                    severity = 'CRITICAL'
                if severity:
                    self._incident(module, 'TEMPERATURE_RISE', severity, round(rate, 3) if rate is not None else None, RISE_CRITICAL if severity == 'CRITICAL' else RISE_WARNING, stamp,
                                   message=f'{module} sustained temperature rise: {rate:.2f} °C/min' if rate is not None else f'{module} sustained temperature rise')
                state['clear'] = state['clear'] + 1 if rate is not None and rate < RISE_RECOVERY else 0
                if state['clear'] >= RECOVERY_FRAMES:
                    self._close(key, stamp)
        if not injected:
            # Evaluate after every module has received the same frame.
            for module in MODULES:
                for field in ('temp_c', 'voltage_v'):
                    key = (module, 'SUSPECTED_STUCK_' + field.upper())
                    if frozen(self.windows, module, field):
                        self._incident(module, key[1], 'UNKNOWN', self.readings[module][field], None, stamp,
                                       message=f'{module} {field} may be stuck; peer channels are changing')
                    else:
                        self._close(key, stamp)
            self.times.append(stamp)
            self.elapsed.append(event.get('elapsed_s'))
            for module in MODULES:
                for field in ('temp_c', 'voltage_v'):
                    self.chart[module][field].append(self.readings[module].get(field))
            if len(self.times) > JOURNAL_LIMIT:
                self.times.pop(0)
                self.elapsed.pop(0)
                for values in self.chart.values():
                    for series in values.values():
                        series.pop(0)

    def consume(self, batch):
        if self.session_id is not None and batch.get('session_id') != self.session_id:
            self._reset()
            self.sequence = -1
        self.session_id = batch.get('session_id', self.session_id)
        if batch.get('gap') and batch.get('oldest_seq', 0) > self.sequence + 1:
            events = batch.get('events', [])
            if events and self.run_id is not None and events[0]['run_id'] != self.run_id and events[0]['kind'] != 'RUN_START':
                for key in list(self.active):
                    self.active[key]['close_reason'] = 'delivery_gap'
                    self._close(key, self.source_time)
                self._reset()
            self.times = []
            self.elapsed = []
            self.chart = {m: {'temp_c': [], 'voltage_v': []} for m in MODULES}
            self.windows = {m: [] for m in MODULES}
            self.counts = {}
            self.continuity_gap = True
        for event in batch.get('events', []):
            if event['seq'] <= self.sequence:
                continue
            self.run_id = event['run_id']
            self._process(event)
            self.sequence = event['seq']
            self.updated_at = utcnow()
        self.playback = batch.get('metadata', self.playback)
        return self.snapshot()

    def acknowledge(self, alert_id):
        for alert in self.active.values():
            if alert['id'] == alert_id:
                alert['acknowledged'] = True
                return self.snapshot()
        raise ValueError('Alert not found')

    def snapshot(self):
        alerts = list(self.active.values())
        severities = {a['severity'] for a in alerts}
        temperatures = [r['temp_c'] for r in self.readings.values() if r['temp_c'] is not None]
        voltages = [r['voltage_v'] for r in self.readings.values() if r['voltage_v'] is not None]
        soc = [r['soc_pct'] for r in self.readings.values() if r['soc_pct'] is not None]
        soh = [r['soh_pct'] for r in self.readings.values() if r['soh_pct'] is not None]
        voltage_spread = max(voltages) - min(voltages) if voltages else None
        spread_overflow = voltage_spread is not None and not math.isfinite(voltage_spread)
        if spread_overflow:
            voltage_spread = None
        severity = 'CRITICAL' if 'CRITICAL' in severities else 'WARNING' if 'WARNING' in severities else 'UNKNOWN' if 'UNKNOWN' in severities or len(temperatures) < 12 or len(voltages) < 12 else 'NORMAL'
        priority = next((a for level in ('CRITICAL', 'WARNING', 'UNKNOWN') for a in alerts if a['severity'] == level), None)
        modules = []
        for module, reading in self.readings.items():
            local = [a['severity'] for a in alerts if a['module_id'] == module]
            level = 'CRITICAL' if 'CRITICAL' in local else 'WARNING' if 'WARNING' in local else 'UNKNOWN' if 'UNKNOWN' in local or reading['temp_c'] is None or reading['voltage_v'] is None else 'NORMAL'
            modules.append({**deepcopy(reading), 'severity': level})
        thermal = [a['module_id'] for a in alerts if a['type'] in ('TEMPERATURE', 'TEMPERATURE_RISE') and a['severity'] in ('WARNING', 'CRITICAL')]
        return {'bus_id': self.bus_id, 'session_id': self.session_id, 'run_id': self.run_id, 'event_sequence': self.sequence,
                'source_time': self.source_time, 'severity': severity,
                'driver_message': (priority['message'] + '. Check battery status.' if priority else 'Battery status normal.' if severity == 'NORMAL' else 'Battery health unknown. Check sensor data.'),
                'modules': modules, 'alerts': deepcopy(alerts), 'recent_alerts': deepcopy(list(self.recent)),
                'summary': {'max_temp': max(temperatures) if temperatures else None, 'min_voltage': min(voltages) if voltages else None,
                            'soc': min(soc) if soc else None, 'soh': min(soh) if soh else None, 'coverage': len(temperatures) / 12,
                            'voltage_spread': voltage_spread, 'voltage_spread_quality': 'overflow' if spread_overflow else 'ok' if voltages else 'missing'},
                'playback': deepcopy(self.playback), 'link_state': 'LIVE', 'updated_at': self.updated_at,
                'correlated_heating': sorted(set(thermal)) if len(set(thermal)) >= 2 else [],
                'continuity_gap': getattr(self, 'continuity_gap', False),
                'warming_up': getattr(self, 'continuity_gap', False) and len(self.times) < WINDOW_SIZE}

    def history(self):
        return {'timestamps': list(self.times), 'elapsed': list(self.elapsed), 'modules': deepcopy(self.chart),
                'history_complete': not getattr(self, 'continuity_gap', False)}
