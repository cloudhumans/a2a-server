import argparse
import asyncio
import os
import sys
import time
import uuid

import httpx
import uvicorn

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


WEBHOOK_TOKEN = 'smoke-webhook-token'


class Webhook:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def handle(self, request: Request) -> JSONResponse:
        self.received.append(
            {
                'token': request.headers.get('x-a2a-notification-token'),
                'authorization': request.headers.get('authorization'),
                'body': await request.json(),
            }
        )
        return JSONResponse({'ok': True})

    def states(self) -> list[str]:
        found = []
        for item in self.received:
            body = item['body']
            node = body.get('task') or body.get('statusUpdate') or {}
            state = node.get('status', {}).get('state')
            if state:
                found.append(state)
        return found


class Client:
    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        self.base_url = base_url.rstrip('/')
        self.http = httpx.AsyncClient(headers=headers, timeout=60.0)

    async def rpc(self, method: str, params: dict) -> dict:
        response = await self.http.post(
            f'{self.base_url}/a2a/jsonrpc',
            json={'jsonrpc': '2.0', 'id': str(uuid.uuid4()), 'method': method, 'params': params},
        )
        response.raise_for_status()
        payload = response.json()
        if 'error' in payload:
            raise RuntimeError(f'{method} failed: {payload["error"]}')
        return payload['result']

    async def send(self, text: str, configuration: dict | None = None, task_id=None, context_id=None) -> dict:
        message = {'messageId': str(uuid.uuid4()), 'role': 'ROLE_USER', 'parts': [{'text': text}]}
        if task_id:
            message['taskId'] = task_id
        if context_id:
            message['contextId'] = context_id
        params = {'message': message}
        if configuration:
            params['configuration'] = configuration
        result = await self.rpc('SendMessage', params)
        return result['task']

    async def get_task(self, task_id: str) -> dict:
        return await self.rpc('GetTask', {'id': task_id})


def check(condition: bool, label: str) -> None:
    print(f'  {"PASS" if condition else "FAIL"}  {label}')
    if not condition:
        raise SystemExit(1)


async def wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.5)
    return False


async def run(args) -> None:
    headers = {'A2A-Version': '1.0'}
    if args.token:
        if args.api_key_header:
            headers[args.api_key_header] = args.token
        else:
            headers['Authorization'] = f'Bearer {args.token}'
    client = Client(args.url, headers)

    print('Agent card')
    card = (await client.http.get(f'{client.base_url}/.well-known/agent-card.json')).json()
    versions = {item.get('protocolVersion') for item in card.get('supportedInterfaces', [])}
    check('1.0' in versions, f'card advertises protocol 1.0 (found {sorted(versions)})')
    check(card.get('capabilities', {}).get('pushNotifications') is True, 'card advertises push notifications')

    if args.token:
        print('Authentication')
        anonymous = await httpx.AsyncClient().post(
            f'{client.base_url}/a2a/jsonrpc',
            json={'jsonrpc': '2.0', 'id': '1', 'method': 'GetTask', 'params': {'id': 'x'}},
        )
        check(anonymous.status_code == 401, f'request without credentials is rejected (HTTP {anonymous.status_code})')

    print('Sync: blocking SendMessage')
    task = await client.send('Reply with the single word: pong')
    check(task['status']['state'] == 'TASK_STATE_COMPLETED', f'task completed ({task["status"]["state"]})')
    reply = task['artifacts'][0]['parts'][0]['text']
    print(f'        reply: {reply!r}')

    print('Sync: returnImmediately + GetTask polling')
    task = await client.send('[delay=3] Say hi', {'returnImmediately': True})
    check(task['status']['state'] in ('TASK_STATE_SUBMITTED', 'TASK_STATE_WORKING'), f'returned before finishing ({task["status"]["state"]})')

    async def finished():
        return (await client.get_task(task['id']))['status']['state'] == 'TASK_STATE_COMPLETED'

    check(await wait_for(finished, 30), 'GetTask reaches completed')

    print('Async: push notification to webhook')
    webhook = Webhook()
    server = uvicorn.Server(
        uvicorn.Config(
            Starlette(routes=[Route('/hook', webhook.handle, methods=['POST'])]),
            host=args.webhook_host,
            port=args.webhook_port,
            log_level='warning',
        )
    )
    server_task = asyncio.create_task(server.serve())
    await wait_for(lambda: asyncio.sleep(0, server.started), 5)
    task = await client.send(
        f'[delay={args.async_delay}] Explain A2A in one sentence',
        {
            'returnImmediately': True,
            'taskPushNotificationConfig': {
                'url': args.webhook_url or f'http://127.0.0.1:{args.webhook_port}/hook',
                'token': WEBHOOK_TOKEN,
            },
        },
    )

    async def pushed_completion():
        return 'TASK_STATE_COMPLETED' in webhook.states()

    check(await wait_for(pushed_completion, args.async_delay + 30), f'webhook received completed (states: {webhook.states()})')
    check(all(item['token'] == WEBHOOK_TOKEN for item in webhook.received), 'every push carries X-A2A-Notification-Token')
    server.should_exit = True
    await server_task

    print('Input required: continue the same task')
    task = await client.send('[input-required] Book a trip')
    check(task['status']['state'] == 'TASK_STATE_INPUT_REQUIRED', f'task asks for input ({task["status"]["state"]})')
    follow_up = await client.send('Order 12345', task_id=task['id'], context_id=task['contextId'])
    check(follow_up['id'] == task['id'], 'follow-up continues the same task id')
    check(follow_up['status']['state'] == 'TASK_STATE_COMPLETED', f'task completed after input ({follow_up["status"]["state"]})')

    print('All checks passed.')


def main() -> None:
    parser = argparse.ArgumentParser(description='End-to-end checks against a running A2A agent.')
    parser.add_argument('--url', default=os.getenv('AGENT_URL', 'http://localhost:8000'))
    auth_mode = os.getenv('A2A_AUTH_MODE', 'none').strip().lower()
    parser.add_argument('--token', default=os.getenv('A2A_AUTH_TOKEN') if auth_mode != 'none' else None)
    parser.add_argument(
        '--api-key-header',
        default=os.getenv('A2A_API_KEY_HEADER', 'X-API-Key') if auth_mode == 'api_key' else None,
    )
    parser.add_argument('--webhook-host', default='127.0.0.1', help='Address the local webhook listens on')
    parser.add_argument('--webhook-port', type=int, default=8099)
    parser.add_argument('--webhook-url', help='URL the agent must call, when it cannot reach 127.0.0.1')
    parser.add_argument('--async-delay', type=int, default=5)
    try:
        asyncio.run(run(parser.parse_args()))
    except httpx.HTTPError as error:
        print(f'HTTP error: {error}')
        sys.exit(1)


if __name__ == '__main__':
    main()
