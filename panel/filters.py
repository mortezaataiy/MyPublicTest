# -*- coding: utf-8 -*-
"""منطق فیلترها. همه چیز از تنظیمات و واژه‌های دیتابیس خوانده می‌شود، پس با
تغییر در پنل رفتار ربات فوراً عوض می‌شود (بدون نیاز به ویرایش کد)."""

import re

from .defaults import NEGATIONS

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


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
    import html as html_mod

    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


# ---------------------------------------------------------------- واژه‌ها
def compile_words(words):
    """(hard, negatable) هر کدام فهرستی از (برچسب، الگوی کامپایل‌شده)."""
    hard, neg = [], []
    for w in words:
        if not w.get("enabled"):
            continue
        try:
            pat = re.compile(w["pattern"])
        except re.error:
            continue
        (neg if w.get("kind") == "negatable" else hard).append((w["word"], pat))
    return hard, neg


def bad_reason(text, hard, neg):
    """واژه ممنوعه‌ای که در متن آمده، وگرنه None."""
    t = str(text or "").translate(FA_DIGITS)
    for word, pat in hard:
        if pat.search(t):
            return word
    for word, pat in neg:
        for match in pat.finditer(t):
            window = t[max(0, match.start() - 20): match.end() + 20]
            if any(n in window for n in NEGATIONS):
                continue
            return word
    return None


# ---------------------------------------------------------------- شاسی/بدنه
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
            "بدون رنگ",
            "دور رنگ",
            "بی خط",
            "خط و خش",
            "سالم",
            "صافکاری بی رنگ",
            "صافکاری بدون رنگ",
            "بی رنگ",
        )
    )


def chassis_ok(chassis):
    """همه شاسی‌های آمده باید سالم باشند."""
    if not chassis:
        return False
    for value in chassis.values():
        if "سالم" not in value:
            return False
        if any(w in value for w in ("رنگ", "تعویض", "ضربه", "جوش", "کج", "بریده", "ترک")):
            return False
    return True


# ---------------------------------------------------------------- ارزیابی
def year_of(specs, title=""):
    n = to_num((specs or {}).get("مدل (سال تولید)", ""))
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
    n = to_num((specs or {}).get("کارکرد", ""))
    return n if n is not None else to_num(card_value)


def evaluate(ad, cfg, words):
    """None اگر با فیلترها جور باشد، وگرنه دلیل رد.

    ad:  {title, price_card, mileage_card, detail:{specs, assessment, title, description}}
    cfg: تنظیمات عددی (price_min, price_max, ...)
    words: فهرست واژه‌های دیتابیس (list_words())
    """
    detail = ad.get("detail") or {}
    specs = detail.get("specs") or {}
    assess = detail.get("assessment") or {}
    hard, neg = compile_words(words)

    text = " ".join(
        [ad.get("title", ""), detail.get("title", ""), detail.get("description", "")]
    ).translate(FA_DIGITS)

    word = bad_reason(text, hard, neg)
    if word:
        return f"واژه «{word}» در آگهی"

    price = to_num(ad.get("price_card")) or to_num(specs.get("قیمت پایه", ""))
    if price is None:
        return "قیمت نامشخص"
    if not cfg["price_min"] <= price <= cfg["price_max"]:
        return f"قیمت {price:,} خارج از بازه"

    mileage = mileage_of(specs, ad.get("mileage_card"))
    if mileage is None:
        return "کارکرد نامشخص"
    if not cfg["mileage_min"] <= mileage <= cfg["mileage_max"]:
        return f"کارکرد {mileage:,} خارج از بازه"

    year = year_of(specs, ad.get("title") or detail.get("title"))
    if year is None:
        return "مدل نامشخص"
    if year < cfg["year_min"]:
        return f"مدل {year} پایین‌تر از {cfg['year_min']}"
    if cfg.get("year_max") and year > cfg["year_max"]:
        return f"مدل {year} بالاتر از {cfg['year_max']}"

    engine = assess.get("موتور", "")
    if engine and "سالم" not in engine:
        return f"موتور: {engine}"

    chassis = {k: v for k, v in assess.items() if "شاسی" in k}
    if not chassis:
        return "وضعیت شاسی در آگهی نیامده"
    if not chassis_ok(chassis):
        label, value = next((k, v) for k, v in chassis.items() if not chassis_ok({k: v}))
        return f"{label}: {value}"

    body = assess.get("بدنه", "")
    if not body:
        return "وضعیت بدنه در آگهی نیامده"
    if not body_ok(body):
        return f"بدنه: {body}"

    return None


def preview(cfg, words, rows, limit: int = 200):
    """چند آگهی از آگهی‌های ذخیره‌شده با تنظیمات فعلی قبول می‌شوند (بدون
    هیچ درخواستی به دیوار)."""
    import json

    total = passed = 0
    samples = []
    for row in rows[:limit]:
        try:
            detail = json.loads(row.get("detail_json") or "{}")
        except ValueError:
            continue
        ad = {
            "title": row.get("title", ""),
            "price_card": "",
            "mileage_card": "",
            "detail": detail,
        }
        total += 1
        reason = evaluate(ad, cfg, words)
        if reason is None:
            passed += 1
            if len(samples) < 5:
                samples.append(row.get("title", ""))
    rejected = total - passed
    return {
        "total": total,
        "passed": passed,
        "rejected": rejected,
        "rate": round(passed * 100 / total, 1) if total else 0,
        "samples": samples,
    }
