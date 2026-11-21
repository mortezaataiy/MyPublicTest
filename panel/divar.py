# -*- coding: utf-8 -*-
"""کلاینت ساده و سریال دیوار (درخواست موازی → ۴۲۹)."""

import json
import time
import urllib.request
from datetime import datetime, timezone

from .filters import clean

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class DivarError(RuntimeError):
    pass


def request(url, payload=None, timeout=45, retries=3):
    last = None
    for attempt in range(retries):
        try:
            data = (
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None
                else None
            )
            headers = {
                "User-Agent": UA,
                "Accept": "application/json",
                "Accept-Language": "fa-IR,fa;q=0.9",
                "Origin": "https://divar.ir",
                "Referer": "https://divar.ir/",
            }
            if data:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    raise DivarError(f"درخواست به {url} ناموفق بود: {last}")


def search_payload(cfg, pagination_data=None):
    payload = {
        "city_ids": [str(cfg["city_id"])],
        "search_data": {
            "form_data": {
                "data": {
                    "category": {"str": {"value": cfg["category"]}},
                    "brand_model": {"repeated_string": {"value": [cfg["brand"]]}},
                    "sort": {"str": {"value": "sort_date"}},
                    "price": {
                        "number_range": {
                            "minimum": str(cfg["price_min"]),
                            "maximum": str(cfg["price_max"]),
                        }
                    },
                    "mileage": {
                        "number_range": {
                            "minimum": str(cfg["mileage_min"]),
                            "maximum": str(cfg["mileage_max"]),
                        }
                    },
                    "production-year": {
                        "number_range": {
                            "minimum": str(cfg["year_min"]),
                            "maximum": str(cfg.get("year_max") or 1425),
                        }
                    },
                }
            }
        },
    }
    if pagination_data:
        payload["pagination_data"] = pagination_data
    return payload


def older_than_hours(iso, hours):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() > hours * 3600
    except Exception:  # noqa: BLE001
        return False


def fetch_search(cfg):
    """تا چند صفحه از نتیجه را می‌خواند (سریال، با یک ثانیه فاصله)."""
    ads, tokens, pagination = [], set(), None
    for _ in range(max(1, int(cfg.get("max_pages") or 1))):
        res = request("https://api.divar.ir/v8/postlist/w/search", search_payload(cfg, pagination))
        added = 0
        for widget in res.get("list_widgets", []):
            if widget.get("widget_type") != "POST_ROW":
                continue
            row = widget.get("data", {}) or {}
            token = row.get("token") or (row.get("action", {}).get("payload") or {}).get("token")
            if not token or token in tokens:
                continue
            tokens.add(token)
            added += 1
            ads.append(
                {
                    "token": token,
                    "title": clean(row.get("title", "")),
                    "price_card": clean(row.get("middle_description_text", "")),
                    "mileage_card": clean(row.get("top_description_text", "")),
                    "where": clean(row.get("bottom_description_text", "")),
                    "url": f"https://divar.ir/v/{token}",
                }
            )
        info = res.get("pagination") or {}
        pagination = info.get("data")
        if not added or not info.get("has_next_page") or not pagination:
            break
        if older_than_hours((pagination or {}).get("last_post_date"), int(cfg.get("fetch_back_hours") or 36)):
            break
        time.sleep(1)
    return ads


def fetch_detail(token):
    res = request(f"https://api.divar.ir/v8/posts-v2/web/{token}")
    info = {"title": "", "description": "", "specs": {}, "assessment": {}}
    descriptions = []
    for section in res.get("sections", []) or []:
        for widget in section.get("widgets", []) or []:
            kind = widget.get("widget_type")
            data = widget.get("data", {}) or {}
            if kind == "LEGEND_TITLE_ROW" and data.get("title"):
                info["title"] = clean(data["title"])
            elif kind == "DESCRIPTION_ROW":
                text = clean(data.get("text", ""))
                if text:
                    descriptions.append((data.get("is_primary", False), text))
            elif kind == "GROUP_INFO_ROW":
                for item in data.get("items", []) or []:
                    if item.get("title"):
                        info["specs"][clean(item["title"])] = clean(item.get("value", ""))
            elif kind == "UNEXPANDABLE_ROW":
                if data.get("title"):
                    info["specs"][clean(data["title"])] = clean(data.get("value", ""))
            elif kind == "SCORE_ROW":
                if data.get("title"):
                    info["assessment"][clean(data["title"])] = clean(
                        data.get("descriptive_score", "")
                    )
    if descriptions:
        descriptions.sort(key=lambda x: x[0], reverse=True)
        info["description"] = " ".join(t for _, t in descriptions)[:2000]
    return info
