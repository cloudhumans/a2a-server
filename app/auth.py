import hmac

from starlette.responses import JSONResponse

from app.settings import Settings


class FixedTokenAuthMiddleware:
    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope['type'] != 'http'
            or not self.settings.auth_enabled
            or not scope['path'].startswith('/a2a')
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode('latin-1').lower(): value.decode('latin-1')
            for key, value in scope['headers']
        }
        if self._authorized(headers):
            await self.app(scope, receive, send)
            return

        if self.settings.auth_mode == 'bearer':
            detail = 'Send Authorization: Bearer <token>.'
            extra = {'WWW-Authenticate': 'Bearer realm="a2a"'}
        else:
            detail = f'Send the {self.settings.api_key_header} header.'
            extra = {}
        response = JSONResponse(
            {'error': 'unauthorized', 'detail': detail}, status_code=401, headers=extra
        )
        await response(scope, receive, send)

    def _authorized(self, headers: dict[str, str]) -> bool:
        expected = self.settings.auth_token.encode()
        if self.settings.auth_mode == 'bearer':
            scheme, _, token = headers.get('authorization', '').partition(' ')
            return scheme.lower() == 'bearer' and hmac.compare_digest(
                token.strip().encode(), expected
            )
        provided = headers.get(self.settings.api_key_header.lower(), '')
        return hmac.compare_digest(provided.encode(), expected)
