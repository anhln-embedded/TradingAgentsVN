import time

from tradingagents.dataflows.ssi_oauth import PendingAuthorization, SSIOAuthClient


class Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        return None

    def json(self):
        return self.value


def test_authorization_uses_pkce_and_discovered_endpoint(monkeypatch):
    client = SSIOAuthClient()
    monkeypatch.setenv("SSI_MCP_CLIENT_ID", "client-1")
    monkeypatch.setattr(client, "metadata", lambda force=False: {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "resource": "https://mcp.ssi.com.vn/mcp",
    })
    url = client.authorization_url("https://app.example/api/integrations/ssi/callback")
    assert url.startswith("https://login.example/authorize?")
    assert "code_challenge_method=S256" in url
    assert "client_id=client-1" in url
    assert client._pending


def test_dynamic_registration_when_client_id_is_not_configured(monkeypatch):
    client = SSIOAuthClient()
    monkeypatch.delenv("SSI_MCP_CLIENT_ID", raising=False)
    monkeypatch.setattr(client, "metadata", lambda force=False: {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "registration_endpoint": "https://login.example/register",
        "resource": "https://mcp.ssi.com.vn/mcp",
    })
    monkeypatch.setattr("tradingagents.dataflows.ssi_oauth.requests.post", lambda *args, **kwargs: Response({
        "client_id": "dynamic-client",
    }))
    url = client.authorization_url("https://app.example/callback")
    assert "client_id=dynamic-client" in url
    assert client._dynamic_client["redirect_uri"] == "https://app.example/callback"


def test_expired_access_token_is_refreshed(monkeypatch):
    client = SSIOAuthClient()
    client._metadata = {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "resource": "https://mcp.ssi.com.vn/mcp",
    }
    client._token = {"access_token": "old", "refresh_token": "refresh", "expires_at": time.time() - 1}
    monkeypatch.delenv("SSI_MCP_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("SSI_MCP_CLIENT_ID", "client-1")
    monkeypatch.setattr("tradingagents.dataflows.ssi_oauth.requests.post", lambda *args, **kwargs: Response({
        "access_token": "new", "expires_in": 3600,
    }))
    assert client.access_token() == "new"
    assert client._token["refresh_token"] == "refresh"


def test_callback_state_is_single_use(monkeypatch):
    client = SSIOAuthClient()
    client._metadata = {
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "resource": "https://mcp.ssi.com.vn/mcp",
    }
    client._pending["state"] = PendingAuthorization(
        "verifier", "https://app/callback", time.time(), "server", "server"
    )
    monkeypatch.setenv("SSI_MCP_CLIENT_ID", "client-1")
    monkeypatch.setattr("tradingagents.dataflows.ssi_oauth.requests.post", lambda *args, **kwargs: Response({
        "access_token": "token", "expires_in": 3600,
    }))
    client.exchange_code("code", "state")
    assert "state" not in client._pending
    assert client.status()["connected"]
