"""
iPad PWA controller — FastAPI + WebSocket server.

Shares the engine state dict and command queue with the main
dashboard server (app.web.server).  Pushes state at 15 Hz and
accepts commands from the iPad over the same WebSocket.
"""

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

from app.web.server import _engine_state, _command_queue

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class _ConnMgr:
    def __init__(self) -> None:
        self.active: list = []

    async def connect(self, ws: "WebSocket") -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: "WebSocket") -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        msg = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_mgr = _ConnMgr()
_PUSH_HZ = 15


async def _push_loop() -> None:
    interval = 1.0 / _PUSH_HZ
    while True:
        await asyncio.sleep(interval)
        if _mgr.active:
            await _mgr.broadcast(dict(_engine_state))


@asynccontextmanager
async def _lifespan(app: "FastAPI"):  # type: ignore[override]
    task = asyncio.create_task(_push_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _build_app() -> "FastAPI":
    app = FastAPI(title="LightBrain iPad", lifespan=_lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        p = os.path.join(os.path.dirname(__file__), "ipad.html")
        with open(p, encoding="utf-8") as f:
            return f.read()

    @app.get("/manifest.json")
    async def manifest() -> JSONResponse:
        return JSONResponse({
            "name": "LightBrain",
            "short_name": "LB",
            "start_url": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#080810",
            "theme_color": "#080810",
            "icons": [],
        })

    @app.websocket("/ws")
    async def ws_endpoint(ws: "WebSocket") -> None:
        await _mgr.connect(ws)
        await ws.send_text(json.dumps(dict(_engine_state)))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    cmd = json.loads(raw)
                    _command_queue.put_nowait(cmd)
                except (json.JSONDecodeError, ValueError):
                    pass
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _mgr.disconnect(ws)

    return app


_thread: Optional[threading.Thread] = None


def start(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the iPad controller server in a background daemon thread."""
    if not _AVAILABLE:
        print("[iPad] fastapi/uvicorn not installed — iPad controller disabled.")
        return
    global _thread
    cfg = uvicorn.Config(
        _build_app(), host=host, port=port,
        log_level="warning", loop="asyncio",
    )
    srv = uvicorn.Server(cfg)
    _thread = threading.Thread(target=srv.run, daemon=True, name="lightbrain-ipad")
    _thread.start()
    disp = "localhost" if host == "0.0.0.0" else host
    print(f"[iPad] Controller -> http://{disp}:{port}/")
