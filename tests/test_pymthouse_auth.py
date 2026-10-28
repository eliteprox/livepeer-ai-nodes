"""PymtHouse public-client auth against a mocked token service."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client

_AUTH_PATH = Path(__file__).resolve().parents[1] / "nodes" / "pymthouse_auth.py"
_SPEC = importlib.util.spec_from_file_location("pymthouse_auth_under_test", _AUTH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
auth = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = auth
_SPEC.loader.exec_module(auth)

ISSUER = "https://issuer.example/api/v1/oidc"
CLIENT_ID = "app_test"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def _script(responses: list[tuple[int, dict]]):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status, payload = responses.pop(0)
        return httpx.Response(status, json=payload)

    return calls, httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> tuple[object, AsyncOAuth2Client]:
    http = AsyncOAuth2Client(
        client_id=CLIENT_ID,
        token_endpoint_auth_method="none",
        transport=transport,
        follow_redirects=False,
    )
    settings = auth.PymtHouseSettings(
        issuer_url=ISSUER,
        client_id=CLIENT_ID,
    )
    return auth.PymtHouseClient(settings, http=http), http


def _discovery() -> tuple[int, dict]:
    return 200, {
        "issuer": ISSUER,
        "device_authorization_endpoint": f"{ISSUER}/device/auth",
        "token_endpoint": f"{ISSUER}/token",
    }


@pytest.mark.asyncio
async def test_device_start_sends_public_client_and_scope():
    calls, transport = _script([
        _discovery(),
        (200, {
            "device_code": "device-1",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://issuer.example/oidc/device",
            "verification_uri_complete": "https://issuer.example/oidc/device?user_code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 5,
        }),
    ])
    client, http = _client(transport)
    try:
        device = await client.start_device_login()
    finally:
        await http.aclose()

    assert device.user_code == "ABCD-EFGH"
    assert device.verification_uri_complete.startswith("https://issuer.example/oidc/device")
    started = calls[1]
    assert started.method == "POST"
    assert started.url.path.endswith("/device/auth")
    body = _form(started)
    assert body["client_id"] == [CLIENT_ID]
    assert body["scope"] == ["openid sign:job"]
    assert body["resource"] == [ISSUER]


@pytest.mark.asyncio
async def test_poll_waits_through_pending_and_slow_down():
    calls, transport = _script([
        _discovery(),
        (400, {"error": "authorization_pending"}),
        (400, {"error": "slow_down"}),
        (200, {
            "access_token": "user-jwt",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    ])
    client, http = _client(transport)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    device = auth.DeviceAuthorization(
        device_code="device-1",
        user_code="ABCD-EFGH",
        verification_uri="https://issuer.example/oidc/device",
        verification_uri_complete="https://issuer.example/oidc/device?user_code=ABCD-EFGH",
        expires_in=600,
        interval=5,
    )
    try:
        token = await client.poll_device_token(device, sleep=sleep, now=lambda: 0.0)
    finally:
        await http.aclose()

    assert token["access_token"] == "user-jwt"
    assert waits == [5.0, 10.0]
    poll = _form(calls[-1])
    assert poll["grant_type"] == [DEVICE_GRANT]
    assert poll["device_code"] == ["device-1"]
    assert poll["client_id"] == [CLIENT_ID]
    assert poll["resource"] == [ISSUER]


@pytest.mark.asyncio
async def test_exchange_posts_user_jwt_without_client_secret():
    calls, transport = _script([
        (200, {
            "access_token": "signer-jwt",
            "expires_in": 300,
            "token_type": "Bearer",
            "scope": "sign:job",
            "signer_url": "https://signer.example",
        }),
    ])
    client, http = _client(transport)
    try:
        session = await client.exchange_signer_session("user-jwt")
    finally:
        await http.aclose()

    assert session["signer_url"] == "https://signer.example"
    request = calls[0]
    assert request.url.path == f"/api/v1/apps/{CLIENT_ID}/oidc/token"
    body = _form(request)
    assert body["grant_type"] == [EXCHANGE_GRANT]
    assert body["subject_token"] == ["user-jwt"]
    assert body["subject_token_type"] == [ACCESS_TOKEN_TYPE]
    assert "client_secret" not in body
    assert "authorization" not in {key.lower() for key in request.headers}


@pytest.mark.asyncio
async def test_refresh_uses_public_client_and_refresh_token():
    calls, transport = _script([
        _discovery(),
        (200, {
            "access_token": "user-jwt-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
            "token_type": "Bearer",
        }),
    ])
    client, http = _client(transport)
    try:
        token = await client.refresh_access_token("refresh-1")
    finally:
        await http.aclose()

    assert token["refresh_token"] == "refresh-2"
    body = _form(calls[1])
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["refresh-1"]
    assert body["client_id"] == [CLIENT_ID]
    assert body["resource"] == [ISSUER]
    assert "client_secret" not in body


@pytest.mark.asyncio
async def test_usage_queries_send_user_bearer():
    calls, transport = _script([
        (200, {"totals": {"requestCount": 2, "totalFeeWei": "0"}, "source": "openmeter"}),
        (200, {"remainingUsdMicros": 1500000, "balanceUsdMicros": 1500000}),
    ])
    client, http = _client(transport)
    try:
        usage, balance = await client.fetch_usage("user-jwt")
    finally:
        await http.aclose()

    assert usage["totals"]["requestCount"] == 2
    assert balance["remainingUsdMicros"] == 1500000
    assert [request.url.path for request in calls] == [
        "/api/v1/user/usage",
        "/api/v1/user/usage/balance",
    ]
    for request in calls:
        assert request.headers["authorization"] == "Bearer user-jwt"
    assert auth.format_usage_summary(usage, balance) == "$1.50 remaining, 2 requests"


def test_bearer_dict_is_updated_in_place():
    headers = {"Authorization": "Bearer old"}
    auth.set_bearer(headers, "signer-jwt")
    assert headers == {"Authorization": "Bearer signer-jwt"}


def test_settings_require_issuer_and_public_client_id():
    with pytest.raises(auth.PymtHouseAuthError):
        auth.load_settings_from_env({})
    settings = auth.load_settings_from_env({
        "PYMTHOUSE_ISSUER_URL": ISSUER,
        "PYMTHOUSE_CLIENT_ID": CLIENT_ID,
    })
    assert settings.client_id == CLIENT_ID
    assert auth.api_origin(ISSUER) == "https://issuer.example"


def test_verification_url_must_be_http():
    with pytest.raises(auth.PymtHouseAuthError):
        auth.public_verification_url("javascript:alert(1)")


def test_verification_url_stays_on_the_issuer_origin():
    rebuilt = auth.same_origin_verification_url(
        "https://issuer.example/oidc/device?user_code=ABCD-EFGH",
        ISSUER,
    )
    assert rebuilt == "https://issuer.example/oidc/device?user_code=ABCD-EFGH"
    with pytest.raises(auth.PymtHouseAuthError):
        auth.same_origin_verification_url("https://evil.example/oidc/device", ISSUER)
