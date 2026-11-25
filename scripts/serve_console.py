"""Serve the console prototype while it is being developed.

The console is static files (console/), so any web server will do. This one adds a single
header, `Cache-Control: no-store`: a browser otherwise keeps JavaScript modules it has already
loaded, and an edited file is silently mixed with stale ones. It listens on this machine only.

When the guard is served over HTTP (FR07), the same files are served by that application and
this script is no longer needed.

Usage:
    uv run python scripts/serve_console.py          # http://localhost:8765
    uv run python scripts/serve_console.py 9000
"""

import functools
import http.server
import sys
from pathlib import Path

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=str(CONSOLE_DIR))
    print(f"console on http://localhost:{port}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
