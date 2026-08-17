import json
import urllib.error

import pytest

from opencode_swap import oauth_refresh
from opencode_swap.exceptions import RefreshError
from tests.helpers import make_jwt


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _payload(**overrides):
    body = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
    }
    body.update(overrides)
    return json.dumps(body).encode()


def test_success_extracts_rotated_tokens_and_expiry(monkeypatch):
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload()))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=1_000_000.0)
    assert result.access == "new-access"
    assert result.refresh == "new-refresh"
    assert result.expires == 1_000_000 + 3600 * 1000
    assert isinstance(result.expires, int)


def test_expires_in_defaults_when_missing(monkeypatch):
    body = {"access_token": "a", "refresh_token": "r"}
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(body).encode()))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.expires == oauth_refresh._DEFAULT_EXPIRES_IN * 1000


@pytest.mark.parametrize("bad_expires_in", [0, -5, "not-a-number", True, float("inf"), float("-inf"), float("nan")])
def test_expires_in_defaults_when_invalid(monkeypatch, bad_expires_in):
    monkeypatch.setattr(
        oauth_refresh.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(_payload(expires_in=bad_expires_in)),
    )
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.expires == oauth_refresh._DEFAULT_EXPIRES_IN * 1000


def test_expires_in_overflowing_json_literal_falls_back_to_default(monkeypatch):
    """1e400 is a syntactically ordinary JSON number that overflows to
    float('inf') while parsing -- must not silently produce a non-finite
    `expires` that later fails `require_expiry`'s finite check when the
    persisted record is reloaded, after the old refresh token this grant
    consumed is already dead."""
    body = b'{"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1e400}'
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.expires == oauth_refresh._DEFAULT_EXPIRES_IN * 1000


def test_expires_in_huge_integer_overflowing_float_conversion_falls_back_to_default(monkeypatch):
    """A huge JSON integer literal (arbitrary precision in Python) can raise
    OverflowError when converted to float, rather than producing inf/nan --
    a distinct failure mode from the float-literal-overflow case above."""
    body = ('{"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": %s}' % ("9" * 400)).encode()
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.expires == oauth_refresh._DEFAULT_EXPIRES_IN * 1000


def test_result_expires_is_always_finite(monkeypatch):
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload(expires_in=3600)))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=1_000_000.0)
    assert oauth_refresh.math.isfinite(result.expires)


def test_fractional_now_ms_still_yields_an_integer_expiry(monkeypatch):
    """`time.time() * 1000` carries a sub-millisecond fraction. OpenCode's
    auth schema types `expires` as NonNegativeInt and drops entries that
    fail to decode without reporting it, so a fractional expiry makes the
    whole provider vanish from OpenCode rather than raising anywhere."""
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload()))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=1_000_000.4)
    assert isinstance(result.expires, int)
    assert result.expires == 1_000_000 + 3600 * 1000


def test_fractional_expires_in_truncates_rather_than_rounding_up(monkeypatch):
    """Truncation keeps the expiry from ever landing after the real one,
    which would send a request with an already-dead access token."""
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload(expires_in=1.9999)))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.expires == 1999


def test_account_id_from_id_token_preferred_over_access_token(monkeypatch):
    id_token = make_jwt({"chatgpt_account_id": "acct-from-id-token"})
    access_token = make_jwt({"chatgpt_account_id": "acct-from-access-token"})
    body = _payload(access_token=access_token, id_token=id_token)
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.account_id == "acct-from-id-token"


def test_account_id_falls_back_to_access_token_when_no_id_token(monkeypatch):
    access_token = make_jwt({"chatgpt_account_id": "acct-from-access-token"})
    body = _payload(access_token=access_token)
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.account_id == "acct-from-access-token"


def test_account_id_none_when_undeterminable(monkeypatch):
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload()))
    result = oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert result.account_id is None


@pytest.mark.parametrize("status", [400, 401])
def test_rejected_grant_raises_refresh_error_with_recovery_hint(monkeypatch, status):
    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError("url", status, "rejected", {}, None)

    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", raise_http_error)
    with pytest.raises(RefreshError, match="opencode auth login"):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_other_http_error_raises_refresh_error(monkeypatch):
    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError("url", 500, "boom", {}, None)

    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", raise_http_error)
    with pytest.raises(RefreshError, match="500"):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_network_error_raises_refresh_error(monkeypatch):
    def raise_url_error(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", raise_url_error)
    with pytest.raises(RefreshError):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_malformed_json_raises_refresh_error(monkeypatch):
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"not json"))
    with pytest.raises(RefreshError):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_non_dict_body_raises_refresh_error(monkeypatch):
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps([1, 2]).encode()))
    with pytest.raises(RefreshError):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_missing_access_token_raises_refresh_error(monkeypatch):
    body = {"refresh_token": "r"}
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(body).encode()))
    with pytest.raises(RefreshError):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_missing_refresh_token_raises_refresh_error(monkeypatch):
    body = {"access_token": "a"}
    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(body).encode()))
    with pytest.raises(RefreshError):
        oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)


def test_refresh_token_never_leaks_into_error_message(monkeypatch):
    secret = "super-secret-refresh-token-xyz"

    def raise_error(*a, **k):
        raise urllib.error.URLError("generic network failure")

    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", raise_error)
    with pytest.raises(RefreshError) as exc_info:
        oauth_refresh.refresh_openai_oauth(secret, now_ms=0.0)
    assert secret not in str(exc_info.value)


def test_request_uses_grant_type_and_client_id(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode()
        captured["content_type"] = request.get_header("Content-type")
        return FakeResponse(_payload())

    monkeypatch.setattr(oauth_refresh.urllib.request, "urlopen", fake_urlopen)
    oauth_refresh.refresh_openai_oauth("old-refresh", now_ms=0.0)
    assert captured["url"] == f"{oauth_refresh.ISSUER}/oauth/token"
    assert "grant_type=refresh_token" in captured["body"]
    assert "refresh_token=old-refresh" in captured["body"]
    assert f"client_id={oauth_refresh.CLIENT_ID}" in captured["body"]
    assert captured["content_type"] == "application/x-www-form-urlencoded"
