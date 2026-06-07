#!/usr/bin/env bash
# LightBrain — launch web dashboard
#
# Automatically uses VB-Cable (CABLE Output) if installed.
# Falls back to demo (synthetic audio) if VB-Cable is not found.
#
# Usage:
#   ./launch.sh                  auto-detect VB-Cable or demo
#   ./launch.sh --device 1       force a specific device index
#   ./launch.sh --demo           force demo mode
#   ./launch.sh --mode dinner    start in a specific mode
#   ./launch.sh --port 8080      custom port
#
# To list all audio devices: python -m sounddevice

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8765
FORCE_DEVICE=""
FORCE_DEMO=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --port)   PORT="$2";           shift 2 ;;
        --device) FORCE_DEVICE="$2";   shift 2 ;;
        --demo)   FORCE_DEMO=1;        shift   ;;
        --mode)   EXTRA_ARGS+=("--mode" "$2"); shift 2 ;;
        *)        EXTRA_ARGS+=("$1");  shift   ;;
    esac
done

if   [ -f ".venv/bin/activate" ]; then source .venv/bin/activate
elif [ -f "venv/bin/activate"  ]; then source venv/bin/activate
fi

echo ""
echo "  LIGHTBRAIN"
echo "  Dashboard → http://localhost:$PORT"
echo ""

# Decide audio source
if [ -n "$FORCE_DEMO" ]; then
    AUDIO_FLAG="--demo"
    AUDIO_LABEL="Demo (synthetic audio)"

elif [ -n "$FORCE_DEVICE" ]; then
    AUDIO_FLAG="--device $FORCE_DEVICE"
    AUDIO_LABEL="Device $FORCE_DEVICE (forced)"

else
    # Auto-detect VB-Cable capture device.
    # Signal flow: DJ software → CABLE Input (playback) → CABLE Output (recording)
    # We read from CABLE Output, but also accept CABLE Input if it appears as a
    # capture device (naming varies between VB-Cable versions).
    VBCABLE_IDX=$(python3 -c "
import sounddevice as sd
devs = sd.query_devices()
for i, d in enumerate(devs):
    name = d['name'].lower()
    if any(k in name for k in ('cable output', 'cable input')) and d['max_input_channels'] > 0:
        print(i)
        break
" 2>/dev/null || true)

    if [ -n "$VBCABLE_IDX" ]; then
        AUDIO_FLAG="--device $VBCABLE_IDX"
        AUDIO_LABEL="VB-Cable (CABLE Output, device $VBCABLE_IDX)"
    else
        AUDIO_FLAG="--demo"
        AUDIO_LABEL="Demo (VB-Cable not found — install from vb-audio.com)"
    fi
fi

echo "  Audio     → $AUDIO_LABEL"
echo ""

# Start LightBrain in the background so we can open the browser after it's ready
# shellcheck disable=SC2086
python -m app.main $AUDIO_FLAG --web --web-port "$PORT" "${EXTRA_ARGS[@]}" &
APP_PID=$!

echo "  Starting server..."
READY=0
for i in $(seq 1 20); do
    sleep 0.5
    if curl -sf "http://localhost:$PORT/" > /dev/null 2>&1 ||
       python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$PORT/')" > /dev/null 2>&1; then
        READY=1; break
    fi
done

[ $READY -eq 1 ] && echo "  Server ready — opening browser" \
                 || echo "  Server did not respond in 10s — opening browser anyway"

URL="http://localhost:$PORT"
if   command -v xdg-open > /dev/null 2>&1; then xdg-open "$URL" &
elif command -v open     > /dev/null 2>&1; then open     "$URL"
else python3 -m webbrowser "$URL"
fi

echo "  Running — press Ctrl+C to stop"
echo ""

wait $APP_PID
