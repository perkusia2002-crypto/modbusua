from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response

from .settings import Settings


COOKIE_NAME = "modbusua_ui_session"


def _signature(settings: Settings, payload: str) -> str:
    return hmac.new(settings.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session(settings: Settings) -> str:
    issued = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    payload = f"{issued}.{nonce}"
    return f"{payload}.{_signature(settings, payload)}"


def valid_session(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    issued_s, nonce, signature = parts
    try:
        issued = int(issued_s)
    except ValueError:
        return False
    if not nonce or not hmac.compare_digest(signature, _signature(settings, f"{issued_s}.{nonce}")):
        return False
    max_age = settings.session_days * 86400
    return 0 <= int(time.time()) - issued <= max_age


def require_auth(request: Request, settings: Settings) -> None:
    if not valid_session(settings, request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Требуется авторизация")


def login_response(response: Response, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session(settings),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
