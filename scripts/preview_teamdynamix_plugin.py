"""Serve only the app and a synthetic MCP host for local UI review; no credentials."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "/": ROOT / "tests/fixtures/plugin_host.html",
    "/widget": ROOT / "src/dynamix_manager/plugin_app.html",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = ROUTES.get(self.path)
        if path is None:
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8769), Handler).serve_forever()
