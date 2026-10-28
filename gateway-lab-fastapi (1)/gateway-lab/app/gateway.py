import asyncio
import math
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import load_env
from .alerts import EmailAlerts, Settings

load_env()

URLS = os.getenv('SERVICE_URLS', 'http://127.0.0.1:9001,http://127.0.0.1:9002,http://127.0.0.1:9003').split(',')
LIMIT = 6
WINDOW = float(os.getenv('RATE_WINDOW', '3'))
COOLDOWN = float(os.getenv('BREAKER_TIMEOUT', '6'))
TIMEOUT = float(os.getenv('UPSTREAM_TIMEOUT', '2'))
KEY = os.getenv('CONTROL_KEY', 'local-demo-key')
ROOT = Path(__file__).resolve().parents[1]

@asynccontextmanager
async def lifespan(app):
    email_settings = Settings.from_env()
    app.state.lock = asyncio.Lock()
    app.state.http = httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)
    app.state.timestamps = deque()
    app.state.events = deque(maxlen=400)
    app.state.sequence = 0
    app.state.pointer = 0
    app.state.stats = {'total': 0, 'success': 0, 'errors': 0}
    app.state.instances = [dict(id=i, name=f'order-service-{i+1}', url=url, state='closed', failures=0, opened=0., generation=0, probe=False, successCount=0, failCount=0, simulateFailure=False, delay_ms=100) for i, url in enumerate(URLS)]
    app.state.alerts = EmailAlerts(email_settings)
    app.state.alerts.start()
    try:
        yield
    finally:
        await app.state.alerts.close()
        await app.state.http.aclose()

app = FastAPI(title='Gateway Lab — real HTTP', lifespan=lifespan)

def event(message, kind='sys'):
    app.state.sequence += 1
    app.state.events.append({'id': app.state.sequence, 'time': time.strftime('%H:%M:%S'), 'message': message, 'type': kind})

def refresh():
    now = time.monotonic()
    while app.state.timestamps and now - app.state.timestamps[0] >= WINDOW:
        app.state.timestamps.popleft()
    for inst in app.state.instances:
        if inst['state'] == 'open' and now - inst['opened'] >= COOLDOWN:
            inst['state'] = 'half-open'
            inst['probe'] = False
            event(f"{inst['name']}: HALF-OPEN, chờ một request thử lại")

def control(key):
    if not secrets.compare_digest(KEY, key):
        raise HTTPException(401, 'Invalid control key')

@app.get('/', include_in_schema=False)
def index():
    return FileResponse(ROOT / 'static/index.html')

@app.get('/health')
def health():
    return {'status': 'ok', 'mode': 'real-http'}

@app.get('/api/state')
async def state():
    async with app.state.lock:
        refresh()
        return {'stats': app.state.stats.copy(), 'instances': [{k:v for k,v in i.items() if k not in ('url', 'opened', 'generation', 'probe')} for i in app.state.instances], 'events': list(app.state.events), 'rate_count': len(app.state.timestamps), 'rate_limit': LIMIT, 'rate_window': WINDOW}

class Fault(BaseModel):
    fail: bool = False
    delay_ms: int = Field(default=100, ge=0, le=10000)

@app.put('/api/instances/{instance_id}/fault')
async def fault(instance_id: int, body: Fault, x_control_key: str = Header(default='')):
    control(x_control_key)
    if not 0 <= instance_id < len(app.state.instances):
        raise HTTPException(404, 'Unknown instance')
    inst = app.state.instances[instance_id]
    try:
        response = await app.state.http.put(inst['url'] + '/admin/fault', json=body.model_dump(), headers={'X-Control-Key': KEY})
        response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, 'Cannot configure service')
    async with app.state.lock:
        inst.update(simulateFailure=body.fail, delay_ms=body.delay_ms)
        event(f"{inst['name']}: lỗi={body.fail}, độ trễ={body.delay_ms}ms")
    return {'ok': True}
    raise HTTPException(403, 'Chức năng gây lỗi đã bị tắt')
