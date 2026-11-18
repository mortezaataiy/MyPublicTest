#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
پایش آگهی‌های پراید مشهد در دیوار و ارسال آگهی‌های مطابق فیلتر به تلگرام.

فیلترها (بالای فایل یا با متغیر محیطی قابل تغییرند):
  • همه مدل‌های پراید سواری (وانت کنار گذاشته می‌شود)
  • قیمت ۳۳۰ تا ۵۲۰ میلیون تومان
  • مدل ۱۳۸۰ و بالاتر
  • کارکرد ۸۰٬۰۰۰ تا ۴۸۰٬۰۰۰ کیلومتر
  • شاسی سالم / سالم و پلمپ (شاسی جلو و عقب هم جدا بررسی می‌شود)
  • بدنه بدون‌رنگ، دوررنگ، خط‌وخش جزیی یا رنگ‌شدگی حداکثر ۲ ناحیه
  • موتور سالم، بدون تصادف و بدون نیاز به تعمیر
  • هیچ اشاره‌ای به اقساط، قسط، چک، سود، درصد، شرایط، پیش‌پرداخت، لیزینگ و وام

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

# بازه‌ها بر پایه اندازه‌گیری روی دیوار تنظیم شده‌اند تا روزی حدود ۱۰ آگهی برسد
PRICE_MIN = int(os.environ.get("PRICE_MIN", 330_000_000))
PRICE_MAX = int(os.environ.get("PRICE_MAX", 520_000_000))
MILEAGE_MIN = int(os.environ.get("MILEAGE_MIN", 80_000))
MILEAGE_MAX = int(os.environ.get("MILEAGE_MAX", 480_000))
YEAR_MIN = int(os.environ.get("YEAR_MIN", 1380))

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
            if isinstance(data, dict) and "seen" in data:
                return data
    except (FileNotFoundError, ValueError):
        pass
    return {"seen": {}}


def save_state(state):
    if len(state["seen"]) > MAX_SEEN:
        state["seen"] = dict(list(state["seen"].items())[-MAX_SEEN:])
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# --------------------------------------------------------------- دیوار API
def fetch_search():
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
    res = request("https://api.divar.ir/v8/postlist/w/search", payload)
    ads, seen = [], set()
    for widget in res.get("list_widgets", []):
        if widget.get("widget_type") != "POST_ROW":
            continue
        row = widget.get("data", {})
        token = row.get("token") or (row.get("action", {}).get("payload") or {}).get("token")
        if not token or token in seen:
            continue
        seen.add(token)
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
    return ads


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
    return request(f"https://api.telegram.org/bot{TOKEN}/{method}", payload, timeout=30)


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


# --------------------------------------------------------------- main
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
    try:
        ads = fetch_search()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"جست‌وجو در دیوار ناموفق بود: {exc}")

    if not ads:
        print("نتیجه‌ای برای این فیلترها روی دیوار نیست (یا دیوار درخواست را محدود کرده).")
        return

    print(f"{len(ads)} آگهی در نتیجه جست‌وجو، جدیدترین: {ads[0]['title']}")
    new_ads = [a for a in ads if a["token"] not in state["seen"]]
    first_run = not state["seen"]

    if args.seed or first_run:
        for ad in ads:
            state["seen"][ad["token"]] = 1
        save_state(state)
        print(f"{len(ads)} آگهی به‌عنوان دیده‌شده ثبت شد.")
        if first_run and not args.dry_run:
            chat = resolve_chat(state)
            save_state(state)
            if chat:
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
        return

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
            print(f"  ! {ad['title']}: صفحه آگهی خوانده نشد ({exc})")
            continue
        reason = check(ad)
        if reason:
            if args.verbose:
                print(f"  ✗ {ad['title']} — {reason}")
        else:
            print(f"  ✓ {ad['title']}")
            matched.append(ad)
        state["seen"][ad["token"]] = 1
        time.sleep(1)

    if args.dry_run:
        for ad in matched:
            print("-" * 50)
            print(re.sub(r"<[^>]+>", "", message(ad)))
        return

    save_state(state)

    if not matched:
        print("هیچ آگهی‌ای با فیلترها جور نبود.")
        return

    chat = resolve_chat(state)
    save_state(state)
    if not chat:
        sys.exit("chat_id پیدا نشد؛ یک بار به ربات پیام بدهید و دوباره اجرا کنید.")
    for ad in reversed(matched):
        tg("sendMessage", {"chat_id": chat, "text": message(ad), "parse_mode": "HTML"})
    print(f"{len(matched)} آگهی مطابق فیلتر به تلگرام ارسال شد.")


if __name__ == "__main__":
    main()
