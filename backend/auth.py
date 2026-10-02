import http.cookies
import secrets
from typing import Optional, Dict

from .config import ADMIN_COOKIE_NAME, SESSION_TIMEOUT
from .utils import timestamp_now

admin_sessions: Dict[str, Dict[str, int]] = {}


def current_session(token: str) -> Optional[Dict[str, int]]:
    session = admin_sessions.get(token)
    if not session:
        return None
    if timestamp_now() - session["created_at"] > SESSION_TIMEOUT:
        del admin_sessions[token]
        return None
    return session


def create_admin_session() -> str:
    token = secrets.token_hex(16)
    admin_sessions[token] = {"created_at": timestamp_now()}
    return token


def parse_cookies(header_value: str) -> Dict[str, str]:
    cookies = http.cookies.SimpleCookie()
    if not header_value:
        return {}
    cookies.load(header_value)
    return {key: morsel.value for key, morsel in cookies.items()}


def authorized(handler) -> bool:
    cookies = parse_cookies(handler.headers.get("Cookie"))
    token = cookies.get(ADMIN_COOKIE_NAME)
    return current_session(token) is not None
