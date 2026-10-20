"""In-memory OAuth 2.0/PKCE client for SSI's protected MCP resource.

Tokens intentionally live only for the current server process.  The module
uses RFC 9728 protected-resource metadata and RFC 8414 authorization-server
metadata, avoiding hard-coded SSI authorization endpoints.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

from .ssi_mcp import SSI_MCP_URL, SSIMCPError


@dataclass
class PendingAuthorization:
    verifier: str
    redirect_uri: str
    created_at: float
    owner_id: str
    session_binding: str


class SSIOAuthClient:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metadata: dict[str, Any] | None = None
        self._token: dict[str, Any] = {}
        self._pending: dict[str, PendingAuthorization] = {}
        self._dynamic_client: dict[str, Any] = {}
        self._load_persisted()

    @staticmethod
    def _token_path() -> Path:
        root = Path(os.environ.get("TA_DATA_DIR") or Path.home() / ".tradingagents")
        return root / "config" / "ssi-oauth.enc"

    @staticmethod
    def _cipher() -> Fernet | None:
        key = os.environ.get("SSI_MCP_TOKEN_ENCRYPTION_KEY", "").strip()
        return Fernet(key.encode("ascii")) if key else None

    def _load_persisted(self) -> None:
        cipher = self._cipher()
        path = self._token_path()
        if cipher is None or not path.exists():
            return
        try:
            value = json.loads(cipher.decrypt(path.read_bytes()).decode("utf-8"))
            if isinstance(value, dict):
                self._token = value.get("token") or {}
                self._dynamic_client = value.get("client") or {}
        except (OSError, ValueError, InvalidToken, json.JSONDecodeError):
            return

    def _persist(self) -> None:
        cipher = self._cipher()
        if cipher is None:
            return
        path = self._token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"token": self._token, "client": self._dynamic_client}).encode("utf-8")
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(cipher.encrypt(payload))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _get_json(self, url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise SSIMCPError(f"OAuth metadata at {url} is not an object")
        return value

    def metadata(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._metadata and not force:
                return dict(self._metadata)
        parsed = urlparse(SSI_MCP_URL)
        resource_metadata_url = (
            f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"
            f"{parsed.path.rstrip('/')}"
        )
        try:
            resource = self._get_json(resource_metadata_url)
        except requests.RequestException:
            resource = self._get_json(f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource")
        issuers = resource.get("authorization_servers") or []
        if not issuers:
            raise SSIMCPError("SSI OAuth metadata does not advertise an authorization server")
        issuer = str(issuers[0]).rstrip("/")
        server = self._get_json(f"{issuer}/.well-known/oauth-authorization-server")
        merged = {**server, "resource": resource.get("resource") or SSI_MCP_URL}
        for required in ("authorization_endpoint", "token_endpoint"):
            if not merged.get(required):
                raise SSIMCPError(f"SSI OAuth metadata is missing {required}")
        with self._lock:
            self._metadata = merged
        return dict(merged)

    @staticmethod
    def _challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorization_url(
        self, redirect_uri: str, owner_id: str = "server", session_binding: str = "server"
    ) -> str:
        metadata = self.metadata()
        client = self._client_credentials(metadata, redirect_uri)
        client_id = client["client_id"]
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        with self._lock:
            self._pending[state] = PendingAuthorization(
                verifier, redirect_uri, time.time(), owner_id, session_binding
            )
            self._pending = {
                key: value for key, value in self._pending.items()
                if time.time() - value.created_at < 600
            }
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": self._challenge(verifier),
            "code_challenge_method": "S256",
            "resource": metadata["resource"],
        }
        params["scope"] = "market-data:read"
        return f"{metadata['authorization_endpoint']}?{urlencode(params)}"

    def _client_credentials(self, metadata: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
        configured = os.environ.get("SSI_MCP_CLIENT_ID", "").strip()
        if configured:
            return {
                "client_id": configured,
                "client_secret": os.environ.get("SSI_MCP_CLIENT_SECRET", "").strip(),
            }
        with self._lock:
            if self._dynamic_client.get("redirect_uri") == redirect_uri:
                return dict(self._dynamic_client)
        registration_endpoint = metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise SSIMCPError("SSI OAuth không hỗ trợ đăng ký client động và SSI_MCP_CLIENT_ID chưa được cấu hình")
        response = requests.post(
            registration_endpoint,
            json={
                "client_name": "TradingAgentsVN",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            timeout=15,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or not value.get("client_id"):
            raise SSIMCPError("SSI dynamic client registration did not return client_id")
        client = {**value, "redirect_uri": redirect_uri}
        with self._lock:
            self._dynamic_client = client
            self._persist()
        return client

    def _active_client(self) -> dict[str, Any]:
        configured = os.environ.get("SSI_MCP_CLIENT_ID", "").strip()
        if configured:
            return {"client_id": configured,
                    "client_secret": os.environ.get("SSI_MCP_CLIENT_SECRET", "").strip()}
        with self._lock:
            if self._dynamic_client.get("client_id"):
                return dict(self._dynamic_client)
        raise SSIMCPError("SSI OAuth client is not initialized")

    def exchange_code(
        self, code: str, state: str, owner_id: str = "server", session_binding: str = "server"
    ) -> None:
        with self._lock:
            pending = self._pending.pop(state, None)
        if (
            pending is None
            or time.time() - pending.created_at >= 600
            or pending.owner_id != owner_id
            or not secrets.compare_digest(pending.session_binding, session_binding)
        ):
            raise SSIMCPError("SSI OAuth state is invalid or expired")
        metadata = self.metadata()
        client = self._active_client()
        payload = {
            "grant_type": "authorization_code",
            "client_id": client["client_id"],
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.verifier,
            "resource": metadata["resource"],
        }
        secret = str(client.get("client_secret") or "").strip()
        if secret:
            payload["client_secret"] = secret
        response = requests.post(metadata["token_endpoint"], data=payload, timeout=15)
        response.raise_for_status()
        self._store_token(response.json())

    def _store_token(self, value: dict[str, Any]) -> None:
        if not value.get("access_token"):
            raise SSIMCPError("SSI token response did not contain an access token")
        token = dict(value)
        returned_scope = set(str(token.get("scope") or "market-data:read").split())
        if "market-data:read" not in returned_scope:
            raise SSIMCPError("SSI token does not grant the required market-data:read scope")
        token["expires_at"] = time.time() + max(0, int(token.get("expires_in", 3600)) - 30)
        with self._lock:
            self._token = token
            self._persist()

    def _refresh(self) -> None:
        metadata = self.metadata()
        refresh_token = self._token.get("refresh_token")
        if not refresh_token:
            raise SSIMCPError("SSI OAuth session expired and has no refresh token")
        client = self._active_client()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client["client_id"],
            "resource": metadata["resource"],
        }
        secret = str(client.get("client_secret") or "").strip()
        if secret:
            payload["client_secret"] = secret
        response = requests.post(metadata["token_endpoint"], data=payload, timeout=15)
        response.raise_for_status()
        refreshed = response.json()
        refreshed.setdefault("refresh_token", refresh_token)
        self._store_token(refreshed)

    def access_token(self) -> str:
        # Explicit token remains useful for non-interactive tests and debugging.
        debug_token = os.environ.get("SSI_MCP_ACCESS_TOKEN", "").strip()
        if debug_token:
            return debug_token
        self._load_persisted()
        with self._lock:
            expires_at = float(self._token.get("expires_at", 0))
        if expires_at <= time.time():
            self._refresh()
        with self._lock:
            token = str(self._token.get("access_token", ""))
        if not token:
            raise SSIMCPError("SSI chưa được kết nối OAuth")
        return token

    def status(self) -> dict[str, Any]:
        with self._lock:
            connected = bool(self._token.get("access_token"))
            expires_at = self._token.get("expires_at")
        return {"connected": connected, "expires_at": expires_at,
                "mode": "server", "persistent": self._cipher() is not None,
                "debug_token": bool(os.environ.get("SSI_MCP_ACCESS_TOKEN", "").strip())}

    def disconnect(self) -> bool:
        revoked = False
        token = self._token.get("refresh_token") or self._token.get("access_token")
        if token:
            try:
                metadata = self.metadata()
                endpoint = metadata.get("revocation_endpoint")
                if endpoint:
                    client = self._active_client()
                    payload = {"token": token, "client_id": client["client_id"]}
                    if client.get("client_secret"):
                        payload["client_secret"] = client["client_secret"]
                    response = requests.post(endpoint, data=payload, timeout=15)
                    response.raise_for_status()
                    revoked = True
            except requests.RequestException:
                revoked = False
        with self._lock:
            self._token.clear()
            self._pending.clear()
            path = self._token_path()
            if path.exists():
                path.unlink()
        return revoked


ssi_oauth = SSIOAuthClient()
