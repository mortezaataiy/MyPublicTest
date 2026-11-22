# -*- coding: utf-8 -*-
"""اپ وب پنل: چهار صفحه (خانه، فیلترها، آگهی‌ها، اجراها) و ورود تک‌کاربره."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, db, filters, worker
from .defaults import HARD_BAD, NEGATABLE_BAD

BASE = Path(__file__).resolve().parent

FORM_KEYS = [
    "price_min",
    "price_max",
    "mileage_min",
    "mileage_max",
    "year_min",
    "year_max",
    "max_pages",
    "fetch_back_hours",
    "interval_minutes",
    "hourly_cap",
    "chat_id",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if os.environ.get("DIVAR_WORKER", "1") == "1":
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="پنل پراید دیوار", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["STATUS_LABELS"] = {
    "sent": "ارسال‌شده",
    "rejected": "رد‌شده",
    "waiting": "در انتظار ارسال",
    "error": "خطا",
    "new": "جدید",
}
templates.env.filters["num"] = lambda value: f"{int(value):,}" if value not in (None, "") else "—"


def _snippet(detail_json, limit: int = 240) -> str:
    """چند خط از متن آگهی، برای اینکه بشود فهمید چرا رد شده."""
    import json

    try:
        detail = json.loads(detail_json or "{}")
    except ValueError:
        return ""
    text = " ".join([detail.get("title", ""), detail.get("description", "")]).strip()
    return text[:limit]


templates.env.filters["snippet"] = _snippet


# ---------------------------------------------------------------- ورود
def require(request: Request):
    if not auth.session_ok(request.cookies.get(auth.COOKIE, "")):
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return True


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: int = 0):
    if auth.session_ok(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error, "auth_ok": False}
    )


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if not auth.password_ok(password):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        auth.COOKIE,
        auth.make_session(),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("PANEL_COOKIE_SECURE", "1") == "1",
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


# ---------------------------------------------------------------- خانه
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, ok: bool = Depends(require)):
    cfg = db.typed_settings()
    ctx = {
        "request": request,
        "page": "home",
        "auth_ok": True,
        "cfg": cfg,
        "counts": db.status_counts(),
        "sent_today": db.sent_today(),
        "sent_hour": db.sent_last_hour(),
        "pending": len(db.pending_list()),
        "runs": db.recent_runs(8),
        "history": db.settings_history(5),
        "telegram_ready": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()),
    }
    return templates.TemplateResponse("dashboard.html", ctx)


# ---------------------------------------------------------------- فیلترها
def _cfg_from_form(form) -> tuple[dict, dict]:
    """فرم پنل (میلیون/هزار) را به تنظیمات دیتابیس (تومان/کیلومتر) تبدیل می‌کند."""
    values, errors = {}, []
    try:
        values["price_min"] = str(int(float(form.get("price_min_m") or 0) * 1_000_000))
        values["price_max"] = str(int(float(form.get("price_max_m") or 0) * 1_000_000))
        values["mileage_min"] = str(int(float(form.get("mileage_min_k") or 0) * 1000))
        values["mileage_max"] = str(int(float(form.get("mileage_max_k") or 0) * 1000))
        values["year_min"] = str(int(form.get("year_min") or 1385))
        values["year_max"] = str(int(form.get("year_max") or 1425))
        values["max_pages"] = str(int(form.get("max_pages") or 4))
        values["fetch_back_hours"] = str(int(form.get("fetch_back_hours") or 36))
        values["interval_minutes"] = str(int(form.get("interval_minutes") or 5))
        values["hourly_cap"] = str(int(form.get("hourly_cap") or 0))
    except (TypeError, ValueError):
        errors.append("همه بازه‌ها باید عدد باشند")
    values["chat_id"] = (form.get("chat_id") or "").strip()
    values["enabled"] = "1" if form.get("enabled") else "0"
    values["allow_front_impact"] = "1" if form.get("allow_front_impact") else "0"

    if not errors:
        if int(values["price_min"]) >= int(values["price_max"]):
            errors.append("قیمت «از» باید کمتر از «تا» باشد")
        if int(values["mileage_min"]) >= int(values["mileage_max"]):
            errors.append("کارکرد «از» باید کمتر از «تا» باشد")
        if int(values["year_min"]) > int(values["year_max"]):
            errors.append("مدل «از» باید کمتر یا مساوی «تا» باشد")
        if int(values["interval_minutes"]) < 1:
            errors.append("فاصله بررسی حداقل ۱ دقیقه است")
    typed = {
        k: int(v)
        for k, v in values.items()
        if k
        in {
            "price_min",
            "price_max",
            "mileage_min",
            "mileage_max",
            "year_min",
            "year_max",
            "max_pages",
            "fetch_back_hours",
            "interval_minutes",
            "hourly_cap",
        }
    }
    typed["allow_front_impact"] = values["allow_front_impact"] == "1"
    return values, {"errors": errors, "typed": typed}


@app.get("/filters", response_class=HTMLResponse)
def filters_page(request: Request, saved: int = 0, passed: int = 0,
                 total: int = 0, error: int = 0, ok: bool = Depends(require)):
    return templates.TemplateResponse(
        "filters.html",
        {
            "request": request,
            "page": "filters",
            "auth_ok": True,
            "cfg": db.typed_settings(),
            "words": db.list_words(),
            "exceptions": db.list_exceptions(),
            "reasons": db.reject_reason_counts(8),
            "saved": saved,
            "passed": passed,
            "total": total,
            "error": error,
            "history": db.settings_history(5),
        },
    )


@app.post("/filters")
async def save_filters(request: Request, ok: bool = Depends(require)):
    form = await request.form()
    values, extra = _cfg_from_form(form)
    if extra["errors"]:
        return RedirectResponse("/filters?error=1", status_code=303)

    # واژه‌ها: فعال/غیرفعال، ویرایش الگو، حذف، افزودن
    for word in db.list_words():
        wid = word["id"]
        if form.get(f"delete_{wid}"):
            db.delete_word(wid)
            continue
        db.set_word(
            wid,
            enabled=bool(form.get(f"enabled_{wid}")),
            pattern=(form.get(f"pattern_{wid}") or "").strip() or None,
        )
    new_word = (form.get("new_word") or "").strip()
    if new_word:
        db.add_word(new_word, form.get("new_kind") or "hard")

    # استثناها: فعال/غیرفعال، حذف، افزودن
    for rule in db.list_exceptions():
        eid = rule["id"]
        if form.get(f"delete_exception_{eid}"):
            db.delete_exception(eid)
            continue
        db.set_exception(eid, bool(form.get(f"exception_{eid}")))
    new_exception = (form.get("new_exception") or "").strip()
    if new_exception:
        db.add_exception(new_exception)

    # پیش‌نمایش با تنظیمات تازه، روی آگهی‌های اخیرِ ذخیره‌شده (بدون تماس با دیوار)
    words = db.list_words()
    result = filters.preview(
        extra["typed"], words, db.ads_with_details(30), exceptions=db.list_exceptions()
    )
    db.set_settings(values)
    db.log_settings_change(
        "ویرایش فیلترها از پنل", values, result["passed"]
    )
    db.set_settings({"recheck_requested": "1"})
    return RedirectResponse(
        f"/filters?saved=1&passed={result['passed']}&total={result['total']}",
        status_code=303,
    )


@app.get("/api/preview")
def api_preview(request: Request, ok: bool = Depends(require)):
    q = request.query_params
    values, extra = _cfg_from_form(q)
    if extra["errors"]:
        return JSONResponse({"error": "؛ ".join(extra["errors"])}, status_code=400)
    result = filters.preview(
        extra["typed"],
        db.list_words(),
        db.ads_with_details(30),
        exceptions=db.list_exceptions(),
    )
    return JSONResponse(result)


# ---------------------------------------------------------------- آگهی‌ها
@app.get("/ads", response_class=HTMLResponse)
def ads_page(request: Request, status: str = "", ok: bool = Depends(require)):
    return templates.TemplateResponse(
        "ads.html",
        {
            "request": request,
            "page": "ads",
            "auth_ok": True,
            "status": status,
            "rows": db.ads_page(status, 200),
            "counts": db.status_counts(),
        },
    )


@app.post("/ads/send")
def ads_send(token: str = Form(""), ok: bool = Depends(require)):
    """ارسال دستی یک آگهی رد‌شده (بدون توجه به فیلترها)."""
    if token:
        threading.Thread(target=worker.send_one_now, args=(token,), daemon=True).start()
    return RedirectResponse("/ads?status=rejected", status_code=303)


@app.post("/ads/except")
def ads_except(
    token: str = Form(""), phrase: str = Form(""), ok: bool = Depends(require)
):
    """افزودن عبارت استثنا از روی یک آگهی رد‌شده و بازبینی دوباره."""
    phrase = " ".join((phrase or "").split())
    if phrase:
        db.add_exception(phrase)
        db.log_settings_change(f"استثنای تازه: {phrase}", {"exception": phrase}, None)
        db.set_settings({"recheck_requested": "1"})
        threading.Thread(
            target=worker.run_cycle, kwargs={"trigger": "manual"}, daemon=True
        ).start()
    return RedirectResponse("/ads?status=rejected", status_code=303)


@app.post("/ads/recheck")
def ads_recheck(ok: bool = Depends(require)):
    db.set_settings({"recheck_requested": "1"})
    threading.Thread(target=worker.run_cycle, kwargs={"trigger": "manual"}, daemon=True).start()
    return RedirectResponse("/ads?status=", status_code=303)


# ---------------------------------------------------------------- اجراها
@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, ok: bool = Depends(require)):
    return templates.TemplateResponse(
        "runs.html",
        {
            "request": request,
            "page": "runs",
            "auth_ok": True,
            "runs": db.recent_runs(50),
            "cfg": db.typed_settings(),
            "pending": db.pending_list(),
        },
    )


@app.post("/run-now")
def run_now(ok: bool = Depends(require)):
    threading.Thread(target=worker.run_cycle, kwargs={"trigger": "manual"}, daemon=True).start()
    return RedirectResponse("/runs", status_code=303)
