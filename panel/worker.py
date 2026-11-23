# -*- coding: utf-8 -*-
"""چرخه پایش: هر چند دقیقه دیوار را می‌خواند، آگهی‌های تازه را با فیلترهای
پنل مقایسه می‌کند، ردشده‌ها را در دیتابیس نگه می‌دارد و قبول‌شده‌ها را
(با سقف ساعتی) به تلگرام می‌فرستد."""

import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import db, divar, filters, telegram

_lock = threading.Lock()
_stop = threading.Event()
_thread = None

LOCAL_TZ = timezone(timedelta(minutes=210))  # ساعت ایران (UTC+3:30)


# ---------------------------------------------------------------- ارسال
def send_quota(cfg: dict) -> int:
    """سهمیه‌ای از سقف روزانه که تا این ساعت از روز مجاز است.

    بدون این سهمیه، همهٔ آگهی‌های صف در اولین ساعات روز یک‌جا فرستاده می‌شدند
    و بعد تا نیمه‌شب سکوت می‌شد؛ حالا سقف روزانه در طول ساعت‌های بیداری پخش می‌شود.
    """
    daily = int(cfg.get("daily_cap") or 0)
    if daily <= 0:
        return 1000
    start = int(cfg.get("send_from_hour") or 0)
    end = int(cfg.get("send_to_hour") or 0)
    if end <= start:
        return daily
    now = datetime.now(LOCAL_TZ)
    hour = now.hour + now.minute / 60
    if hour < start:
        return 0
    if hour >= end:
        return daily
    elapsed = int((hour - start) * 60)
    return max(1, -(-daily * elapsed // ((end - start) * 60)))


def _numbers(ad) -> dict:
    """قیمت/کارکرد/مدل را یک‌جا از کارت و مشخصات آگهی درمی‌آورد تا هم در
    پیام تلگرام و هم در پنل نمایش داده شود."""
    detail = ad.get("detail") or {}
    specs = detail.get("specs") or {}
    price = filters.to_num(ad.get("price_card")) or filters.to_num(
        specs.get("قیمت پایه", "")
    )
    mileage = filters.mileage_of(specs, ad.get("mileage_card"))
    year = filters.year_of(specs, ad.get("title") or detail.get("title") or "")
    return {"price": price, "mileage": mileage, "year": year}


def _market(ad, cfg) -> str:
    """اگر آگهی از میانگین هم‌مدل‌هایش گران‌تر از حد مجاز باشد، دلیل رد."""
    pct = int(cfg.get("price_vs_market") or 0)
    if pct <= 0:
        return ""
    price = ad.get("price")
    year = ad.get("year")
    if not price or not year:
        return ""
    prices = db.market_prices(year)
    if len(prices) < 6:
        return ""
    median = prices[len(prices) // 2]
    if price > median * (1 + pct / 100):
        diff = round((price - median) * 100 / median)
        return f"قیمت {diff}٪ بالاتر از میانگین مدل {int(year)}"
    return ""


def market_note(row) -> tuple[int | None, int | None]:
    """میانهٔ قیمت هم‌مدل‌ها و اختلاف درصدی این آگهی با آن (برای پیام)."""
    price, year = row.get("price"), row.get("year")
    if not price or not year:
        return None, None
    prices = db.market_prices(year)
    if len(prices) < 6:
        return None, None
    median = prices[len(prices) // 2]
    return median, round((price - median) * 100 / median)


def _make_text(row) -> str:
    detail = {}
    try:
        detail = json.loads(row.get("detail_json") or "{}")
    except ValueError:
        pass
    ad = {
        "token": row["token"],
        "title": row.get("title", ""),
        "url": row.get("url") or f"https://divar.ir/v/{row['token']}",
        "price_card": "",
        "where": "",
        "detail": detail,
    }
    # قیمت/کارکرد/مدل را از دیتابیس بازسازی می‌کنیم
    if row.get("price"):
        ad["price_card"] = f"{row['price']:,} تومان"
    if row.get("mileage"):
        ad["mileage_card"] = f"{row['mileage']:,}"
    median, diff = market_note(row)
    ad["market_median"] = median
    ad["market_diff"] = diff
    ad["year"] = row.get("year")
    return telegram.format_ad(ad)


def filter_summary() -> str:
    """خلاصه خوانا از فیلترهای فعلی، برای فرستادن به تلگرام."""
    cfg = db.typed_settings()
    words = [w["word"] for w in db.list_words() if w["enabled"]]
    exc = [
        (f"{e['word']}: " if e.get("word") else "") + e["phrase"]
        for e in db.list_exceptions()
        if e["enabled"]
    ]
    lines = [
        "⚙️ <b>فیلترها تغییر کرد</b>",
        "",
        f"💰 قیمت: {cfg['price_min'] // 1_000_000} تا {cfg['price_max'] // 1_000_000} میلیون تومان",
        f"🛣 کارکرد: {cfg['mileage_min'] // 1000} تا {cfg['mileage_max'] // 1000} هزار کیلومتر",
        f"📅 مدل: {cfg['year_min']} تا {cfg['year_max']}",
        "🔧 شاسی ضربه‌خورده: "
        + ("قبول" if cfg.get("allow_front_impact") else "رد"),
        "🚫 واژه‌های ممنوعه: " + ("، ".join(words) if words else "هیچ"),
        "✅ استثناها: " + ("، ".join(exc) if exc else "هیچ"),
        "📉 سقف گرانی نسبت به میانگین مدل: "
        + (f"{cfg['price_vs_market']}٪" if cfg.get("price_vs_market") else "خاموش"),
        "📤 ارسال: "
        + (
            f"سقف {cfg['hourly_cap']} در ساعت، {cfg.get('daily_cap', 0)} در روز"
            f" (پخش‌شده بین ساعت {cfg.get('send_from_hour', 8)} تا {cfg.get('send_to_hour', 23)})"
            if int(cfg.get("daily_cap") or 0) > 0
            else "در لحظه و بدون سقف و بدون صف"
        ),
    ]
    return "\n".join(lines)


def notify_filters() -> bool:
    """خلاصهٔ فیلترهای تازه را به تلگرام می‌فرستد."""
    chat = db.typed_settings().get("chat_id")
    if not chat or not telegram.token():
        return False
    try:
        telegram.send_message(chat, filter_summary())
        return True
    except Exception:  # noqa: BLE001
        return False


def send_one_now(token: str) -> bool:
    """یک آگهی را به‌خواسته کاربر و بی‌توجه به سقف ساعتی می‌فرستد."""
    chat = db.typed_settings().get("chat_id")
    row = db.query_one("SELECT * FROM ads WHERE token = ?", (token,))
    if not row or not chat or not telegram.token():
        return False
    text = _make_text(row)
    try:
        telegram.send_message(chat, text)
        db.mark_sent(token)
        return True
    except Exception as exc:  # noqa: BLE001
        db.add_pending(token, row.get("title", ""), text, exc)
        db.set_ad_status(token, "error", f"تلگرام: {str(exc)[:150]}")
        return False


def flush_pending(chat_id: str) -> int:
    """آگهی‌هایی که ارسالشان خطا خورده بود را دوباره می‌فرستد."""
    sent = 0
    for item in db.pending_list():
        try:
            telegram.send_message(chat_id, item["text"])
            db.mark_sent(item["token"])
            sent += 1
        except Exception as exc:  # noqa: BLE001
            db.bump_pending_tries(item["token"], exc)
            if int(item.get("tries") or 0) + 1 >= 20:
                db.drop_pending(item["token"])
                db.set_ad_status(item["token"], "error", "ارسال تلگرام ناموفق بود")
        time.sleep(0.3)
    return sent


def send_waiting(chat_id: str, cap: int) -> int:
    """آگهی‌های آماده ارسال را با رعایت سقف ساعتی می‌فرستد."""
    if not chat_id:
        return 0
    if cap and cap > 0:
        room = cap - db.sent_last_hour()
        if room <= 0:
            return 0
    else:
        room = 1000
    cfg = db.typed_settings()
    daily = int(cfg.get("daily_cap") or 0)
    if daily > 0:
        room = min(room, send_quota(cfg) - db.sent_today_local())
        if room <= 0:
            return 0
    rows = db.query(
        "SELECT * FROM ads WHERE status = 'waiting' ORDER BY first_seen ASC LIMIT ?",
        (max(1, room),),
    )
    sent = 0
    for row in rows:
        text = _make_text(row)
        try:
            telegram.send_message(chat_id, text)
            db.mark_sent(row["token"])
            sent += 1
        except Exception as exc:  # noqa: BLE001
            # در صف می‌ماند تا چرخه بعد دوباره تلاش کند
            db.add_pending(row["token"], row.get("title", ""), text, exc)
            db.set_ad_status(row["token"], "error", f"تلگرام: {str(exc)[:150]}")
        time.sleep(0.5)
    return sent


# ---------------------------------------------------------------- چرخه
def recheck_rejected() -> tuple[int, int]:
    """ردشده‌های اخیر را با فیلترهای جدید دوباره می‌سنجد (بدون درخواست به دیوار)."""
    cfg = db.typed_settings()
    words = db.list_words()
    exceptions = db.list_exceptions()
    passed = total = 0
    for row in db.recent_for_recheck(7):
        try:
            detail = json.loads(row.get("detail_json") or "{}")
        except ValueError:
            continue
        total += 1
        ad = {
            "title": row.get("title", ""),
            "price_card": f"{row['price']:,}" if row.get("price") else "",
            "mileage_card": "",
            "detail": detail,
        }
        ad.update(_numbers(ad))
        reason = filters.evaluate(ad, cfg, words, exceptions) or _market(ad, cfg)
        if reason is None:
            db.set_ad_status(row["token"], "waiting", "")
            passed += 1
        else:
            db.set_ad_status(row["token"], "rejected", reason)
    return passed, total


def run_cycle(trigger: str = "schedule") -> dict:
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "یک بررسی در حال اجراست"}
    run_id = None
    try:
        cfg = db.typed_settings()
        words = db.list_words()
        exceptions = db.list_exceptions()
        run_id = db.start_run()
        if not cfg["enabled"] and trigger == "schedule":
            db.finish_run(run_id, True, 0, 0, 0, 0, "ربات خاموش است")
            db.set_settings({"last_tick": db.now_iso(), "last_result": "خاموش"})
            return {"ok": True, "message": "ربات خاموش است"}

        chat = cfg["chat_id"]
        fetched = new_ads = rejected = sent = 0
        notes = []

        # بدون توکن تلگرام هیچ ارسالی تلاش نمی‌شود (تا آگهی‌ها خطا نخورند)
        can_send = bool(telegram.token()) and bool(chat)

        # ۱) صف ارسال قبلی
        if can_send:
            sent += flush_pending(chat)

        # ۱.۵) آگهی‌های کهنهٔ صف، تا صف بی‌نهایت بزرگ نشود
        expired = db.expire_waiting_older_than(int(cfg.get("fetch_back_hours") or 36))
        if expired:
            notes.append(f"{expired} آگهی کهنه از صف خارج شد")

        # ۲) بررسی مجدد درخواستی
        if db.get_setting("recheck_requested") == "1":
            p, t = recheck_rejected()
            db.set_settings({"recheck_requested": "0"})
            notes.append(f"بازبینی {t} آگهی: {p} قبول شد")

        # ۳) جست‌وجوی تازه در دیوار
        try:
            ads = divar.fetch_search(cfg)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"دیوار پاسخ نداد: {str(exc)[:120]}")
            ads = []
        fetched = len(ads)

        for ad in ads:
            existing = db.ad_status(ad["token"])
            if existing is not None:
                # فقط آگهی‌هایی که خواندن صفحه‌شان قبلاً خطا خورده دوباره تلاش می‌شوند
                if existing not in ("error",):
                    continue
                row = db.query_one("SELECT tries FROM ads WHERE token = ?", (ad["token"],))
                if int((row or {}).get("tries") or 0) >= 5:
                    continue
            new_ads += 1
            db.execute(
                "UPDATE ads SET tries = tries + 1 WHERE token = ?", (ad["token"],)
            )
            try:
                ad["detail"] = divar.fetch_detail(ad["token"])
            except Exception as exc:  # noqa: BLE001
                notes.append(f"صفحه آگهی خوانده نشد: {str(exc)[:80]}")
                db.save_ad({**ad, "status": "error", "reason": "خواندن صفحه آگهی ناموفق"})
                continue
            ad.update(_numbers(ad))
            reason = filters.evaluate(ad, cfg, words, exceptions) or _market(ad, cfg)
            if reason:
                rejected += 1
                word = reason.split("«")[1].split("»")[0] if "«" in reason else ""
                if word:
                    db.bump_word_hits(word)
                db.save_ad({**ad, "status": "rejected", "reason": reason})
            else:
                db.save_ad({**ad, "status": "waiting", "reason": ""})
            time.sleep(1)

        # ۴) ارسال آماده‌ها با سقف ساعتی
        if can_send:
            sent += send_waiting(chat, int(cfg.get("hourly_cap") or 0))
        elif db.query("SELECT 1 FROM ads WHERE status='waiting' LIMIT 1"):
            notes.append(
                "ارسال ممکن نیست (توکن ربات یا شناسه چت تنظیم نشده)؛ آگهی‌ها منتظر ماندند"
            )

        summary = (
            f"{fetched} آگهی دیده شد، {new_ads} تازه، {rejected} رد، {sent} ارسال"
        )
        if notes:
            summary += " | " + "؛ ".join(notes[:3])
        db.finish_run(run_id, True, fetched, new_ads, sent, rejected, summary)
        db.set_settings({"last_tick": db.now_iso(), "last_result": summary})
        return {"ok": True, "message": summary}
    except Exception as exc:  # noqa: BLE001
        msg = f"خطا: {str(exc)[:200]}"
        if run_id:
            db.finish_run(run_id, False, 0, 0, 0, 0, msg + "\n" + traceback.format_exc()[:300])
        db.set_settings({"last_tick": db.now_iso(), "last_result": msg})
        return {"ok": False, "message": msg}
    finally:
        _lock.release()


# ---------------------------------------------------------------- نخ پس‌زمینه
def _loop():
    while not _stop.is_set():
        try:
            run_cycle("schedule")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        try:
            minutes = int(db.typed_settings().get("interval_minutes") or 5)
        except Exception:  # noqa: BLE001
            minutes = 5
        _stop.wait(max(60, minutes * 60))


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="divar-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
