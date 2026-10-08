from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .prompt import PromptConfig, inject_messages


@dataclass(frozen=True)
class ProxyConfig:
    prompt: PromptConfig
    upstream_url: str
    upstream_api_key: str | None = field(default=None, repr=False)
    upstream_model: str | None = None
    timeout: float = 120.0
    max_body_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        parts = urlsplit(self.upstream_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("upstream URL must be an absolute HTTP(S) chat-completions endpoint")
        if parts.username or parts.password or parts.fragment:
            raise ValueError("put upstream credentials in the API-key environment variable")
        if self.timeout <= 0 or self.max_body_bytes <= 0:
            raise ValueError("timeout and body size limit must be positive")


def make_server(config: ProxyConfig, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        # Close-delimited responses let SSE pass through without parsing its contents.
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            # Request bodies, prompts, keys, and upstream response content are not logged.
            pass

        def error_json(self, status: int, message: str) -> None:
            data = json.dumps({"error": {"message": message}}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.error_json(404, "not found")
                return
            data = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self.error_json(404, "not found")
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("send requests with Content-Length")
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise ValueError("Content-Length is required")
                length = int(raw_length)
                if length <= 0:
                    raise ValueError("request body must be nonempty")
                if length > config.max_body_bytes:
                    self.error_json(413, "request body too large")
                    return
                self.connection.settimeout(config.timeout)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("incomplete request body")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                payload["messages"] = inject_messages(payload.get("messages"), config.prompt)
                if config.upstream_model:
                    payload["model"] = config.upstream_model
                headers = {"Content-Type": "application/json"}
                authorization = (
                    "Bearer " + config.upstream_api_key if config.upstream_api_key
                    else self.headers.get("Authorization")
                )
                if authorization:
                    headers["Authorization"] = authorization
                request = Request(config.upstream_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            except (ValueError, TypeError, UnicodeError) as exc:
                # JSONDecodeError.__str__ never includes the submitted body.
                self.error_json(400, str(exc))
                return
            except TimeoutError:
                self.error_json(408, "request body timed out")
                return
            try:
                response = urlopen(request, timeout=config.timeout)
            except HTTPError as exc:
                exc.close()
                # Upstream errors may echo the injected system prompt; do not relay them.
                self.error_json(exc.code, "upstream Judge rejected the request")
                return
            except (URLError, TimeoutError, OSError):
                self.error_json(502, "upstream Judge unavailable")
                return
            try:
                with response:
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    # No score extraction or modification. The Judge applies the prompt.
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (OSError, TimeoutError):
                self.close_connection = True

    return ThreadingHTTPServer((host, port), Handler)
