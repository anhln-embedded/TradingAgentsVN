"""Authenticated web endpoints for the application-wide SSI MCP connection."""

import os
import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from tradingagents.dataflows.ssi_mcp import SSIMCPError
from tradingagents.dataflows.ssi_oauth import ssi_oauth
from vnapp.accounts import require_admin

router = APIRouter(prefix="/api/integrations/ssi", tags=["SSI"])


def _callback_url(request: Request) -> str:
    configured = os.environ.get("SSI_MCP_REDIRECT_URI", "").strip()
    if os.environ.get("TA_ENV") == "production":
        if not configured:
            raise SSIMCPError("SSI_MCP_REDIRECT_URI bắt buộc phải cấu hình trong production")
        if not os.environ.get("SSI_MCP_TOKEN_ENCRYPTION_KEY", "").strip():
            raise SSIMCPError("SSI_MCP_TOKEN_ENCRYPTION_KEY bắt buộc phải cấu hình trong production")
    return configured or str(request.url_for("ssi_oauth_callback"))


def _session_binding(request: Request) -> str:
    raw = request.cookies.get("ta_session", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.get("/status")
def ssi_status(request: Request):
    require_admin(request.state.user)
    return ssi_oauth.status()


@router.get("/connect")
def ssi_connect(request: Request):
    require_admin(request.state.user)
    try:
        return RedirectResponse(ssi_oauth.authorization_url(
            _callback_url(request), request.state.user["id"], _session_binding(request)
        ), status_code=303)
    except SSIMCPError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/callback", name="ssi_oauth_callback")
def ssi_callback(request: Request, code: str, state: str):
    require_admin(request.state.user)
    try:
        ssi_oauth.exchange_code(
            code, state, request.state.user["id"], _session_binding(request)
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không thể kết nối SSI: {exc}") from exc
    return RedirectResponse("/account?ssi=connected", status_code=303)


@router.post("/disconnect")
def ssi_disconnect(request: Request):
    require_admin(request.state.user)
    revoked = ssi_oauth.disconnect()
    return {"connected": False, "revoked": revoked}
