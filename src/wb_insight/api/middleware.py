"""Request correlation and timing middleware."""

from __future__ import annotations

from time import perf_counter
from uuid import UUID, uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_REQUEST_ID_HEADER = "X-Request-ID"
_PROCESS_TIME_HEADER = "X-Process-Time-Ms"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a UUID request id and elapsed-time headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER)
        request.state.request_id = _valid_request_id(incoming) or str(uuid4())
        request.state.started_at = perf_counter()

        response = await call_next(request)
        elapsed = max((perf_counter() - request.state.started_at) * 1000, 0.0)
        response.headers[_REQUEST_ID_HEADER] = request.state.request_id
        response.headers[_PROCESS_TIME_HEADER] = f"{elapsed:.3f}"
        return response


def _valid_request_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None
