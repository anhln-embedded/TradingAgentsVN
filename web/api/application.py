"""Authenticated multi-user dashboard. Analysis runs in independent workers."""
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from vnapp.accounts import AccountStore, DomainError
from vnapp.database import make_engine
from vnapp.jobs import JobStore
from vnapp.settings import load_runtime_env

load_runtime_env()


def create_app(engine=None):
    @asynccontextmanager
    async def lifespan(app):
        if os.environ.get("TA_ENV") == "production" and not os.environ.get("TA_PUBLIC_ORIGIN", "").startswith("https://"):
            raise RuntimeError("TA_PUBLIC_ORIGIN must be the public HTTPS origin in production")
        db = engine if engine is not None else make_engine()
        accounts = AccountStore(db)
        await run_in_threadpool(accounts.initialize)
        password = os.environ.get("TA_BOOTSTRAP_ADMIN_PASSWORD")
        if password:
            await run_in_threadpool(accounts.bootstrap,
                os.environ.get("TA_BOOTSTRAP_ADMIN_EMAIL", "superadmin"), password)
        app.state.accounts = accounts
        app.state.jobs = JobStore(db)
        yield
        if engine is None:
            db.dispose()

    app = FastAPI(title="TradingAgentsVN", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    static = Path(__file__).resolve().parent.parent / "static"

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.middleware("http")
    async def access_control(request, call_next):
        path = request.url.path
        public = path in ("/login", "/api/health", "/api/auth/login", "/api/auth/register") or path.startswith("/static/") or path.startswith("/api/quote/")
        origin = request.headers.get("origin")
        expected = (os.environ.get("TA_PUBLIC_ORIGIN") or str(request.base_url)).rstrip("/")
        if origin and origin.rstrip("/") != expected:
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if path in ("/api/auth/login", "/api/auth/register"):
                if request.headers.get("x-requested-with") != "TradingAgentsVN":
                    return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 65536:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
        if not public:
            user = await run_in_threadpool(app.state.accounts.authenticate, request.cookies.get("ta_session"))
            if not user:
                if not path.startswith("/api/"):
                    return RedirectResponse("/login", status_code=303)
                return JSONResponse({"detail": "Vui lòng đăng nhập."}, status_code=401)
            request.state.user = user
            if request.method not in ("GET", "HEAD", "OPTIONS") and not hmac.compare_digest(
                    request.headers.get("x-csrf-token", ""), user["csrf"]):
                return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
            if (path.startswith("/api/admin/") or path == "/admin" or path == "/api/config" or path.startswith("/api/git/")) and user["role"] != "superadmin":
                return JSONResponse({"detail": "Chỉ superadmin được truy cập."}, status_code=403)
            if path == "/api/analyze/stream":
                return JSONResponse({"detail": "Use POST /api/runs and authenticated run events."}, status_code=410)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    from web.api.saas import router as saas_router
    from web.api.routes import router as api_router
    from web.api.ssi_oauth import router as ssi_oauth_router
    app.include_router(saas_router)
    app.include_router(api_router)
    app.include_router(ssi_oauth_router)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(static / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(static / "login.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(static / "admin.html")

    @app.get("/account", include_in_schema=False)
    def profile_page():
        return FileResponse(static / "profile.html")

    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app


app = create_app()
