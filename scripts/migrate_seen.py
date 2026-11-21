#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""یک‌بار اجرا کنید تا ۹۵ آگهی قدیمی seen.json به‌عنوان «ارسال‌شده» در
دیتابیس پنل ثبت شوند (تا بعد از راه‌اندازی دوباره فرستاده نشوند)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel import db  # noqa: E402


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "seen.json")
    if not path.exists():
        print(f"فایل {path} پیدا نشد؛ چیزی برای مهاجرت نیست.")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    seen = data.get("seen") or {}
    pending = data.get("pending") or []

    db.init_db()
    added = 0
    now = db.now_iso()
    for token in seen:
        if db.ad_exists(token):
            continue
        db.execute(
            "INSERT INTO ads(token, title, status, first_seen, sent_at) "
            "VALUES(?, '', 'sent', ?, ?)",
            (token, now, now),
        )
        added += 1

    for item in pending:
        token = item.get("token")
        if token and not db.ad_exists(token):
            db.add_pending(token, item.get("title", ""), item.get("text", ""),
                           item.get("last_error", ""))
            added += 1

    print(f"{added} آگهی قدیمی به دیتابیس منتقل شد (کل seen: {len(seen)}).")


if __name__ == "__main__":
    main()
