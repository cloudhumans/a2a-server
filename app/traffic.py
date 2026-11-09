import json
import time

from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx


MAX_BODY_BYTES = 64 * 1024
LOGGED_PREFIXES = ('/a2a', '/.well-known')


class TrafficLog:
    def __init__(self, capacity: int, sensitive_headers: set[str]) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._next_id = 1
        self._version = 0
        self._sensitive = {name.lower() for name in sensitive_headers}

    @property
    def version(self) -> int:
        return self._version

    def record(self, **fields: Any) -> dict[str, Any]:
        self._version += 1
        entry = {
            'id': self._next_id,
            'version': self._version,
            'at': datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        self._next_id += 1
        self._entries.append(entry)
        return entry

    def update(self, entry: dict[str, Any], **fields: Any) -> None:
        self._version += 1
        entry.update(fields, version=self._version)

    def changed_since(self, version: int) -> list[dict[str, Any]]:
        return [entry for entry in self._entries if entry['version'] > version]

    def clear(self) -> None:
        self._entries.clear()
        self._version += 1

    def mask(self, headers: list[tuple[str, str]]) -> dict[str, str]:
        masked = {}
        for name, value in headers:
            lower = name.lower()
            if lower in self._sensitive:
                scheme, _, secret = value.partition(' ')
                masked[lower] = f'{scheme} ••••' if secret else '••••'
            else:
                masked[lower] = value
        return masked


def _truncate(raw: bytes) -> str:
    text = raw[:MAX_BODY_BYTES].decode('utf-8', errors='replace')
    if len(raw) > MAX_BODY_BYTES:
        text += f'\n… truncated ({len(raw)} bytes total)'
    return text


def _last_sse_payload(text: str) -> str:
    payloads = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
    return payloads[-1] if payloads else text


def _find_task_state(node: Any) -> tuple[str | None, str | None]:
    if isinstance(node, dict):
        status = node.get('status')
        if isinstance(status, dict) and 'state' in status:
            return node.get('id') or node.get('taskId'), status['state']
        for value in node.values():
            found = _find_task_state(value)
            if found != (None, None):
                return found
    if isinstance(node, list):
        for value in node:
            found = _find_task_state(value)
            if found != (None, None):
                return found
    return None, None


def summarize(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(_last_sse_payload(body))
    except (ValueError, TypeError):
        return {}
    summary: dict[str, Any] = {}
    if isinstance(payload, dict):
        event = next(
            (key for key in ('task', 'statusUpdate', 'artifactUpdate', 'message') if key in payload),
            None,
        )
        if event:
            summary['event'] = event
        if 'method' in payload:
            summary['rpc_method'] = payload['method']
        if isinstance(payload.get('error'), dict):
            summary['rpc_error'] = payload['error'].get('message')
    task_id, state = _find_task_state(payload)
    if not task_id and isinstance(payload, dict) and isinstance(payload.get(summary.get('event')), dict):
        task_id = payload[summary['event']].get('taskId')
    if task_id:
        summary['task_id'] = task_id
    if state:
        summary['task_state'] = state
    return summary


class TrafficMiddleware:
    def __init__(self, app, log: TrafficLog) -> None:
        self.app = app
        self.log = log

    async def __call__(self, scope, receive, send) -> None:
        if scope['type'] != 'http' or not scope['path'].startswith(LOGGED_PREFIXES):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            event = await receive()
            chunks.append(event.get('body', b''))
            if not event.get('more_body'):
                break
        request_body = b''.join(chunks)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': request_body, 'more_body': False}
            return await receive()

        request_text = _truncate(request_body)
        started = time.monotonic()
        entry = self.log.record(
            kind='inbound',
            method=scope['method'],
            path=scope['path'] + (f"?{scope['query_string'].decode()}" if scope['query_string'] else ''),
            client=f"{scope['client'][0]}" if scope.get('client') else None,
            request_headers=self.log.mask(
                [(k.decode('latin-1'), v.decode('latin-1')) for k, v in scope['headers']]
            ),
            request_body=request_text,
            status=None,
            **summarize(request_text),
        )
        status = {'code': None}
        response_chunks: list[bytes] = []
        captured = 0

        async def capture(message):
            nonlocal captured
            if message['type'] == 'http.response.start':
                status['code'] = message['status']
            elif message['type'] == 'http.response.body':
                body = message.get('body', b'')
                if captured < MAX_BODY_BYTES * 2:
                    response_chunks.append(body)
                    captured += len(body)
            await send(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            response_text = _truncate(b''.join(response_chunks))
            response_summary = summarize(response_text)
            response_summary.pop('rpc_method', None)
            response_summary.pop('event', None)
            self.log.update(
                entry,
                status=status['code'],
                duration_ms=round((time.monotonic() - started) * 1000),
                response_body=response_text,
                **response_summary,
            )


class LoggingTransport(httpx.AsyncBaseTransport):
    def __init__(self, log: TrafficLog, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._log = log
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request_text = _truncate(request.content)
        started = time.monotonic()
        entry = self._log.record(
            kind='push',
            method=request.method,
            path=str(request.url),
            request_headers=self._log.mask(list(request.headers.items())),
            request_body=request_text,
            status=None,
            **summarize(request_text),
        )
        try:
            response = await self._inner.handle_async_request(request)
            body = await response.aread()
        except Exception as error:
            self._log.update(
                entry,
                error=f'{type(error).__name__}: {error}',
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        self._log.update(
            entry,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
            response_body=_truncate(body),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=body,
            request=request,
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
