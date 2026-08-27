import http.client
import json
import urllib.error

from opencode_swap import cli, usage


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


class TruncatedResponse(FakeResponse):
    def read(self):
        raise http.client.IncompleteRead(b"partial", 999)


def _payload(**overrides):
    body = {
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800},
            "secondary_window": {"used_percent": 49, "reset_at": 1785400000, "limit_window_seconds": 18000},
        },
    }
    body.update(overrides)
    return json.dumps(body).encode()


def test_success_extracts_both_windows_sorted_shortest_first(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload()))
    snap = usage.fetch_openai_oauth_usage("access-token", "acct-1")
    assert snap.available is True
    assert snap.plan_name == "ChatGPT Plus"
    assert len(snap.windows) == 2
    five_hour, seven_day = snap.windows
    assert five_hour.window_seconds == 18000
    assert five_hour.used_percent == 49
    assert five_hour.reset_at == 1785400000 * 1000
    assert seven_day.window_seconds == 604800
    assert seven_day.used_percent == 42
    assert seven_day.reset_at == 1785500000 * 1000  # seconds -> ms


def test_windows_sort_by_duration_even_when_primary_is_the_longer_window(monkeypatch):
    # The API's own key names (primary/secondary) don't reliably identify
    # which window is which; sorting must key off limit_window_seconds, not
    # dict order or key name.
    body = json.dumps(
        {
            "rate_limit": {
                "primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800},
                "secondary_window": {"used_percent": 49, "reset_at": 1785400000, "limit_window_seconds": 18000},
            }
        }
    ).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert [w.window_seconds for w in snap.windows] == [18000, 604800]


def test_third_window_under_an_unknown_key_is_still_discovered(monkeypatch):
    body = json.dumps(
        {
            "rate_limit": {
                "primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800},
                "tertiary_window": {"used_percent": 5, "reset_at": 1785300000, "limit_window_seconds": 3600},
            }
        }
    ).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert [w.window_seconds for w in snap.windows] == [3600, 604800]


def test_single_window_response_still_works(monkeypatch):
    body = json.dumps({"rate_limit": {"primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800}}}).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert len(snap.windows) == 1
    assert snap.windows[0].window_seconds == 604800


def test_non_window_rate_limit_entries_are_skipped(monkeypatch):
    body = json.dumps(
        {
            "rate_limit": {
                "primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800},
                "some_flag": True,
                "some_note": "not a window",
                "empty_dict": {},
            }
        }
    ).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert len(snap.windows) == 1


def test_sibling_code_review_rate_limit_is_ignored(monkeypatch):
    body = json.dumps(
        {
            "rate_limit": {"primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800}},
            "code_review_rate_limit": {"primary_window": {"used_percent": 99, "reset_at": 1785500000, "limit_window_seconds": 604800}},
        }
    ).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert len(snap.windows) == 1
    assert snap.windows[0].used_percent == 42


def test_reset_at_already_in_milliseconds_passed_through(monkeypatch):
    body = json.dumps({"rate_limit": {"primary_window": {"used_percent": 10, "reset_at": 1785500000123}}}).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.windows[0].reset_at == 1785500000123


def test_unknown_plan_type_is_not_displayed(monkeypatch):
    secret = "access-token-leaked-by-server"
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(_payload(plan_type=secret)))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.plan_name is None
    assert secret not in cli._format_usage(snap)


def test_missing_rate_limit_still_available_but_no_windows(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps({}).encode()))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is True
    assert snap.windows == ()


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


def test_invalid_utf8_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"\xff"))
    assert usage.fetch_openai_oauth_usage("access-token", None).available is False


