#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
پایش آگهی‌های پراید مشهد در دیوار و ارسال آگهی‌های مطابق فیلتر به تلگرام.

فیلترها (بالای فایل یا با متغیر محیطی قابل تغییرند):
  • همه مدل‌های پراید سواری (وانت کنار گذاشته می‌شود)
  • قیمت ۳۰۰ تا ۵۶۰ میلیون تومان
  • مدل ۱۳۸۵ و بالاتر
  • کارکرد ۶۰٬۰۰۰ تا ۵۰۰٬۰۰۰ کیلومتر
  • شاسی سالم / سالم و پلمپ (شاسی جلو و عقب هم جدا بررسی می‌شود)
  • بدنه بدون‌رنگ، دوررنگ، خط‌وخش جزیی یا رنگ‌شدگی حداکثر ۲ ناحیه
  • موتور سالم، بدون تصادف و بدون نیاز به تعمیر
  • هیچ اشاره‌ای به اقساط، قسط، چک، سود، درصد، شرایط، پیش‌پرداخت، لیزینگ و وام

ضد تکرار و ضد گم‌شدن:
  • هر آگهی با «توکن» خودش در seen.json ثبت می‌شود؛ دوباره فرستاده نمی‌شود.
  • آگهی فقط بعد از ارسال موفق «دیده‌شده» علامت می‌خورد؛ اگر ارسال به خطا بخورد
    در صف «pending» می‌ماند و اجرای بعدی دوباره می‌فرستد.
  • وضعیت بعد از هر ارسال ذخیره می‌شود تا اگر اجرا وسط کار قطع شد، آگهی‌های
    فرستاده‌شده تکراری نشوند و فرستاده‌نشده‌ها در اجرای بعد بروند.
  • چند صفحه از نتایج خوانده می‌شود تا اگر چند اجرا عقب افتاد، آگهی‌های جدیدِ
    همان بازه از دست نروند.

اجرا:
    python3 divar_pride_monitor.py            # بررسی و ارسال آگهی‌های جدید
    python3 divar_pride_monitor.py --dry-run  # فقط نمایش در ترمینال
    python3 divar_pride_monitor.py --verbose  # نمایش دلیل رد شدن هر آگهی
    python3 divar_pride_monitor.py --seed     # ثبت آگهی‌های فعلی بدون ارسال
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------- تنظیمات
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

CITY_ID = os.environ.get("DIVAR_CITY_ID", "3")  # مشهد
CATEGORY = os.environ.get("DIVAR_CATEGORY", "light")  # سواری
BRAND = os.environ.get("DIVAR_BRAND", "Pride")

# بازه‌ها بر پایه اندازه‌گیری روی دیوار تنظیم شده‌اند تا روزی حدود ۷ آگهی برسد
# (با مدل ۱۳۸۳ و همین بازه قیمت/کارکرد، روزی ~۱۵ آگهی می‌آمد که زیاد بود)
PRICE_MIN = int(os.environ.get("PRICE_MIN", 300_000_000))
PRICE_MAX = int(os.environ.get("PRICE_MAX", 560_000_000))
MILEAGE_MIN = int(os.environ.get("MILEAGE_MIN", 60_000))
MILEAGE_MAX = int(os.environ.get("MILEAGE_MAX", 500_000))
YEAR_MIN = int(os.environ.get("YEAR_MIN", 1385))

# حداکثر چند صفحه از نتایج خوانده شود (هر صفحه ~۲۵ آگهی)
MAX_PAGES = int(os.environ.get("MAX_PAGES", 4))
# در صفحه‌بندی عقب‌تر از این تعداد ساعت نرو (آگهی کهنه ارزش فرستادن ندارد)
FETCH_BACK_HOURS = int(os.environ.get("FETCH_BACK_HOURS", 36))

# صف ارسال مجدد: چند بار تلاش شود و تا چند روز نگه داشته شود
MAX_PENDING_TRIES = 20
PENDING_TTL = 3 * 24 * 3600

