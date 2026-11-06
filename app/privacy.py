"""Log request metadata only, including failures and streaming requests."""
import json
import logging
import os
import secrets
import time
import uuid


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Never format record.msg, args, exc_info or stack_info: all may contain input.
        data = {"level": record.levelname, "event": "runtime_log"}
        if record.name == "audit" and isinstance(record.msg, dict):
            data = {k: record.msg[k] for k in
                    ("event", "request_id", "endpoint", "status", "duration_ms")
                    if k in record.msg}
        return json.dumps(data, ensure_ascii=True)


class AuditMiddleware:
    def __init__(self, app):
        self.app = app
        self.token = os.environ["VLLM_API_KEY"].encode()
        if len(self.token) < 32:
            raise RuntimeError("API key must contain at least 32 characters")
        self.log = logging.getLogger("audit")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/health":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        start = time.perf_counter()
        status = 500
        # Never log raw URLs, headers, user-controlled IDs, request or response bodies.
        endpoint = path if path in ("/v1/chat/completions", "/v1/completions", "/v1/models") else "other"

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"")
            if not secrets.compare_digest(supplied, b"Bearer " + self.token):
                await tracked_send({"type": "http.response.start", "status": 401,
                                    "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
            await self.app(scope, receive, tracked_send)
        finally:
            self.log.info({"event": "request_completed", "request_id": request_id,
                           "endpoint": endpoint, "status": status,
                           "duration_ms": round((time.perf_counter() - start) * 1000, 2)})
