"""Integration tests use four real Uvicorn processes, not mocked HTTP."""
import concurrent.futures
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = 'integration-test-key'

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

@pytest.fixture
def lab():
    ports = []
    while len(ports) < 4:
        port = free_port()
        if port not in ports: ports.append(port)
    processes = []
    env = dict(os.environ, ALERT_EMAIL_ENABLED='false', CONTROL_KEY=KEY, RATE_WINDOW='0.5', BREAKER_TIMEOUT='0.8', UPSTREAM_TIMEOUT='0.2', SERVICE_URLS=','.join(f'http://127.0.0.1:{p}' for p in ports[:3]))
    with httpx.Client(timeout=3, trust_env=False) as client:
        try:
            for i, port in enumerate(ports):
                module = 'app.service:app' if i < 3 else 'app.gateway:app'
                processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', str(port), '--log-level', 'error'], env=dict(env, INSTANCE_NAME=f'order-service-{i+1}'), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                for attempt in range(100):
                    try:
                        if client.get(f'http://127.0.0.1:{port}/health').status_code == 200: break
                    except httpx.HTTPError: pass
                    if processes[-1].poll() is not None: raise RuntimeError('Service failed to start')
                    time.sleep(.05)
                else: raise RuntimeError('Startup timeout')
            base = f'http://127.0.0.1:{ports[3]}'
            yield client, base, processes
        finally:
            for process in processes:
                if process.poll() is None: process.terminate()
            for process in processes:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()

def set_fault(client, base, index, fail=False, delay=0):
    response = client.put(f'{base}/api/instances/{index}/fault', headers={'X-Control-Key': KEY}, json={'fail': fail, 'delay_ms': delay})
    assert response.status_code == 200

def test_frontend_docs_and_round_robin(lab):
    c,b,_=lab
    assert 'fetch' in c.get(b).text
    assert c.get(b+'/docs').status_code==200
    responses=[c.get(b+'/api/orders') for _ in range(3)]
    assert [r.status_code for r in responses]==[200]*3
    assert [r.json()['instance'] for r in responses]==[f'order-service-{i}' for i in (1,2,3)]
    assert responses[0].json()['data']['orders'][0]['id']=='ORD-001'

def test_concurrent_rate_limit(lab):
    c,b,_=lab
    for i in range(3): set_fault(c,b,i)
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        responses=list(pool.map(lambda _: c.get(b+'/api/orders'),range(15)))
    assert sum(r.status_code==200 for r in responses)==6
    assert sum(r.status_code==429 for r in responses)==9
    assert all('Retry-After' in r.headers for r in responses if r.status_code==429)
    time.sleep(.55)
    assert c.get(b+'/api/orders').status_code==200

def open_all(c,b):
    for i in range(3): set_fault(c,b,i,True)
    for batch in range(3):
        assert all(c.get(b+'/api/orders').status_code==500 for _ in range(3))
        if batch==1:time.sleep(.55)
    assert all(i['state']=='open' for i in c.get(b+'/api/state').json()['instances'])

def test_breaker_open_skip_and_recovery(lab):
    c,b,_=lab
    open_all(c,b)
    assert c.get(b+'/api/orders').status_code==503
    for i in range(3):set_fault(c,b,i)
    time.sleep(.85)
    assert all(c.get(b+'/api/orders').status_code==200 for _ in range(3))
    assert all(i['state']=='closed' for i in c.get(b+'/api/state').json()['instances'])

def test_half_open_failed_probe_reopens(lab):
    c,b,_=lab
    open_all(c,b)
    time.sleep(.85)
    assert c.get(b+'/api/orders').status_code==500
    assert c.get(b+'/api/state').json()['instances'][0]['state']=='open'

def test_timeout(lab):
    c,b,_=lab
    set_fault(c,b,0,delay=500)
    r=c.get(b+'/api/orders')
    assert r.status_code==504
    assert r.json()['instance']=='order-service-1'

def test_stopped_service(lab):
    c,b,processes=lab
    processes[0].terminate();processes[0].wait(timeout=5)
    assert c.get(b+'/api/orders').status_code==502
    assert c.get(b+'/api/orders').status_code==200

def test_control_auth_and_validation(lab):
    c,b,_=lab
    assert c.put(b+'/api/instances/0/fault',json={'fail':True}).status_code==401
    assert c.put(b+'/api/instances/0/fault',headers={'X-Control-Key':KEY},json={'delay_ms':-1}).status_code==422
    assert c.put(b+'/api/instances/99/fault',headers={'X-Control-Key':KEY},json={'fail':True}).status_code==404

def test_half_open_single_probe(lab):
    c,b,_=lab
    open_all(c,b)
    for i in range(3):set_fault(c,b,i,delay=150)
    time.sleep(.85)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        responses=list(pool.map(lambda _:c.get(b+'/api/orders'),range(6)))
    assert sum(r.status_code==200 for r in responses)==3
    assert sum(r.status_code==503 for r in responses)==3
    assert all(i['state']=='closed' for i in c.get(b+'/api/state').json()['instances'])
