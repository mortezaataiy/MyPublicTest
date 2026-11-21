# -*- coding: utf-8 -*-
"""ارسال پیام به تلگرام."""

import html as html_mod
import json
import os
import urllib.request


class TelegramError(RuntimeError):
    pass


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _call(method: str, payload=None, timeout: int = 30):
    tok = token()
    if not tok:
        raise TelegramError("TELEGRAM_BOT_TOKEN تنظیم نشده است")
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    res = json.loads(body)
    if not res.get("ok"):
        raise TelegramError(f"{method} ناموفق: {res.get('description')}")
    return res


def send_message(chat_id: str, text: str) -> None:
    _call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})


def esc(text) -> str:
    return html_mod.escape(str(text or ""), quote=False)


def format_ad(ad: dict) -> str:
    """متن پیام تلگرام برای یک آگهی (شامل لینک، طبق خواست کاربر)."""
    d = ad.get("detail") or {}
    specs = d.get("specs") or {}
    assess = d.get("assessment") or {}

    lines = [f"🚗 <b>{esc(ad.get('title') or d.get('title'))}</b>"]

    meta = []
    if specs.get("مدل (سال تولید)"):
        meta.append(f"مدل {esc(specs['مدل (سال تولید)'])}")
    if specs.get("کارکرد"):
        meta.append(f"{esc(specs['کارکرد'])} کیلومتر")
    if specs.get("رنگ"):
        meta.append(f"رنگ {esc(specs['رنگ'])}")
    if meta:
        lines.append(" · ".join(meta))

    if ad.get("price_card"):
        lines.append(f"💰 {esc(ad['price_card'])}")

    tags = [f"{k}: {v}" for k, v in assess.items() if v]
    if tags:
        lines.append("🔧 " + " | ".join(tags))

    if ad.get("where"):
        lines.append(f"📍 {esc(ad['where'])}")

    url = ad.get("url") or ""
    lines.append(f'🔗 <a href="{esc(url)}">مشاهده آگهی در دیوار</a>')
    lines.append(esc(url))
    return "\n".join(lines)
