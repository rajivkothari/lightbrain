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

# Commands the iPad is allowed to send. Reject anything outside this set so
# an attacker who can reach the port cannot inject arbitrary engine state.
_ALLOWED_TYPES = frozenset({
    "mode", "set_mode", "scene", "activate_scene", "release_scene",
    "blackout", "strobe_master", "set_fader", "momentary",
    "toggle_kill", "fixture_test", "release_fixture_test",
    "fixture_test_aim", "aim_fixture", "save_position",
})
_MAX_QUEUE = 64          # drop commands when the engine is behind

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


def _build_app(token: str = "") -> "FastAPI":
    _token = token
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
        # Optional token check (app_config.json "web_server_token": "secret")
        token = _token  # captured from build_app closure below
        if token:
            q_token = ws.query_params.get("token", "")
            if q_token != token:
                await ws.close(code=4403)
                return

        await _mgr.connect(ws)
        await ws.send_text(json.dumps(dict(_engine_state)))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    cmd = json.loads(raw)
                    if not isinstance(cmd, dict):
                        continue
                    if cmd.get("type") not in _ALLOWED_TYPES:
                        continue
                    if _command_queue.qsize() < _MAX_QUEUE:
                        _command_queue.put_nowait(cmd)
                except (json.JSONDecodeError, ValueError):
                    pass
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _mgr.disconnect(ws)

    return app


_thread: Optional[threading.Thread] = None


def start(host: str = "0.0.0.0", port: int = 8080, token: str = "") -> None:
    """Start the iPad controller server in a background daemon thread."""
    if not _AVAILABLE:
        print("[iPad] fastapi/uvicorn not installed — iPad controller disabled.")
        return
    global _thread
    cfg = uvicorn.Config(
        _build_app(token=token), host=host, port=port,
        log_level="warning", loop="asyncio",
    )
    srv = uvicorn.Server(cfg)
    _thread = threading.Thread(target=srv.run, daemon=True, name="lightbrain-ipad")
    _thread.start()
    disp = "localhost" if host == "0.0.0.0" else host
    token_note = f"  (token required: ?token=...)" if token else "  (no auth)"
    print(f"[iPad] Controller -> http://{disp}:{port}/{token_note}")
