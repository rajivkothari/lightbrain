"""
LightBrain Web Dashboard server — Sprint 8.

Exposes a local web server alongside app.main:
  GET  /            — dashboard HTML
  GET  /api/state   — current engine state snapshot (JSON)
  POST /api/command — send a control command to the engine
  WS   /ws          — live state stream pushed at ~10 fps

State is shared via a plain dict (_engine_state).  Commands flow via a
queue.Queue that app.main drains each frame.  Both are GIL-safe for the
simple scalar types used here.
"""

import asyncio
import json
import os
import queue as _queue
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Shared state — written by app.main, read by FastAPI handlers
# ---------------------------------------------------------------------------

_engine_state: Dict[str, Any] = {
    "mode":           "open_dance",
    "mode_display":   "Open Dance",
    "scene":          None,
    "scene_name":     "",
    "blackout":       False,
    "bpm":            0.0,
    "beat":           False,
    "low_energy":     0.0,
    "mid_energy":     0.0,
    "high_energy":    0.0,
    "overall_energy": 0.0,
    "fps":            0,
    "dmx_output":     "MOCK",
    "scenes":         [],   # [{id, name, index}] — populated once at startup
    "modes":          [],   # [{key, display_name}]  — populated once at startup
}

_command_queue: _queue.Queue = _queue.Queue()


def update_state(**kwargs: Any) -> None:
    """Called by app.main each frame to push fresh engine state."""
    _engine_state.update(kwargs)


def set_catalog(
    modes:  List[Dict[str, str]],
    scenes: List[Dict[str, Any]],
) -> None:
    """Populate the static mode and scene lists (call once before loop starts)."""
    _engine_state["modes"]  = modes
    _engine_state["scenes"] = scenes


def get_command() -> Optional[dict]:
    """Pop one pending command, or return None if the queue is empty."""
    try:
        return _command_queue.get_nowait()
    except _queue.Empty:
        return None


def get_all_commands() -> List[dict]:
    """Drain and return all pending commands at once."""
    cmds: List[dict] = []
    while True:
        try:
            cmds.append(_command_queue.get_nowait())
        except _queue.Empty:
            break
    return cmds


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class _ConnectionManager:
    def __init__(self) -> None:
        self.active: list = []

    async def connect(self, ws: Any) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: Any) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        msg  = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = _ConnectionManager()


async def _broadcast_loop() -> None:
    """Push a state snapshot to all connected WebSocket clients at ~10 fps."""
    while True:
        await asyncio.sleep(0.1)
        if _manager.active:
            await _manager.broadcast(dict(_engine_state))


@asynccontextmanager
async def _lifespan(app: "FastAPI"):  # type: ignore[override]
    task = asyncio.create_task(_broadcast_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

def _build_app() -> "FastAPI":
    fastapi_app = FastAPI(title="LightBrain Dashboard", lifespan=_lifespan)

    @fastapi_app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
        with open(html_path, encoding="utf-8") as f:
            return f.read()

    @fastapi_app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(dict(_engine_state))

    @fastapi_app.post("/api/command")
    async def api_command(request: Request) -> dict:
        cmd = await request.json()
        _command_queue.put_nowait(cmd)
        return {"ok": True}

    @fastapi_app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await _manager.connect(websocket)
        await websocket.send_text(json.dumps(dict(_engine_state)))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            _manager.disconnect(websocket)

    return fastapi_app


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start(host: str = "0.0.0.0", port: int = 8765) -> None:
    """
    Start the web server in a background daemon thread.

    Safe to call even when fastapi/uvicorn are not installed — prints a
    helpful install hint and returns without raising.
    """
    if not _FASTAPI_AVAILABLE:
        print("[Web] fastapi / uvicorn not installed — dashboard disabled.")
        print("[Web] Install with:  pip install fastapi 'uvicorn[standard]'")
        return

    global _server_thread

    config = uvicorn.Config(
        _build_app(),
        host=host,
        port=port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    _server_thread = threading.Thread(
        target=server.run,
        daemon=True,
        name="lightbrain-web",
    )
    _server_thread.start()
    display_host = "localhost" if host == "0.0.0.0" else host
    print(f"[Web] Dashboard → http://{display_host}:{port}/")
