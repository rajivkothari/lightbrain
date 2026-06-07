"""
LightBrain Web Dashboard server — Sprint 9.

Exposes a local web server alongside app.main:
  GET  /                    — dashboard HTML
  GET  /api/state           — current engine state snapshot (JSON)
  POST /api/command         — send a control command to the engine
  WS   /ws                  — live state stream pushed at ~10 fps
  GET  /api/scenes          — full scene list (for scene editor)
  POST /api/scenes          — create / update a scene JSON file
  DELETE /api/scenes/{id}   — delete a scene JSON file
  GET  /api/presets         — position + state presets (for editor dropdowns)

State is shared via a plain dict (_engine_state).  Commands flow via a
queue.Queue that app.main drains each frame.
"""

import asyncio
import json
import os
import queue as _queue
import re
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
# Shared state
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
    "scenes":         [],   # [{id, name, index}]
    "modes":          [],   # [{key, display_name}]
    "fixtures":       {},   # serialized RigVisualState (updated each frame)
    "impact_lane":    0.0,
    "room_lane":      0.0,
    "strobe_rate":    0.0,
    "strobe_master":  1.0,
    "master_dimmer":  1.0,
    "uplight_dimmer": 1.0,
    "test_mode":      False,
    "test_pattern":   "",
}

_command_queue: _queue.Queue = _queue.Queue()

# Paths used by the scene editor API
_paths: Dict[str, Any] = {
    "scenes_dir":     "",
    "positions_file": "",
    "states_file":    "",
    "scene_manager":  None,
}


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


def set_paths(
    scenes_dir:     str,
    positions_file: str,
    states_file:    str,
    scene_manager:  Any = None,
) -> None:
    """Configure file paths and scene manager reference for the editor API."""
    _paths["scenes_dir"]     = scenes_dir
    _paths["positions_file"] = positions_file
    _paths["states_file"]    = states_file
    _paths["scene_manager"]  = scene_manager


def _refresh_scene_catalog() -> None:
    """Rebuild the scene catalog in engine state after a scene is saved/deleted."""
    mgr = _paths.get("scene_manager")
    if mgr:
        _engine_state["scenes"] = [
            {"id": s.scene_id, "name": s.name, "index": i}
            for i, s in enumerate(mgr.list_scenes())
        ]


def get_command() -> Optional[dict]:
    """Pop one pending command, or None if the queue is empty."""
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


def serialize_rig_state(rig: Any) -> dict:
    """
    Convert a RigVisualState to a JSON-serializable dict for web state push.
    Called by app.main; accepts any object matching the RigVisualState interface.
    """
    return {
        "uplights": [
            {"id": u.fixture_id, "x": u.x, "y": u.y,
             "rgb": list(u.color_rgb), "brt": round(float(u.brightness), 3),
             "active": u.active}
            for u in rig.uplights
        ],
        "washes": [
            {"id": w.fixture_id, "x": w.x, "y": w.y,
             "rgb": list(w.color_rgb), "brt": round(float(w.brightness), 3),
             "radius": w.radius, "pulse": round(float(w.pulse_strength), 3),
             "active": w.active}
            for w in rig.washes
        ],
        "beams": [
            {"id": b.fixture_id, "x": b.x, "y": b.y,
             "rgb": list(b.color_rgb), "brt": round(float(b.brightness), 3),
             "angle": round(float(b.angle_degrees), 1),
             "length": b.length, "spread": b.spread, "active": b.active}
            for b in rig.beams
        ],
        "sparkles": [
            {"id": s.fixture_id, "x": s.x, "y": s.y,
             "rgb": list(s.color_rgb), "brt": round(float(s.brightness), 3),
             "amount": round(float(s.sparkle_amount), 3), "active": s.active}
            for s in rig.sparkles
        ],
        "impacts": [
            {"id": i.fixture_id, "x": i.x, "y": i.y,
             "brt": round(float(i.brightness), 3), "flash": i.flash_active,
             "active": i.active}
            for i in rig.impacts
        ],
        "ambient_warm": round(float(getattr(rig, "ambient_warm", 0.0)), 3),
    }


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
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _manager.disconnect(websocket)

    # --- Scene editor API ---

    @fastapi_app.get("/api/scenes")
    async def api_get_scenes() -> JSONResponse:
        scenes_dir = _paths.get("scenes_dir", "")
        if not scenes_dir or not os.path.exists(scenes_dir):
            return JSONResponse([])
        result = []
        for fname in sorted(os.listdir(scenes_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(scenes_dir, fname), encoding="utf-8") as f:
                    result.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
        return JSONResponse(result)

    @fastapi_app.post("/api/scenes")
    async def api_save_scene(request: Request) -> JSONResponse:
        data      = await request.json()
        scene_id  = data.get("scene_id", "").strip()
        if not scene_id or not re.match(r'^[a-zA-Z0-9_]+$', scene_id):
            return JSONResponse({"error": "invalid scene_id"}, status_code=400)
        scenes_dir = _paths.get("scenes_dir", "")
        if not scenes_dir:
            return JSONResponse({"error": "scenes_dir not configured"}, status_code=500)
        os.makedirs(scenes_dir, exist_ok=True)
        path = os.path.join(scenes_dir, f"{scene_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        mgr = _paths.get("scene_manager")
        if mgr:
            mgr.load_all()
        _refresh_scene_catalog()
        return JSONResponse({"ok": True})

    @fastapi_app.delete("/api/scenes/{scene_id}")
    async def api_delete_scene(scene_id: str) -> JSONResponse:
        if not re.match(r'^[a-zA-Z0-9_]+$', scene_id):
            return JSONResponse({"error": "invalid scene_id"}, status_code=400)
        scenes_dir = _paths.get("scenes_dir", "")
        if scenes_dir:
            path = os.path.join(scenes_dir, f"{scene_id}.json")
            if os.path.exists(path):
                os.remove(path)
            mgr = _paths.get("scene_manager")
            if mgr:
                mgr.load_all()
            _refresh_scene_catalog()
        return JSONResponse({"ok": True})

    @fastapi_app.get("/api/presets")
    async def api_presets() -> JSONResponse:
        result: Dict[str, dict] = {"positions": {}, "states": {}}
        pos_file = _paths.get("positions_file", "")
        if pos_file and os.path.exists(pos_file):
            try:
                with open(pos_file, encoding="utf-8") as f:
                    result["positions"] = json.load(f).get("positions", {})
            except (json.JSONDecodeError, OSError):
                pass
        st_file = _paths.get("states_file", "")
        if st_file and os.path.exists(st_file):
            try:
                with open(st_file, encoding="utf-8") as f:
                    result["states"] = json.load(f).get("states", {})
            except (json.JSONDecodeError, OSError):
                pass
        return JSONResponse(result)

    return fastapi_app


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start(host: str = "0.0.0.0", port: int = 8765) -> None:
    """Start the web server in a background daemon thread."""
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