def test_nonfinite_or_huge_usage_values_are_rejected(monkeypatch):
    # A NaN used_percent means the whole window can't be trusted -- it's
    # dropped entirely rather than surfaced with a fabricated percent.
    body = b'{"rate_limit":{"primary_window":{"used_percent":NaN,"reset_at":1e300,"limit_window_seconds":Infinity}}}'
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.windows == ()
    assert (
        cli._format_usage(
            usage.UsageSnapshot(available=True, windows=(usage.UsageWindow(used_percent=1, reset_at=float("inf"), window_seconds=None),))
        )
        == "  usage: 1%"
    )
    assert usage._reset_at_millis(10**1000) is None


def test_second_window_poisoned_does_not_affect_the_first(monkeypatch):
    body = json.dumps(
        {
            "rate_limit": {
                "primary_window": {"used_percent": 42, "reset_at": 1785500000, "limit_window_seconds": 604800},
                "secondary_window": {"used_percent": float("nan"), "reset_at": 1785400000, "limit_window_seconds": 18000},
            }
        },
        allow_nan=True,
    ).encode()
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert len(snap.windows) == 1
    assert snap.windows[0].used_percent == 42


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


def test_truncated_response_body_returns_unavailable(monkeypatch):
    # http.client.IncompleteRead is not an OSError; it must still be caught.
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: TruncatedResponse(b""))
    snap = usage.fetch_openai_oauth_usage("access-token", None)
    assert snap.available is False
    assert snap.message == "HTTP protocol error"


# --- Z.AI GLM Coding Plan ---------------------------------------------------

# The real /api/monitor/usage/quota/limit response shape (key redacted).
_ZAI_PAYLOAD = {
    "code": 200,
    "msg": "Operation successful",
    "data": {
        "limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 2000, "currentValue": 167, "percentage": 8, "nextResetTime": 1787862798247},
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 10000, "currentValue": 1245, "percentage": 12, "nextResetTime": 1788357330998},
        ],
        "level": "lite",
    },
    "success": True,
}


def _zai_response(body):
    return lambda *a, **k: FakeResponse(json.dumps(body).encode())


def test_zai_success_derives_windows_from_unit_and_number(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(_ZAI_PAYLOAD))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is True
    assert snap.plan_name == "GLM Coding Lite"
    assert [w.window_seconds for w in snap.windows] == [18000, 604800]  # 5h then 7d, from number*unit
    five_hour, seven_day = snap.windows
    assert five_hour.used_percent == 8
    assert five_hour.reset_at == 1787862798247  # already ms, passed through
    assert seven_day.used_percent == 12


def test_zai_time_limit_entry_is_excluded(monkeypatch):
    body = {
        "data": {
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247},
                {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 45, "nextResetTime": 1788357330998},
            ]
        }
    }
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert [w.used_percent for w in snap.windows] == [8]


def test_zai_legacy_tokens_limit_type_is_accepted(monkeypatch):
    body = {"data": {"limits": [{"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 30, "nextResetTime": 1788357330998}]}}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert len(snap.windows) == 1
    assert snap.windows[0].window_seconds == 604800


def test_zai_limit_kind_from_legacy_name_key_is_accepted(monkeypatch):
    body = {"data": {"limits": [{"name": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247}]}}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert [w.used_percent for w in snap.windows] == [8]


def test_zai_recognized_limit_with_unreadable_percentage_is_unavailable(monkeypatch):
    body = {"data": {"limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "nextResetTime": 1787862798247}]}}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.message == "unexpected response shape"


def test_zai_unrelated_entry_without_percentage_does_not_poison_the_snapshot(monkeypatch):
    body = {
        "data": {
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247},
                {"type": "TIME_LIMIT", "unit": 5, "number": 1},  # no percentage, but not a percentage-quota kind
            ]
        }
    }
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is True
    assert [w.used_percent for w in snap.windows] == [8]


def test_zai_incomplete_read_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: TruncatedResponse(b""))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.message == "HTTP protocol error"


def test_zai_unknown_unit_keeps_window_without_duration_sorted_last(monkeypatch):
    body = {
        "data": {
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 99, "number": 1, "percentage": 70, "nextResetTime": 1788357330998},
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247},
            ]
        }
    }
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert [w.window_seconds for w in snap.windows] == [18000, None]
    assert [w.used_percent for w in snap.windows] == [8, 70]