# واژه‌هایی که هر طور در متن بیایند آگهی رد می‌شود (اقساط، شرایط، درصد و ...)
W = r"(?<![\u0600-\u06FF])"  # مرز کلمه فارسی
E = r"(?![\u0600-\u06FF])"
HARD_BAD = [
    ("اقساط", W + r"(?:اقساط|قسط)(?:ی|ه)?" + E),
    ("لیزینگ", W + r"لیزینگ" + E),
    ("تسهیلات/وام", W + r"(?:تسهیلات|وام)" + E),
    ("اجاره به شرط", r"اجاره\s*به\s*شرط"),
    ("درصد در معامله", r"(?:درصد|٪|%)\s*(?:سود|پیش|قسط|ماهیانه|کارمزد)|(?:سود|پیش|قسط)\s*(?:درصد|٪|%)"),
    ("سود/بهره", W + r"(?:سود|بهره)" + E),
    ("پول پیش", r"پول\s*پیش|پیش\s*پول"),
    ("شرایط", r"شرایط"),
    ("چک/سفته", W + r"(?:چک|چک‌ها|سفته|ضامن)" + E),
    ("پیش‌پرداخت", r"پیش\s*[-‌]?\s*پرداخت|پیش‌پرداخت|پیش\s*قسط"),
    ("قسط ماهانه", r"قسط\s*ماهانه|ماهی\s*(?:چند|یک)?\s*قسط"),
    ("وانت", W + r"وانت" + E),
    ("اوراق", W + r"اوراق" + E),
    ("بدون موتور", r"بدون\s*موتور"),
]

# آسیب‌ها؛ اگر با «بدون/ندارد/نخورده» همراه باشند (مثل «شاسی ضربه نخورده») رد نمی‌شود
NEGATABLE_BAD = [
    ("تصادف", r"تصادف"),
    ("سقف رنگ", r"سقف\s*[-‌]?\s*رنگ(?:ی|ه)?" + E),
    ("ضربه", r"ضربه"),
    ("جوش", r"جوش"),
    ("تمام رنگ", r"(?:تمام|کامل)\s*[-‌]?\s*رنگ|رنگ\s*(?:کامل|تمام)"),
    ("تعویض قطعه", r"(?:تعویض|عوض)\s*(?:شده)?\s*(?:شاسی|موتور|گیربکس|سینی)|(?:شاسی|موتور|گیربکس|سینی)\s*(?:تعویض|عوض)"),
    ("نیاز به تعمیر", r"نیاز\s*به\s*تعمیر"),
    ("تعمیر موتور", r"(?:تعمیر|بازسازی|تعمیرات)\s*(?:موتور|گیربکس|شاسی)"),
    ("داغی", r"داغی|جوش آوردن"),
    ("سوخته", r"(?<!آفتاب )(?<!افتاب )سوخته"),
    ("کچل", r"کچل"),
]
NEGATIONS = ("بدون", "ندارد", "نداشته", "نداریم", "نداره", "نخورده", "نخورد", "نشده", "نیست", "نزده")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
MAX_SEEN = 4000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# --------------------------------------------------------------- ابزارها
def to_num(text):
    """اولین عدد متن را برمی‌گرداند؛ میلیون/میلیارد/هزار را هم می‌فهمد."""
    if not text:
        return None
    t = str(text).translate(FA_DIGITS).replace("٬", ",").replace("،", ",")
    m = re.search(r"[\d,\.]+", t)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", "").rstrip("."))
    except ValueError:
        return None
    if "میلیارد" in t:
        value *= 1_000_000_000
    elif "میلیون" in t:
        value *= 1_000_000
    elif "هزار" in t:
        value *= 1_000
    return int(value)


def clean(text):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


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
    raise RuntimeError(f"درخواست به {url} ناموفق بود: {last}")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("seen"), dict):
        data["seen"] = {}
    if not isinstance(data.get("pending"), list):
        data["pending"] = []
    return data


