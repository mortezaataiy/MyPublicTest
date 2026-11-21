# -*- coding: utf-8 -*-
"""ورود تک‌کاربره با کوکی امضاشده (HMAC). بدون دیتابیس و بدون وابستگی اضافه."""

import hashlib
import hmac
import os
import secrets
import time

COOKIE = "divar_panel"
MAX_AGE = 30 * 24 * 3600  # ۳۰ روز


def _password() -> str:
    return os.environ.get("PANEL_PASSWORD", "").strip()


def _secret() -> bytes:
    raw = os.environ.get("PANEL_SECRET", "").strip()
    if not raw:
        raw = "divar-panel::" + _password()
    return hashlib.sha256(raw.encode("utf-8")).digest()


def password_ok(candidate: str) -> bool:
    expected = _password()
    if not expected:
        return False
    return hmac.compare_digest(candidate or "", expected)


def make_session() -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(6)
    body = f"{ts}:{nonce}"
    sig = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}:{sig}"


def session_ok(value: str) -> bool:
    if not value:
        return False
    parts = value.split(":")
    if len(parts) != 3:
        return False
    ts, nonce, sig = parts
    body = f"{ts}:{nonce}"
    expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(time.time()) - int(ts) <= MAX_AGE
    except ValueError:
        return False
