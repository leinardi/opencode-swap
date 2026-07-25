import json
import urllib.error

from opencode_swap import usage


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
        "plan_type": "plus",
        "rate_limit": {"primary_window": {"used_percent": 42, "reset_at": 1785500000}},
    }
    body.update(overrides)
    return json.dumps(body).encode()


def test_success_extracts_percent_plan_and_reset(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload()))
    snap = usage.fetch_openai_oauth_usage("access-token", "acct-1")
    assert snap.available is True
    assert snap.used_percent == 42
    assert snap.plan_name == "ChatGPT Plus"
    assert snap.reset_at == 1785500000 * 1000  # seconds -> ms


def test_reset_at_already_in_milliseconds_passed_through(monkeypatch):
    body = json.dumps({"rate_limit": {"primary_window": {"used_percent": 10, "reset_at": 1785500000123}}}).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.reset_at == 1785500000123


def test_unknown_plan_type_passed_through_verbatim(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload(plan_type="mystery")))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.plan_name == "mystery"


def test_missing_rate_limit_still_available_but_no_percent(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps({}).encode()))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is True
    assert snap.used_percent is None
    assert snap.reset_at is None


def test_http_error_returns_unavailable(monkeypatch):
    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_http_error)
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is False
    assert "401" in snap.message


def test_network_error_returns_unavailable(monkeypatch):
    def raise_url_error(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_url_error)
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is False


def test_malformed_json_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"not json"))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is False


def test_non_dict_body_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps([1, 2]).encode()))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is False


def test_account_id_header_only_set_when_present(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        return FakeResponse(_payload())

    monkeypatch.setattr(usage.urllib.request, "urlopen", fake_urlopen)
    usage.fetch_openai_oauth_usage("access-token", "acct-1")
    assert captured["headers"].get("Chatgpt-account-id") == "acct-1"

    usage.fetch_openai_oauth_usage("access-token", None)
    assert "Chatgpt-account-id" not in captured["headers"]


def test_access_token_never_leaks_into_message_on_failure(monkeypatch):
    secret = "super-secret-access-token-xyz"

    def raise_error(*a, **k):
        raise urllib.error.URLError("generic network failure")

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_error)
    snap = usage.fetch_openai_oauth_usage(secret, None)
    assert secret not in snap.message
