# -*- coding: utf-8 -*-
"""چرخه پایش: هر چند دقیقه دیوار را می‌خواند، آگهی‌های تازه را با فیلترهای
پنل مقایسه می‌کند، ردشده‌ها را در دیتابیس نگه می‌دارد و قبول‌شده‌ها را
(با سقف ساعتی) به تلگرام می‌فرستد."""

import json
import threading
import time
import traceback

from . import db, divar, filters, telegram

_lock = threading.Lock()
_stop = threading.Event()
_thread = None


# ---------------------------------------------------------------- ارسال
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
    # قیمت روی کارت را از دیتابیس بازسازی می‌کنیم
    if row.get("price"):
        ad["price_card"] = f"{row['price']:,} تومان"
    return telegram.format_ad(ad)


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
        reason = filters.evaluate(ad, cfg, words)
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
        run_id = db.start_run()
        if not cfg["enabled"] and trigger == "schedule":
            db.finish_run(run_id, True, 0, 0, 0, 0, "ربات خاموش است")
            db.set_settings({"last_tick": db.now_iso(), "last_result": "خاموش"})
            return {"ok": True, "message": "ربات خاموش است"}

        chat = cfg["chat_id"]
        fetched = new_ads = rejected = sent = 0
        notes = []

        # ۱) صف ارسال قبلی
        if chat:
            sent += flush_pending(chat)

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
            reason = filters.evaluate(ad, cfg, words)
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
        if chat:
            sent += send_waiting(chat, int(cfg.get("hourly_cap") or 0))
        elif db.query("SELECT 1 FROM ads WHERE status='waiting' LIMIT 1"):
            notes.append("chat_id تنظیم نشده؛ آگهی‌ها در انتظار ماندند")

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
