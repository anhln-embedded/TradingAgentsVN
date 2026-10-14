"""FastAPI Application Server for TradingAgentsVN Web UI."""

from pathlib import Path
import base64
import binascii
import hmac
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from vnapp.settings import load_runtime_env
from vnapp.runtime import RUN_LOCK

load_runtime_env()

from web.api.routes import router as api_router

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        yield
        # Detached browser sessions still finish saving before a normal shutdown.
        acquired = await asyncio.to_thread(RUN_LOCK.acquire, True, 300)
        if acquired:
            RUN_LOCK.release()

    app = FastAPI(
        title="TradingAgentsVN Web Dashboard",
        description="Giao diện Web trực quan hóa báo cáo và quá trình phân tích Multi-Agent cho TTCK Việt Nam",
        version="1.0.0",
        lifespan=lifespan,
    )

    production = os.environ.get("TA_ENV", "development") == "production"
    password = os.environ.get("TA_WEB_PASSWORD", "")
    username = os.environ.get("TA_WEB_USERNAME", "admin")
    if production and not password:
        raise RuntimeError("TA_WEB_PASSWORD is required in production")

    @app.middleware("http")
    async def access_control(request, call_next):
        if request.url.path == "/api/health":
            return await call_next(request)
        # Mutations are same-origin only, including authenticated browser requests.
        origin = request.headers.get("origin")
        expected_origin = os.environ.get("TA_PUBLIC_ORIGIN") or str(request.base_url)
        if origin and origin.rstrip("/") != expected_origin.rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, status_code=403)
        if password:
            scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
            try:
                decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
                user, _, supplied = decoded.partition(":")
            except (ValueError, UnicodeDecodeError, binascii.Error):
                user, supplied = "", ""
            if scheme.lower() != "basic" or not (
                hmac.compare_digest(user.encode(), username.encode())
                and hmac.compare_digest(supplied.encode(), password.encode())
            ):
                return JSONResponse({"detail": "Authentication required"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="TradingAgentsVN"'})
        return await call_next(request)

    # Register API Router
    app.include_router(api_router)

    # Static files directory
    static_dir = Path(__file__).resolve().parent.parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    # Serve index.html at root
    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"message": "TradingAgentsVN Web UI is loading..."}

    # Mount static assets
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app

app = create_app()
