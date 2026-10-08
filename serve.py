"""Serve the map locally: python3 serve.py  ->  http://localhost:8765"""
import functools, http.server, pathlib, sys

PORT = 8765
ROOT = pathlib.Path(__file__).resolve().parent
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

try:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
except OSError:
    sys.exit(f"Port {PORT} is already in use: the map is probably already being served at http://localhost:{PORT}")

print(f"Serving SSEN grid map at http://localhost:{PORT}  (Ctrl+C to stop)")
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
