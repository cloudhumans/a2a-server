import os

from dataclasses import dataclass


AUTH_MODES = ('none', 'bearer', 'api_key')


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    public_url: str
    agent_name: str
    auth_mode: str
    auth_token: str
    api_key_header: str
    default_delay_seconds: int
    progress_interval_seconds: int
    strict_push_urls: bool
    traffic_capacity: int
    force_fail: bool
    force_fail_message: str

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode != 'none'

    @classmethod
    def from_env(cls) -> 'Settings':
        port = int(os.getenv('PORT', '8000'))
        auth_mode = os.getenv('A2A_AUTH_MODE', 'none').strip().lower()
        if auth_mode not in AUTH_MODES:
            raise ValueError(
                f'A2A_AUTH_MODE must be one of {", ".join(AUTH_MODES)}, got {auth_mode!r}'
            )
        auth_token = os.getenv('A2A_AUTH_TOKEN', '').strip()
        if auth_mode != 'none' and not auth_token:
            raise ValueError(
                f'A2A_AUTH_MODE={auth_mode} requires A2A_AUTH_TOKEN to be set'
            )
        return cls(
            host=os.getenv('HOST', '127.0.0.1'),
            port=port,
            public_url=os.getenv('PUBLIC_URL', '').strip().rstrip('/'),
            agent_name=os.getenv('AGENT_NAME', 'A2A Reference Agent'),
            auth_mode=auth_mode,
            auth_token=auth_token,
            api_key_header=os.getenv('A2A_API_KEY_HEADER', 'X-API-Key'),
            default_delay_seconds=int(os.getenv('DEFAULT_DELAY_SECONDS', '0')),
            progress_interval_seconds=max(
                1, int(os.getenv('PROGRESS_INTERVAL_SECONDS', '10'))
            ),
            strict_push_urls=os.getenv('PUSH_URL_VALIDATION', 'off').lower()
            == 'strict',
            traffic_capacity=int(os.getenv('TRAFFIC_CAPACITY', '500')),
            force_fail=os.getenv('FORCE_FAIL', 'false').strip().lower() in ('1', 'true', 'yes'),
            force_fail_message=os.getenv('FORCE_FAIL_MESSAGE', '').strip()
            or 'Simulated failure: this agent runs with FORCE_FAIL=true.',
        )
