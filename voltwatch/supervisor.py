"""Start and stop only the child processes owned by this utility."""
import multiprocessing
import os
import threading
import time
from dataclasses import dataclass


@dataclass
class ChildSpec:
    role: str
    port: int
    utility_url: str
    session_id: str
    bus_id: str = ''
    name: str = ''
    route_index: int = 0


class StopFlag:
    def __init__(self, value):
        self.value = value

    def set(self):
        self.value.value = 1

    def wait(self, timeout):
        time.sleep(timeout)
        return bool(self.value.value)


@dataclass
class ChildHandle:
    spec: ChildSpec
    process: object
    stop_event: object


def _child_entry(spec, stop_event):
    if spec.role == 'bus':
        from voltwatch.tcu import serve_bus
        serve_bus(spec, stop_event)
    else:
        from voltwatch.city import serve_city
        serve_city(spec, stop_event)


def start_children(specs):
    ctx = multiprocessing.get_context('spawn')
    children = []
    try:
        for spec in specs:
            event = StopFlag(ctx.Value('b', 0, lock=False))
            process = ctx.Process(target=_child_entry, args=(spec, event), name=f'{spec.role}-{spec.port}')
            process.start()
            children.append(ChildHandle(spec, process, event))
    except BaseException:
        stop_children(children)
        raise
    return children


def stop_children(children, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    for child in children:
        child.stop_event.set()
    for child in children:
        child.process.join(max(0, deadline - time.monotonic()))
    for child in children:
        if child.process.is_alive():
            child.process.kill()
    for child in children:
        child.process.join(timeout=1)


def watch_owner(server, stop_event):
    parent = multiprocessing.parent_process()
    def watch():
        while not stop_event.wait(0.25):
            if parent is None or not parent.is_alive():
                server.should_exit = True
                time.sleep(3)
                os._exit(1)
        server.should_exit = True
        time.sleep(3)
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()
