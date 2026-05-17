#!/usr/bin/env python3
"""OpenAI-shaped hop: LiteLLM (client model) -> this -> LiteLLM (canonical model).

Forces stream=false when calling upstream so a minimal stdlib server suffices.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UPSTREAM = os.environ.get("UPSTREAM_BASE", "http://litellm:4000/v1").rstrip("/")
API_KEY = os.environ.get("UPSTREAM_API_KEY", "sk-local")
CANON = os.environ.get("UPSTREAM_MODEL", "qwen3.6-35b-heretic")
LISTEN = int(os.environ.get("LISTEN_PORT", "8088"))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/v1/models":
            self.send_error(404)
            return
        auth = self.headers.get("Authorization", f"Bearer {API_KEY}")
        req = Request(
            f"{UPSTREAM}/models",
            headers={"Authorization": auth},
            method="GET",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                data = resp.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            self._json(502, {"error": {"message": str(exc), "type": "relay_error"}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "invalid JSON body", "type": "invalid_request_error"}})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": {"message": "body must be a JSON object", "type": "invalid_request_error"}})
            return
        payload["model"] = CANON
        payload["stream"] = False
        body = json.dumps(payload).encode()
        auth = self.headers.get("Authorization", f"Bearer {API_KEY}")
        req = Request(
            f"{UPSTREAM}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth},
            method="POST",
        )
        try:
            with urlopen(req, timeout=600) as resp:
                out = resp.read()
        except HTTPError as exc:
            err_body = exc.read() or b"{}"
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            return
        except (URLError, TimeoutError, OSError) as exc:
            self._json(502, {"error": {"message": str(exc), "type": "relay_error"}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class _Server(ThreadingMixIn, ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    httpd = _Server(("0.0.0.0", LISTEN), _Handler)
    print(f"openai-stack-relay :{LISTEN} -> {UPSTREAM} (model={CANON})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
