#!/usr/bin/env bash
# LightBrain — launch web dashboard
#
# Usage:
#   ./launch.sh              # synthetic audio (demo), port 8765
#   ./launch.sh --device 1   # real mic on device 1
#   ./launch.sh --port 8080  # custom port

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8765
EXTRA_ARGS=()

# Parse --port / --device / --mode overrides from this script's args
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)   PORT="$2"; shift 2 ;;
        --device) EXTRA_ARGS+=("--device" "$2"); shift 2 ;;
        --mode)   EXTRA_ARGS+=("--mode" "$2"); shift 2 ;;
        *)        EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# Activate virtual environment if one exists alongside this script
if   [ -f ".venv/bin/activate" ];    then source .venv/bin/activate
elif [ -f "venv/bin/activate" ];     then source venv/bin/activate
fi

# If no --device arg was passed, run in demo (synthetic audio) mode
DEMO_FLAG="--demo"
for a in "${EXTRA_ARGS[@]}"; do
    [[ "$a" == "--device" ]] && DEMO_FLAG="" && break
done

echo ""
echo "  ██╗     ██╗ ██████╗ ██╗  ██╗████████╗██████╗ ██████╗  █████╗ ██╗███╗   ██╗"
echo "  ██║     ██║██╔════╝ ██║  ██║╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██║████╗  ██║"
echo "  ██║     ██║██║  ███╗███████║   ██║   ██████╔╝██████╔╝███████║██║██╔██╗ ██║"
echo "  ██║     ██║██║   ██║██╔══██║   ██║   ██╔══██╗██╔══██╗██╔══██║██║██║╚██╗██║"
echo "  ███████╗██║╚██████╔╝██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║██║██║ ╚████║"
echo "  ╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝"
echo ""
echo "  Dashboard → http://localhost:$PORT"
[ -n "$DEMO_FLAG" ] && echo "  Mode     → Demo (synthetic audio — use --device N for real mic)"
echo ""

# Start LightBrain in the background so we can poll for readiness
python -m app.main $DEMO_FLAG --web --web-port "$PORT" "${EXTRA_ARGS[@]}" &
APP_PID=$!

# Wait up to 10 s for the server to respond before opening the browser
echo "  Starting server..."
READY=0
for i in $(seq 1 20); do
    sleep 0.5
    if curl -sf "http://localhost:$PORT/" > /dev/null 2>&1 ||
       python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$PORT/')" > /dev/null 2>&1; then
        READY=1
        break
    fi
done

if [ $READY -eq 1 ]; then
    echo "  Server ready — opening browser"
else
    echo "  Server did not respond after 10 s — opening browser anyway"
fi

# Open browser (try xdg-open, macOS open, then Python fallback)
URL="http://localhost:$PORT"
if   command -v xdg-open > /dev/null 2>&1; then xdg-open "$URL" &
elif command -v open     > /dev/null 2>&1; then open     "$URL"
else python3 -m webbrowser "$URL"
fi

echo "  Running — press Ctrl+C to stop"
echo ""

# Bring app back to foreground so Ctrl+C cleanly stops it
wait $APP_PID
