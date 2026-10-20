"""Authenticated SaaS HTTP boundary. Jobs are created only by POST."""
import asyncio
import json
import os
import time
from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from vnapp.accounts import require_admin

router = APIRouter(prefix="/api")
COOKIE = "ta_session"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=4, max_length=256)


def actor(request):
    result = getattr(request.state, "user", None)
    if not result:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    return result


def admin(request):
    result = actor(request)
    require_admin(result)
    return result


@router.post("/auth/register", status_code=201)
def register(payload: Credentials, request: Request):
    if os.environ.get("TA_ALLOW_REGISTRATION", "true").lower() != "true":
        raise HTTPException(403, "Đăng ký đang tạm đóng. Vui lòng liên hệ quản trị viên.")
    store = request.app.state.accounts
    store.throttle("register:" + request.client.host, limit=5, seconds=3600)
    return {"user": store.register(payload.email, payload.password)}


@router.post("/auth/login")
def login(payload: Credentials, request: Request, response: Response):
    store = request.app.state.accounts
    store.throttle("login-ip:" + request.client.host, limit=30)
    store.throttle("login-account:" + payload.email.strip().lower(), limit=10)
    token, csrf, user = store.login(payload.email, payload.password)
    secure = os.environ.get("TA_ENV") == "production" or request.url.scheme == "https"
    response.set_cookie(COOKIE, token, max_age=12*3600, httponly=True,
                        secure=secure, samesite="strict", path="/")
    return {"user": user, "csrf": csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    request.app.state.accounts.logout(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    return {"success": True}


@router.get("/auth/me")
def me(request: Request):
    user = actor(request)
    return {"user": {k: v for k, v in user.items() if k != "csrf"}, "csrf": user["csrf"],
            "quota": request.app.state.accounts.quota(user["id"])}


class PasswordRequest(StrictModel):
    current_password: str = Field(min_length=4, max_length=256)
    new_password: str = Field(min_length=4, max_length=256)


@router.post("/auth/password")
def change_password(payload: PasswordRequest, request: Request, response: Response):
    user = actor(request)
    request.app.state.accounts.throttle("password:" + user["id"], limit=5)
    request.app.state.accounts.change_password(user, payload.current_password, payload.new_password)
    response.delete_cookie(COOKIE, path="/")
    return {"success": True}


class RunRequest(StrictModel):
    ticker: str = Field(min_length=2, max_length=10, pattern=r"^[A-Za-z0-9]+$")
    trade_date: date
    analysts: list[str] = Field(default=["market", "social", "news", "fundamentals"], min_length=1, max_length=4)
    request_key: str = Field(min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


@router.post("/runs", status_code=202)
def submit(payload: RunRequest, request: Request):
    from tradingagents.markets.vietnam.registry import resolve_vn_instrument

    user = actor(request)
    allowed = {"market", "social", "news", "fundamentals"}
    analysts = list(dict.fromkeys("social" if a == "sentiment" else a for a in payload.analysts))
    if not set(analysts) <= allowed:
        raise HTTPException(422, "Danh sách chuyên gia không hợp lệ.")
    request.app.state.accounts.throttle("submit:" + user["id"], limit=30, seconds=60)
    try:
        instrument = resolve_vn_instrument(payload.ticker)
    except Exception as exc:
        raise HTTPException(503, "Không thể xác minh mã chứng khoán với nguồn dữ liệu Việt Nam.") from exc
    if instrument is None:
        raise HTTPException(422, "Mã chứng khoán không thuộc HOSE, HNX hoặc UPCoM.")
    return request.app.state.jobs.submit(user, payload.ticker.upper(), payload.trade_date.isoformat(), analysts, payload.request_key)


@router.get("/runs")
def runs(request: Request, active: bool = False):
    return {"runs": request.app.state.jobs.list(actor(request), active)}


@router.get("/workspace")
def workspace(request: Request):
    rows = request.app.state.jobs.list(actor(request), limit=1)
    return {"run": rows[0] if rows else None}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request):
    return request.app.state.jobs.get(actor(request), run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    return request.app.state.jobs.cancel(actor(request), run_id)


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request,
    after: int = Query(0, ge=0), last_event_id: int = Header(0, alias="Last-Event-ID", ge=0)):
    user = actor(request)
    store = request.app.state.jobs
    await run_in_threadpool(store.get, user, run_id)

    async def generate():
        cursor = max(after, last_event_id)
        checked_at = 0
        while not await request.is_disconnected():
            if time.monotonic()-checked_at > 10:
                current_user = await run_in_threadpool(request.app.state.accounts.authenticate, request.cookies.get(COOKIE))
                if not current_user:
                    yield {"event": "session_expired", "data": "{}"}
                    return
                checked_at = time.monotonic()
            batch = await run_in_threadpool(store.read_events, user, run_id, cursor)
            for item in batch:
                cursor = item["seq"]
                yield {"id": str(cursor), "event": item["kind"], "data": item["data"]}
            job = await run_in_threadpool(store.get, user, run_id)
            if job["status"] in ("completed", "failed", "cancelled") and cursor >= job["event_seq"]:
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(generate(), ping=15)


@router.get("/history")
def history(request: Request):
    rows = request.app.state.jobs.list(actor(request), status="completed")
    return {"reports": [{"id": row["id"], "ticker": row["ticker"], "trade_date": row["trade_date"],
        "saved_at": row["finished_at"], "signal": row["snapshot"].get("complete", {}).get("signal", "COMPLETED")}
        for row in rows if row["status"] == "completed"]}


@router.get("/history/{report_id}")
def report(report_id: str, request: Request):
    return {"id": report_id, "type": "json_state", "data": request.app.state.jobs.report(actor(request), report_id)}


class SubscriptionRequest(StrictModel):
    plan: str = Field(min_length=1, max_length=80)
    allowance: int = Field(ge=0, le=100000, strict=True)
    reason: str = Field(min_length=5, max_length=500)


class QuotaRequest(StrictModel):
    delta: int = Field(ge=-100000, le=100000, strict=True)
    reason: str = Field(min_length=5, max_length=500)
    request_key: str = Field(min_length=16, max_length=100)


class ActiveRequest(StrictModel):
    active: bool
    reason: str = Field(min_length=5, max_length=500)


@router.get("/admin/users")
def user_list(request: Request, search: str = Query("", max_length=160),
              offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
    return {"users": request.app.state.accounts.list_users(admin(request), search, offset, limit)}


@router.post("/admin/users", status_code=201)
def create_user(payload: Credentials, request: Request):
    user = admin(request)
    result = request.app.state.accounts.register(payload.email, payload.password)
    request.app.state.accounts.record_admin_action(user, "user.create", {"user_id": result["id"]})
    return {"user": result}


@router.post("/admin/users/{user_id}/subscription")
def assign_subscription(user_id: str, payload: SubscriptionRequest, request: Request):
    return request.app.state.accounts.set_subscription(admin(request), user_id, payload.plan, payload.allowance, payload.reason)


@router.post("/admin/users/{user_id}/quota")
def adjust_quota(user_id: str, payload: QuotaRequest, request: Request):
    return request.app.state.accounts.adjust_quota(admin(request), user_id, payload.delta, payload.reason, payload.request_key)


@router.post("/admin/users/{user_id}/active")
def set_active(user_id: str, payload: ActiveRequest, request: Request):
    request.app.state.accounts.set_active(admin(request), user_id, payload.active, payload.reason)
    return {"success": True}


@router.get("/admin/audit")
def audit_log(request: Request):
    return {"entries": request.app.state.accounts.audit_log(admin(request))}
