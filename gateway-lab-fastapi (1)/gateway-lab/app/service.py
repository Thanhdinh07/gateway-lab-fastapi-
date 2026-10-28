import asyncio
import os
import secrets
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import load_env

load_env()

app = FastAPI(title='Order Service')
name = os.getenv('INSTANCE_NAME', 'order-service-1')
key = os.getenv('CONTROL_KEY', 'local-demo-key')
mode = {'fail': False, 'delay_ms': 100}

class Fault(BaseModel):
    fail: bool = False
    delay_ms: int = Field(default=100, ge=0, le=10000)

@app.get('/health')
def health():
    return {'instance': name, **mode}

@app.put('/admin/fault')
def fault(body: Fault, x_control_key: str = Header(default='')):
    if not secrets.compare_digest(key, x_control_key):
        raise HTTPException(401, 'Invalid control key')
    mode.update(body.model_dump())
    return mode

@app.get('/orders')
async def orders():
    snapshot = mode.copy()
    await asyncio.sleep(snapshot['delay_ms'] / 1000)
    if snapshot['fail']:
        raise HTTPException(500, 'Injected service failure')
    return {'instance': name, 'orders': [{'id': 'ORD-001', 'product': 'Bàn phím', 'quantity': 1}]}
