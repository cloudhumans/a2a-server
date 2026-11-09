import contextlib
import logging

from pathlib import Path

import httpx

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.push_url_validator import validate_push_notification_url
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from app.agent import ReferenceAgent
from app.auth import FixedTokenAuthMiddleware
from app.executor import ReferenceAgentExecutor
from app.settings import Settings
from app.traffic import LoggingTransport, TrafficLog, TrafficMiddleware


logger = logging.getLogger(__name__)

DASHBOARD_HTML = Path(__file__).with_name('dashboard.html')


def base_url(settings: Settings, request: Request | None = None) -> str:
    if settings.public_url:
        return settings.public_url
    if request is not None:
        return str(request.base_url).rstrip('/')
    return f'http://localhost:{settings.port}'


def build_agent_card(settings: Settings, url: str) -> AgentCard:
    jsonrpc_url = f'{url}/a2a/jsonrpc'
    rest_url = f'{url}/a2a/rest'
    card = AgentCard(
        name=settings.agent_name,
        description=(
            'Reference A2A agent built with LangGraph. Supports blocking and '
            'non-blocking SendMessage, streaming, push notifications and '
            'input-required. Message directives: [delay=N], [input-required], [fail].'
        ),
        provider=AgentProvider(organization='Cloud Humans', url='https://cloudhumans.com'),
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            AgentSkill(
                id='reference_chat',
                name='Reference chat',
                description='Answers questions. Use directives in the message to drive task states.',
                tags=['reference', 'testing'],
                examples=['What is A2A?', '[delay=420] long running request', '[input-required] book a trip'],
            )
        ],
        supported_interfaces=[
            AgentInterface(protocol_binding='JSONRPC', protocol_version='1.0', url=jsonrpc_url),
            AgentInterface(protocol_binding='HTTP+JSON', protocol_version='1.0', url=rest_url),
            AgentInterface(protocol_binding='JSONRPC', protocol_version='0.3', url=jsonrpc_url),
            AgentInterface(protocol_binding='HTTP+JSON', protocol_version='0.3', url=rest_url),
        ],
    )
    if settings.auth_mode == 'bearer':
        card.security_schemes['bearer'].CopyFrom(
            SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme='Bearer'))
        )
        card.security_requirements.append(SecurityRequirement(schemes={'bearer': StringList()}))
    elif settings.auth_mode == 'api_key':
        card.security_schemes['apiKey'].CopyFrom(
            SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name=settings.api_key_header
                )
            )
        )
        card.security_requirements.append(SecurityRequirement(schemes={'apiKey': StringList()}))
    return card


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    traffic = TrafficLog(
        capacity=settings.traffic_capacity,
        sensitive_headers={
            'authorization',
            'x-a2a-notification-token',
            settings.api_key_header,
        },
    )
    agent = ReferenceAgent()
    card = build_agent_card(settings, base_url(settings))

    push_client = httpx.AsyncClient(transport=LoggingTransport(traffic), timeout=10.0)
    push_config_store = InMemoryPushNotificationConfigStore()
    url_validator = validate_push_notification_url if settings.strict_push_urls else None
    request_handler = DefaultRequestHandler(
        agent_executor=ReferenceAgentExecutor(
            agent,
            default_delay_seconds=settings.default_delay_seconds,
            progress_interval_seconds=settings.progress_interval_seconds,
            force_fail_message=settings.force_fail_message if settings.force_fail else None,
        ),
        task_store=InMemoryTaskStore(),
        agent_card=card,
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(
            httpx_client=push_client,
            config_store=push_config_store,
            push_url_validator=url_validator,
        ),
        push_url_validator=url_validator,
    )

    async def dashboard(_: Request) -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML.read_text(encoding='utf-8'))

    async def agent_card(request: Request) -> JSONResponse:
        served = build_agent_card(settings, base_url(settings, request))
        return JSONResponse(agent_card_to_dict(served))

    async def traffic_feed(request: Request) -> JSONResponse:
        after = int(request.query_params.get('after', '0'))
        return JSONResponse(
            {
                'version': traffic.version,
                'entries': traffic.changed_since(after),
                'agent': {
                    'name': settings.agent_name,
                    'card_url': f'{base_url(settings, request)}{AGENT_CARD_WELL_KNOWN_PATH}',
                    'jsonrpc_url': f'{base_url(settings, request)}/a2a/jsonrpc',
                    'rest_url': f'{base_url(settings, request)}/a2a/rest',
                    'llm': agent.mode,
                    'model': agent.model,
                    'default_delay_seconds': settings.default_delay_seconds,
                    'auth': settings.auth_mode,
                    'api_key_header': settings.api_key_header if settings.auth_mode == 'api_key' else None,
                    'force_fail': settings.force_fail,
                },
            }
        )

    async def clear_traffic(_: Request) -> JSONResponse:
        traffic.clear()
        return JSONResponse({'version': traffic.version})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({'status': 'ok'})

    async def root(_: Request) -> RedirectResponse:
        return RedirectResponse('/dashboard')

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        logger.info('Agent card: %s%s', base_url(settings), AGENT_CARD_WELL_KNOWN_PATH)
        logger.info('Dashboard:  %s/dashboard', base_url(settings))
        logger.info('LLM: %s | auth: %s', agent.mode, settings.auth_mode)
        if settings.force_fail:
            logger.warning('FORCE_FAIL is on: every task ends in failed')
        yield
        await push_client.aclose()

    routes = [
        Route('/', root),
        Route('/healthz', health),
        Route('/dashboard', dashboard),
        Route('/dashboard/api/traffic', traffic_feed),
        Route('/dashboard/api/clear', clear_traffic, methods=['POST']),
        Route(AGENT_CARD_WELL_KNOWN_PATH, agent_card, methods=['GET']),
        *create_jsonrpc_routes(
            request_handler=request_handler, rpc_url='/a2a/jsonrpc', enable_v0_3_compat=True
        ),
        *create_rest_routes(
            request_handler=request_handler, path_prefix='/a2a/rest', enable_v0_3_compat=True
        ),
    ]
    return Starlette(
        routes=routes,
        middleware=[
            Middleware(TrafficMiddleware, log=traffic),
            Middleware(FixedTokenAuthMiddleware, settings=settings),
        ],
        lifespan=lifespan,
    )
