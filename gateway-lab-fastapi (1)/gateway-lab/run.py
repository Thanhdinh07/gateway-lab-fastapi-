"""Start the gateway and three real HTTP services; Ctrl+C stops all children."""
import os
import subprocess
import sys
import time
from pathlib import Path

from app.config import load_env

if __name__ == '__main__':
    load_env()
    root = Path(__file__).resolve().parent
    children = []
    try:
        for port in (9001, 9002, 9003):
            env = dict(os.environ, INSTANCE_NAME=f'order-service-{port-9000}')
            children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.service:app', '--host', '127.0.0.1', '--port', str(port)], cwd=root, env=env))
        children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.gateway:app', '--host', '127.0.0.1', '--port', '8080'], cwd=root))
        print('\nGateway Lab: http://127.0.0.1:8080 | Swagger: /docs\n', flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None: p.terminate()
        for p in children:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
