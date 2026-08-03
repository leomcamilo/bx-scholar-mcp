"""Optional HTTP (streamable-http) transport for the MCP servers.

Selected at runtime via ``BX_SCHOLAR_TRANSPORT=streamable-http``; the default
stays ``stdio`` so nothing changes for existing stdio consumers.

Security posture (this is a network surface, unlike stdio):
- A bearer token is REQUIRED — the server refuses to start over HTTP without
  ``BX_SCHOLAR_TOKEN``. Every request must send ``Authorization: Bearer <token>``.
- Binds to ``127.0.0.1`` by default (``BX_SCHOLAR_HOST``); FastMCP auto-enables
  DNS-rebinding protection for loopback hosts.
- The token compare is constant-time (``hmac.compare_digest``).
"""

from __future__ import annotations

import hmac
import os
import sys

from mcp.server.fastmcp import FastMCP

_HTTP_TRANSPORTS = ("streamable-http", "http", "sse")


def should_serve_http() -> bool:
    return os.environ.get("BX_SCHOLAR_TRANSPORT", "stdio").strip().lower() in _HTTP_TRANSPORTS


def serve_http(server: FastMCP) -> None:
    """Serve ``server`` over streamable-http behind a bearer-token gate."""
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    token = os.environ.get("BX_SCHOLAR_TOKEN", "").strip()
    if not token:
        print(
            "[FATAL] BX_SCHOLAR_TOKEN is required to serve over HTTP", file=sys.stderr
        )
        raise SystemExit(1)

    expected = f"Bearer {token}"

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            provided = request.headers.get("authorization", "")
            if not hmac.compare_digest(provided, expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app = server.streamable_http_app()
    app.add_middleware(_BearerAuth)

    uvicorn.run(
        app,
        host=server.settings.host,
        port=server.settings.port,
        log_level=os.environ.get("BX_SCHOLAR_LOG_LEVEL", "info"),
    )