def test_zai_out_of_range_percentage_on_a_quota_entry_is_unavailable(monkeypatch):
    # 150 isn't a usable percentage; on a recognized CREDIT_LIMIT entry that
    # makes the whole response untrustworthy rather than a partial snapshot.
    body = {
        "data": {
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 150, "nextResetTime": 1787862798247},
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "percentage": 12, "nextResetTime": 1788357330998},
            ]
        }
    }
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.message == "unexpected response shape"


def test_zai_root_level_limits_array_is_tolerated(monkeypatch):
    body = {"limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247}]}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is True
    assert len(snap.windows) == 1


def test_zai_no_coding_plan_reports_its_own_reason(monkeypatch):
    body = {"success": False, "code": 500, "msg": "The account has no active coding plan"}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.message == "no active GLM coding plan"


def test_zai_other_success_false_response_is_unavailable_not_empty(monkeypatch):
    body = {"success": False, "code": 503, "msg": "service unavailable"}
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.windows == ()
    assert "service unavailable" not in snap.message  # server msg is not trusted into output


def test_zai_successful_response_without_a_limits_array_is_unexpected_shape(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response({"code": 200, "data": {"level": "lite"}}))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert snap.message == "unexpected response shape"


def test_zai_empty_limits_array_is_a_valid_no_usage_snapshot(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response({"data": {"level": "lite", "limits": []}}))
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is True
    assert snap.windows == ()


def test_zai_credential_never_leaks_through_a_header_valueerror(monkeypatch):
    # http.client raises ValueError("Invalid header value <the bearer value>")
    # when a header contains CR/LF; that string must not reach snap.message.
    secret = "zai-key\r\nX-Injected: 1"

    def raise_header_valueerror(*a, **k):
        raise ValueError(f"Invalid header value b'Bearer {secret}'")

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_header_valueerror)
    snap = usage.fetch_zai_usage(secret)
    assert snap.available is False
    assert "zai-key" not in snap.message and "X-Injected" not in snap.message


def test_openai_access_token_never_leaks_through_a_header_valueerror(monkeypatch):
    secret = "tok\r\nX-Injected: 1"

    def raise_header_valueerror(*a, **k):
        raise ValueError(f"Invalid header value b'Bearer {secret}'")

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_header_valueerror)
    snap = usage.fetch_openai_oauth_usage(secret, None)
    assert snap.available is False
    assert secret not in snap.message


def test_zai_unknown_level_is_not_displayed(monkeypatch):
    body = {
        "data": {"level": "galaxy", "limits": [{"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 8, "nextResetTime": 1787862798247}]}
    }
    monkeypatch.setattr(usage.urllib.request, "urlopen", _zai_response(body))
    assert usage.fetch_zai_usage("zai-key").plan_name is None


def test_zai_http_error_returns_unavailable(monkeypatch):
    def raise_http_error(*a, **k):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_http_error)
    snap = usage.fetch_zai_usage("zai-key")
    assert snap.available is False
    assert "401" in snap.message


def test_zai_malformed_json_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"not json"))
    assert usage.fetch_zai_usage("zai-key").available is False


def test_zai_non_dict_body_returns_unavailable(monkeypatch):
    monkeypatch.setattr(usage.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps([1, 2]).encode()))
    assert usage.fetch_zai_usage("zai-key").available is False


def test_zai_api_key_never_leaks_into_message_on_failure(monkeypatch):
    secret = "super-secret-zai-api-key-xyz"

    def raise_error(*a, **k):
        raise urllib.error.URLError("generic network failure")

    monkeypatch.setattr(usage.urllib.request, "urlopen", raise_error)
    assert secret not in usage.fetch_zai_usage(secret).message