@app.get('/api/orders')
async def orders():
    async with app.state.lock:
        refresh()
        app.state.stats['total'] += 1
        rid = f"R{app.state.stats['total']}"
        if len(app.state.timestamps) >= LIMIT:
            app.state.stats['errors'] += 1
            event(f'{rid}: 429 — vượt giới hạn {LIMIT} request/{WINDOW:g}s', 'err')
            retry = max(1, math.ceil(WINDOW - (time.monotonic() - app.state.timestamps[0])))
            return JSONResponse({'request_id': rid, 'detail': 'Rate limit exceeded'}, status_code=429, headers={'Retry-After': str(retry)})
        app.state.timestamps.append(time.monotonic())
        inst = None
        for offset in range(len(URLS)):
            idx = (app.state.pointer + offset) % len(URLS)
            candidate = app.state.instances[idx]
            if candidate['state'] == 'open' or candidate['probe']:
                continue
            inst = candidate
            app.state.pointer = (idx + 1) % len(URLS)
            break
        if inst is None:
            app.state.stats['errors'] += 1
            event(f'{rid}: 503 — không có instance khả dụng', 'err')
            return JSONResponse({'request_id': rid, 'detail': 'No available instance'}, status_code=503)
        trial = inst['state'] == 'half-open'
        if trial:
            inst['probe'] = True
        generation = inst['generation']
        event(f"{rid}: chuyển tới {inst['name']}" + (' · HALF-OPEN probe' if trial else ''))
    started = time.monotonic()
    try:
        response = await app.state.http.get(inst['url'] + '/orders')
        code = response.status_code
        payload = response.json()
    except httpx.TimeoutException:
        code, payload = 504, {'detail': 'Upstream timeout'}
    except (httpx.HTTPError, ValueError):
        code, payload = 502, {'detail': 'Upstream unavailable'}
    elapsed = round((time.monotonic() - started) * 1000)
    failed = code >= 500
    async with app.state.lock:
        app.state.stats['errors' if code >= 400 else 'success'] += 1
        inst['failCount' if code >= 400 else 'successCount'] += 1
        # Ignore stale in-flight results for breaker transitions after an OPEN.
        if generation == inst['generation']:
            if failed:
                inst['failures'] += 1
                if trial or inst['failures'] >= 3:
                    inst.update(state='open', opened=time.monotonic(), probe=False, generation=generation+1)
                    app.state.alerts.submit(
                        f"circuit:{inst['id']}",
                        f"[Gateway Lab] {inst['name']} - Circuit OPEN",
                        f"Thời gian UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}\n"
                        f"Service: {inst['name']}\nRequest: {rid}\nHTTP: {code}\n"
                        f"Số lỗi liên tiếp: {inst['failures']}\n"
                        f"Gateway tạm ngắt service trong {COOLDOWN:g}s.\n"
                        "Kiểm tra kết nối, log service và cấu hình gây lỗi/độ trễ."
                    )
                    event(f"{inst['name']}: OPEN — ngừng chuyển request trong {COOLDOWN:g}s", 'err')
            else:
                inst.update(state='closed', failures=0, probe=False)
                if trial:
                    event(f"{inst['name']}: CLOSED — đã phục hồi", 'ok')
        event(f"{rid}: {code} từ {inst['name']} · {elapsed}ms", 'err' if code >= 400 else 'ok')
    return JSONResponse({'request_id': rid, 'instance': inst['name'], 'elapsed_ms': elapsed, 'data': payload}, status_code=code)


@app.get('/api/alerts/status')
async def alert_status(x_control_key: str = Header(default='')):
    control(x_control_key)
    return app.state.alerts.status()

@app.post('/api/alerts/test', status_code=202)
async def alert_test(x_control_key: str = Header(default='')):
    control(x_control_key)
    result = app.state.alerts.submit('test', '[Gateway Lab] Kiểm tra email', 'Đây là email kiểm tra do quản trị viên yêu cầu từ Gateway Lab. Không phải báo lỗi service.')
    if result == 'disabled':
        raise HTTPException(409, 'Email disabled; configure .env and restart')
    if result == 'suppressed':
        raise HTTPException(429, 'Test email is pending or within cooldown')
    if result == 'full':
        raise HTTPException(503, 'Email queue full')
    return {'status': 'queued', 'detail': 'Queued only, not proof of inbox delivery. Check /api/alerts/status.'}