def save_state(state):
    seen = state.get("seen", {})
    if len(seen) > MAX_SEEN:
        state["seen"] = dict(list(seen.items())[-MAX_SEEN:])
    pending = state.get("pending", [])
    if len(pending) > 200:
        state["pending"] = pending[-200:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# --------------------------------------------------------------- دیوار API
def search_payload(pagination_data=None):
    payload = {
        "city_ids": [CITY_ID],
        "search_data": {
            "form_data": {
                "data": {
                    "category": {"str": {"value": CATEGORY}},
                    "brand_model": {"repeated_string": {"value": [BRAND]}},
                    "sort": {"str": {"value": "sort_date"}},
                    "price": {
                        "number_range": {
                            "minimum": str(PRICE_MIN),
                            "maximum": str(PRICE_MAX),
                        }
                    },
                    "mileage": {
                        "number_range": {
                            "minimum": str(MILEAGE_MIN),
                            "maximum": str(MILEAGE_MAX),
                        }
                    },
                    "production-year": {
                        "number_range": {
                            "minimum": str(YEAR_MIN),
                            "maximum": "1425",
                        }
                    },
                }
            }
        },
    }
    if pagination_data:
        payload["pagination_data"] = pagination_data
    return payload


def fetch_search(max_pages=MAX_PAGES):
    """تا چند صفحه از نتیجه را می‌خواند تا آگهی‌ای از قلم نیفتد."""
    ads, tokens, pagination = [], set(), None
    for _ in range(max(1, max_pages)):
        res = request("https://api.divar.ir/v8/postlist/w/search", search_payload(pagination))
        added = 0
        for widget in res.get("list_widgets", []):
            if widget.get("widget_type") != "POST_ROW":
                continue
            row = widget.get("data", {})
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
        last_date = (pagination or {}).get("last_post_date")
        if not added or not info.get("has_next_page") or not pagination:
            break
        if last_date and older_than_hours(last_date, FETCH_BACK_HOURS):
            break
        time.sleep(1)
    return ads


def older_than_hours(iso, hours):
    try:
        stamp = iso.replace("Z", "+00:00")
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(stamp)
        return (datetime.now(timezone.utc) - dt).total_seconds() > hours * 3600
    except Exception:  # noqa: BLE001
        return False


def fetch_detail(token):
    res = request(f"https://api.divar.ir/v8/posts-v2/web/{token}")
    info = {"title": "", "description": "", "specs": {}, "assessment": {}}
    descriptions = []
    for section in res.get("sections", []):
        for widget in section.get("widgets", []):
            kind = widget.get("widget_type")
            data = widget.get("data", {}) or {}
            if kind == "LEGEND_TITLE_ROW" and data.get("title"):
                info["title"] = clean(data["title"])
            elif kind == "DESCRIPTION_ROW":
                text = clean(data.get("text", ""))
                if text:
                    descriptions.append((data.get("is_primary", False), text))
            elif kind == "GROUP_INFO_ROW":
                for item in data.get("items", []):
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


# --------------------------------------------------------------- فیلتر
def year_of(specs, title=""):
    n = to_num(specs.get("مدل (سال تولید)", ""))
    if n is None:
        m = re.search(r"مدل\s*([0-9]{2,4})", str(title).translate(FA_DIGITS))
        n = to_num(m.group(1)) if m else None
    if n is None:
        return None
    if n > 1000:
        return n
    if n >= 100:  # ۹۵ → ۱۳۹۵
        return 1300 + n
    return None


def mileage_of(specs, card_value):
    n = to_num(specs.get("کارکرد", ""))
    return n if n is not None else to_num(card_value)


def bad_reason(text):
    """واژه ممنوعه‌ای که در متن آمده، وگرنه None."""
    t = text.translate(FA_DIGITS)
    for word, pattern in HARD_BAD:
        if re.search(pattern, t):
            return f"واژه «{word}» در آگهی"
    for word, pattern in NEGATABLE_BAD:
        for match in re.finditer(pattern, t):
            window = t[max(0, match.start() - 20): match.end() + 20]
            if any(neg in window for neg in NEGATIONS):
                continue
            return f"واژه «{word}» در آگهی"
    return None


def body_ok(body):
    """بدنه قابل قبول: بدون‌رنگ، دوررنگ، خط‌وخش جزیی، رنگ‌شدگی حداکثر ۲ ناحیه."""
    b = str(body or "").replace("\u200c", " ").strip()
    if not b:
        return False
    if any(w in b for w in ("تصادف", "تمام رنگ", "کامل رنگ", "سقف رنگ")):
        return False
    if "رنگ شدگی" in b:
        n = to_num(b)
        return n is None or n <= 2
    return any(
        w in b
        for w in (
            "بدون رنگ", "دور رنگ", "بی خط", "خط و خش", "سالم", "صافکاری بی رنگ",
            "صافکاری بدون رنگ", "بی رنگ",
        )
    )


def chassis_ok(chassis):
    """همه شاسی‌های آمده باید سالم باشند (بدون رنگ/تعویض/ضربه/جوش/کج/بریده)."""
    if not chassis:
        return False
    for value in chassis.values():
        if "سالم" not in value:
            return False
        if any(w in value for w in ("رنگ", "تعویض", "ضربه", "جوش", "کج", "بریده", "ترک")):
            return False
    return True


def check(ad):
    """None اگر با فیلترها جور باشد، وگرنه دلیل رد."""
    detail = ad["detail"]
    specs, assess = detail["specs"], detail["assessment"]
    text = " ".join([ad["title"], detail["title"], detail["description"]]).translate(FA_DIGITS)

    bad = bad_reason(text)
    if bad:
        return bad

    price = to_num(ad["price_card"]) or to_num(specs.get("قیمت پایه", ""))
    if price is None:
        return "قیمت نامشخص"
    if not PRICE_MIN <= price <= PRICE_MAX:
        return f"قیمت {price:,} خارج از بازه"

    mileage = mileage_of(specs, ad["mileage_card"])
    if mileage is None:
        return "کارکرد نامشخص"
    if not MILEAGE_MIN <= mileage <= MILEAGE_MAX:
        return f"کارکرد {mileage:,} خارج از بازه"

    year = year_of(specs, ad["title"] or detail["title"])
    if year is None:
        return "مدل نامشخص"
    if year < YEAR_MIN:
        return f"مدل {year} پایین‌تر از {YEAR_MIN}"

    engine = assess.get("موتور", "")
    if engine and "سالم" not in engine:
        return f"موتور: {engine}"

    chassis = {k: v for k, v in assess.items() if "شاسی" in k}
    if not chassis:
        return "وضعیت شاسی در آگهی نیامده"
    if not chassis_ok(chassis):
        label, value = next(
            (k, v) for k, v in chassis.items() if not chassis_ok({k: v})
        )
        return f"{label}: {value}"

    body = assess.get("بدنه", "")
    if not body:
        return "وضعیت بدنه در آگهی نیامده"
    if not body_ok(body):
        return f"بدنه: {body}"

    return None


# --------------------------------------------------------------- تلگرام
def tg(method, payload=None):
    res = request(f"https://api.telegram.org/bot{TOKEN}/{method}", payload, timeout=30)
    if not isinstance(res, dict) or not res.get("ok"):
        desc = res.get("description") if isinstance(res, dict) else str(res)[:200]
        raise RuntimeError(f"تلگرام {method} ناموفق بود: {desc}")
    return res


def resolve_chat(state=None):
    if CHAT_ID:
        return CHAT_ID
    if state and state.get("chat_id"):
        return state["chat_id"]
    try:
        res = tg("getUpdates", {"limit": 20, "timeout": 0})
    except Exception:  # noqa: BLE001
        return ""
    for upd in reversed(res.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            if state is not None:
                state["chat_id"] = str(chat["id"])
            return str(chat["id"])
    return ""


def esc(t):
    return html_mod.escape(str(t or ""), quote=False)


def message(ad):
    d = ad["detail"]
    specs, assess = d["specs"], d["assessment"]
    lines = [f"🚗 <b>{esc(ad['title'] or d['title'])}</b>"]

    meta = []
    if specs.get("مدل (سال تولید)"):
        meta.append(f"مدل {esc(specs['مدل (سال تولید)'])}")
    if specs.get("کارکرد"):
        meta.append(f"{esc(specs['کارکرد'])} کیلومتر")
    if specs.get("رنگ"):
        meta.append(f"رنگ {esc(specs['رنگ'])}")
    if meta:
        lines.append(" · ".join(meta))

    if ad["price_card"]:
        lines.append(f"💰 {esc(ad['price_card'])}")

    tags = [f"{k}: {v}" for k, v in assess.items() if v]
    if tags:
        lines.append("🔧 " + " | ".join(tags))

    if ad["where"]:
        lines.append(f"📍 {esc(ad['where'])}")
    lines.append(f"🔗 <a href=\"{esc(ad['url'])}\">مشاهده آگهی در دیوار</a>")
    lines.append(esc(ad["url"]))
    return "\n".join(lines)


# --------------------------------------------------------------- صف ارسال
def queue_pending(state, token, title, text, error, tries=1):
    for item in state["pending"]:
        if item.get("token") == token:
            item["tries"] = item.get("tries", 1) + 1
            item["last_error"] = str(error)[:150]
            return
    state["pending"].append(
        {
            "token": token,
            "title": title,
            "text": text,
            "tries": tries,
            "added_at": int(time.time()),
            "last_error": str(error)[:150],
        }
    )


def send_ad(state, chat, token, text, delay=0.5):
    """ارسال یک آگهی؛ فقط بعد از موفقیت «دیده‌شده» علامت می‌خورد."""
    tg("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML"})
    state["seen"][token] = 1
    state["pending"] = [p for p in state["pending"] if p.get("token") != token]
    save_state(state)
    time.sleep(delay)


def flush_pending(state, chat):
    """آگهی‌هایی که اجرای قبل ارسال نشدند را دوباره می‌فرستد."""
    sent = 0
    for item in list(state["pending"]):
        try:
            send_ad(state, chat, item["token"], item["text"], delay=0)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            item["tries"] = item.get("tries", 1) + 1
            item["last_error"] = str(exc)[:150]
            expired = time.time() - item.get("added_at", 0) > PENDING_TTL
            if item["tries"] >= MAX_PENDING_TRIES or expired:
                state["pending"] = [p for p in state["pending"] if p.get("token") != item["token"]]
                state["seen"][item["token"]] = 1
                print(f"  ! ارسال {item.get('title', '')} رها شد ({exc})")
            else:
                print(f"  ! ارسال {item.get('title', '')} باز هم ناموفق ماند؛ اجرای بعد")
            save_state(state)
    return sent


# --------------------------------------------------------------- main
def report_pending(state):
    pending = state.get("pending") or []
    if pending:
        print(f"{len(pending)} آگهی در صف ارسال مجدد است:")
        for item in pending[:10]:
            print(f"  • {item.get('title', '')} ({item.get('tries', 0)} تلاش)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not TOKEN and not args.dry_run:
        sys.exit("TELEGRAM_BOT_TOKEN تنظیم نشده است.")

    state = load_state()
    first_run = not state["seen"] and not state["pending"]

    # ---------- حالت آزمایشی: فقط نمایش، هیچ تغییری در seen.json نمی‌دهد
    if args.dry_run:
        report_pending(state)
        try:
            ads = fetch_search()
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"جست‌وجو در دیوار ناموفق بود: {exc}")
        if not ads:
            print("نتیجه‌ای برای این فیلترها روی دیوار نیست (یا دیوار درخواست را محدود کرده).")
            return
        print(f"{len(ads)} آگهی در نتیجه جست‌وجو، جدیدترین: {ads[0]['title']}")
        if first_run:
            print("(حالت اولیه: در اجرای واقعی این آگهی‌ها فقط ثبت می‌شوند و ارسال نمی‌شوند.)")
            return
        new_ads = [a for a in ads if a["token"] not in state["seen"]]
        if not new_ads:
            print("آگهی جدیدی نیست.")
            return
        if args.limit:
            new_ads = new_ads[: args.limit]
        print(f"{len(new_ads)} آگهی تازه بررسی می‌شود...")
        for ad in new_ads:
            try:
                ad["detail"] = fetch_detail(ad["token"])
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {ad['title']}: صفحه آگهی خوانده نشد ({exc})")
                continue
            reason = check(ad)
            if reason:
                if args.verbose:
                    print(f"  ✗ {ad['title']} — {reason}")
            else:
                print(f"  ✓ {ad['title']}")
                print("-" * 50)
                print(re.sub(r"<[^>]+>", "", message(ad)))
            time.sleep(1)
        return

    # ---------- اجرای واقعی
    if first_run:
        try:
            ads = fetch_search()
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"جست‌وجو در دیوار ناموفق بود: {exc}")
        for ad in ads:
            state["seen"][ad["token"]] = 1
        save_state(state)
        print(f"{len(ads)} آگهی به‌عنوان دیده‌شده ثبت شد (اجرای اول).")
        chat = resolve_chat(state)
        save_state(state)
        if chat:
            try:
                tg(
                    "sendMessage",
                    {
                        "chat_id": chat,
                        "parse_mode": "HTML",
                        "text": (
                            "✅ پایش آگهی‌های پراید مشهد فعال شد.\n"
                            f"قیمت {PRICE_MIN // 1_000_000} تا {PRICE_MAX // 1_000_000} میلیون · "
                            f"مدل {YEAR_MIN} به بالا · کارکرد "
                            f"{MILEAGE_MIN // 1000} تا {MILEAGE_MAX // 1000} هزار کیلومتر\n"
                            "شاسی سالم · بدنه بدون‌رنگ/دوررنگ · بدون تصادف و نیاز به تعمیر"
                        ),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                print(f"! پیام فعال‌سازی ارسال نشد: {exc}")
        return

    chat = resolve_chat(state)
    save_state(state)
    if state["pending"]:
        report_pending(state)
        if chat:
            sent = flush_pending(state, chat)
            print(f"{sent} آگهی از صف قبلی ارسال شد.")
        else:
            print("chat_id پیدا نشد؛ صف برای اجرای بعد می‌ماند.")

    try:
        ads = fetch_search()
    except Exception as exc:  # noqa: BLE001
        print(f"جست‌وجو در دیوار ناموفق بود: {exc}")
        return

    if not ads:
        print("نتیجه‌ای برای این فیلترها روی دیوار نیست (یا دیوار درخواست را محدود کرده).")
        return

    print(f"{len(ads)} آگهی در نتیجه جست‌وجو، جدیدترین: {ads[0]['title']}")
    new_ads = [a for a in ads if a["token"] not in state["seen"]]
    if not new_ads:
        print("آگهی جدیدی نیست.")
        return
    if args.limit:
        new_ads = new_ads[: args.limit]

    print(f"{len(new_ads)} آگهی تازه بررسی می‌شود...")
    matched = []
    for ad in new_ads:
        try:
            ad["detail"] = fetch_detail(ad["token"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {ad['title']}: صفحه آگهی خوانده نشد ({exc}) — اجرای بعد دوباره بررسی می‌شود")
            continue
        reason = check(ad)
        if reason:
            if args.verbose:
                print(f"  ✗ {ad['title']} — {reason}")
            state["seen"][ad["token"]] = 1
        else:
            print(f"  ✓ {ad['title']}")
            matched.append(ad)
        time.sleep(1)

    # ردشده‌ها ثبت می‌شوند؛ قبول‌شده‌ها فقط بعد از ارسال موفق
    save_state(state)

    if not matched:
        print("هیچ آگهی‌ای با فیلترها جور نبود.")
        return

    if not chat:
        for ad in matched:
            queue_pending(state, ad["token"], ad["title"], message(ad), "chat_id تنظیم نشده")
        save_state(state)
        sys.exit("chat_id پیدا نشد؛ آگهی‌ها در صف ماندند و اجرای بعد (پس از ست کردن chat_id) ارسال می‌شوند.")

    sent, failed = 0, 0
    for ad in reversed(matched):
        try:
            send_ad(state, chat, ad["token"], message(ad))
            sent += 1
        except Exception as exc:  # noqa: BLE001
            queue_pending(state, ad["token"], ad["title"], message(ad), exc)
            save_state(state)
            failed += 1
            print(f"  ! ارسال {ad['title']} ناموفق: {exc}")
    print(f"{sent} آگهی ارسال شد" + (f"، {failed} آگهی در صف اجرای بعد ماند." if failed else "."))


if __name__ == "__main__":
    main()
