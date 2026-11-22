# -*- coding: utf-8 -*-
"""منطق فیلترها. همه چیز از تنظیمات و واژه‌های دیتابیس خوانده می‌شود، پس با
تغییر در پنل رفتار ربات فوراً عوض می‌شود (بدون نیاز به ویرایش کد)."""

import re

from .defaults import NEGATIONS

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
SPACES = r"[\s\u200c\u200e\u200f]+"  # فاصله، نیم‌فاصله و کاراکترهای جهت


def norm_spaces(text):
    """فاصله/نیم‌فاصله‌ها را یکدست می‌کند (دیوار گاهی «دوررنگ» می‌نویسد و
    گاهی «دور رنگ»)."""
    return re.sub(SPACES, " ", str(text or "")).strip()


def compact(text):
    """همه فاصله‌ها را برمی‌دارد تا مقایسه‌ها به فاصله حساس نباشند."""
    return re.sub(SPACES, "", str(text or ""))


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


# ---------------------------------------------------------------- استثناها
def phrase_pattern(phrase):
    """الگوی یک عبارت استثنا؛ فاصله‌ها انعطاف‌پذیر هستند."""
    parts = [re.escape(p) for p in norm_spaces(phrase).split(" ") if p]
    if not parts:
        return None
    return re.compile("[\\s\u200c\u200e\u200f]*".join(parts))


def _excepted(word, text, exceptions):
    """آیا برای این واژه استثنایی هست که عبارتش در متن آمده؟"""
    for rule in exceptions or []:
        if not int(rule.get("enabled", 1) or 0):
            continue
        target = (rule.get("word") or "").strip()
        if target and target != word and target not in word and word not in target:
            continue
        pat = phrase_pattern(rule.get("phrase", ""))
        if pat and pat.search(text):
            return True
    return False


def bad_reason(text, hard, neg, exceptions=None):
    """واژه ممنوعه‌ای که در متن آمده، وگرنه None.

    هر واژه می‌تواند استثنای خودش را داشته باشد؛ مثلاً واژهٔ «وانت» با استثنای
    «مثل وانت» رد نمی‌شود.
    """
    t = str(text or "").translate(FA_DIGITS)
    for word, pat in hard:
        if pat.search(t) and not _excepted(word, t, exceptions):
            return word
    for word, pat in neg:
        for match in pat.finditer(t):
            window = t[max(0, match.start() - 20): match.end() + 20]
            if any(n in window for n in NEGATIONS):
                continue
            if _excepted(word, t, exceptions):
                continue
            return word
    return None


# ---------------------------------------------------------------- شاسی/بدنه
def body_ok(body):
    """بدنه قابل قبول: بدون‌رنگ، دوررنگ، خط‌وخش جزیی، رنگ‌شدگی حداکثر ۲ ناحیه.

    مقایسه بدون فاصله انجام می‌شود تا «دوررنگ» و «دور رنگ» یکی حساب شوند.
    """
    b = norm_spaces(body)
    if not b:
        return False
    c = compact(b)
    if any(w in c for w in ("تصادف", "تمامرنگ", "کاملرنگ", "سقفرنگ", "رنگتمام", "رنگکامل")):
        return False
    if "رنگشدگی" in c:
        n = to_num(b)
        return n is None or n <= 2
    return any(
        w in c
        for w in (
            "بدونرنگ",
            "دوررنگ",
            "بیخط",
            "خطوخش",
            "سالم",
            "صافکاریبیرنگ",
            "صافکاریبدونرنگ",
            "بیرنگ",
        )
    )


CHASSIS_BAD = ("رنگ", "تعویض", "ضربه", "جوش", "کج", "بریده", "ترک")


def chassis_ok(chassis, allow_front_impact=True):
    """همه شاسی‌های آمده باید سالم باشند.

    اگر allow_front_impact روشن باشد، «شاسی جلو: ضربه‌خورده» قبول می‌شود؛ چون
    دیوار این وضعیت را برای بیشتر پرایدها ثبت می‌کند و شاسی جلو با ضربهٔ جزئی
    تقریباً همیشه هست. تعویض/رنگ/جوش در شاسی جلو همچنان رد می‌شود.
    """
    if not chassis:
        return False
    for label, value in chassis.items():
        v = compact(value)
        if not v:
            return False
        front = "جلو" in compact(label)
        if front and allow_front_impact and "ضربه" in v:
            if not any(w in v for w in ("تعویض", "رنگ", "جوش", "کج", "بریده", "ترک")):
                continue
        if "سالم" not in v:
            return False
        if any(w in v for w in CHASSIS_BAD):
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


def evaluate(ad, cfg, words, exceptions=None):
    """None اگر با فیلترها جور باشد، وگرنه دلیل رد.

    ad:  {title, price_card, mileage_card, detail:{specs, assessment, title, description}}
    cfg: تنظیمات عددی (price_min, price_max, ...)
    words: فهرست واژه‌های دیتابیس (list_words())
    exceptions: فهرست استثناها؛ عبارت‌هایشان پیش از بررسی واژه‌ها حذف می‌شود
    """
    detail = ad.get("detail") or {}
    specs = detail.get("specs") or {}
    assess = detail.get("assessment") or {}
    hard, neg = compile_words(words)

    raw = " ".join(
        [ad.get("title", ""), detail.get("title", ""), detail.get("description", "")]
    ).translate(FA_DIGITS)
    text = norm_spaces(raw)

    word = bad_reason(text, hard, neg, exceptions)
    if word:
        return f"واژه «{word}» در آگهی"

    installment = compact(specs.get("امکان خرید قسطی", ""))
    if installment and installment not in ("خیر", "ندارد", "نه"):
        return "امکان خرید قسطی در آگهی"

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
    if engine and "سالم" not in compact(engine):
        return f"موتور: {engine}"

    chassis = {k: v for k, v in assess.items() if "شاسی" in k}
    if not chassis:
        return "وضعیت شاسی در آگهی نیامده"
    allow_front = bool(cfg.get("allow_front_impact"))
    if not chassis_ok(chassis, allow_front):
        label, value = next(
            (k, v) for k, v in chassis.items() if not chassis_ok({k: v}, allow_front)
        )
        return f"{label}: {value}"

    body = assess.get("بدنه", "")
    if not body:
        return "وضعیت بدنه در آگهی نیامده"
    if not body_ok(body):
        return f"بدنه: {body}"

    return None


def preview(cfg, words, rows, limit: int = 200, exceptions=None):
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
        reason = evaluate(ad, cfg, words, exceptions)
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
