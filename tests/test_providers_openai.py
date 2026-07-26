import time

import pytest

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import Validity
from opencode_swap.providers.openai import OpenAiProvider
from tests.helpers import make_jwt

provider = OpenAiProvider()
FRACTIONAL_EXPIRY = 1_730_000_000_000.5


def oauth_entry(**overrides):
    entry = {
        "type": "oauth",
        "refresh": "refresh-token",
        "access": make_jwt({"chatgpt_account_id": "acct-1"}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": "acct-1",
    }
    entry.update(overrides)
    return entry


def test_extract_none_when_no_openai_key():
    assert provider.extract({"anthropic": {"type": "api", "key": "x"}}) is None


def test_extract_valid_oauth():
    auth = {"openai": oauth_entry()}
    record = provider.extract(auth)
    assert record.type == "oauth"
    assert record.raw["accountId"] == "acct-1"


def test_extract_accepts_finite_fractional_expiry():
    record = provider.extract({"openai": oauth_entry(expires=FRACTIONAL_EXPIRY)})
    assert record.raw["expires"] == FRACTIONAL_EXPIRY


def test_extract_valid_api():
    auth = {"openai": {"type": "api", "key": "sk-abc"}}
    record = provider.extract(auth)
    assert record.type == "api"


def test_extract_valid_wellknown():
    auth = {"openai": {"type": "wellknown", "key": "k", "token": "t"}}
    record = provider.extract(auth)
    assert record.type == "wellknown"


def test_unknown_type_error_never_includes_untrusted_value():
    secret = "refresh-token-in-type-field"

    with pytest.raises(SchemaError) as exc_info:
        provider.extract({"openai": {"type": secret}})

    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "oauth", "access": "a", "expires": 1},  # missing refresh
        {"type": "oauth", "refresh": "r", "expires": 1},  # missing access
        {"type": "oauth", "refresh": "r", "access": "a"},  # missing expires
        {"type": "oauth", "refresh": "r", "access": "a", "expires": "not-a-number"},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": True},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": float("nan")},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": float("inf")},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 10**1000},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 1, "accountId": True},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 1, "enterpriseUrl": True},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 1, "unknown": float("nan")},
        {"type": "api"},  # missing key
        {"type": "api", "key": "k", "metadata": []},
        {"type": "api", "key": "k", "metadata": {"nested": float("nan")}},
        {"type": "wellknown", "key": "k"},  # missing token
        {"type": "totally-unknown"},
        {"no": "type field"},
        "not-a-dict",
    ],
)
def test_extract_rejects_malformed_shapes(entry):
    with pytest.raises(SchemaError):
        provider.extract({"openai": entry})


def test_splice_preserves_other_keys():
    auth = {"anthropic": {"type": "api", "key": "keep-me"}, "openai": oauth_entry(accountId="old")}
    record = provider.extract({"openai": oauth_entry(accountId="new")})
    result = provider.splice(auth, record)
    assert result["anthropic"] == {"type": "api", "key": "keep-me"}
    assert result["openai"]["accountId"] == "new"
    assert auth["openai"]["accountId"] == "old"  # original untouched


def test_identity_prefers_account_id():
    record = provider.extract({"openai": oauth_entry(accountId="acct-1", refresh="r1")})
    assert provider.identity(record) == "oauth-account\0acct-1"


def test_identity_derives_from_jwt_when_account_id_missing():
    entry = oauth_entry(refresh="r1")
    del entry["accountId"]
    record = provider.extract({"openai": entry})
    assert provider.identity(record) == "oauth-account\0acct-1"  # from JWT claim


def test_identity_falls_back_to_refresh_token():
    entry = oauth_entry(refresh="unique-refresh", access=make_jwt({}))
    del entry["accountId"]
    record = provider.extract({"openai": entry})
    assert provider.identity(record) == "oauth-refresh\0unique-refresh"


def test_identity_api_key():
    record = provider.extract({"openai": {"type": "api", "key": "sk-abc"}})
    assert provider.identity(record) == "api-key\0sk-abc"


def test_describe_oauth_with_and_without_email():
    with_email = provider.extract({"openai": oauth_entry(access=make_jwt({"chatgpt_account_id": "acct-1", "email": "a@example.test"}))})
    desc = provider.describe(with_email)
    assert desc.email == "a@example.test"
    assert desc.account_id == "acct-1"

    without_email = provider.extract({"openai": oauth_entry()})
    desc2 = provider.describe(without_email)
    assert desc2.email is None


def test_describe_suppresses_secret_or_terminal_metadata():
    entry = oauth_entry(access=make_jwt({"email": "refresh-token", "chatgpt_account_id": "bad\naccount"}))
    del entry["accountId"]
    record = provider.extract({"openai": entry})
    desc = provider.describe(record)
    assert desc.email is None
    assert desc.account_id is None


def test_describe_suppresses_metadata_containing_credential():
    entry = oauth_entry(refresh="refresh-token", access=make_jwt({"email": "refresh-token@example.test"}))
    record = provider.extract({"openai": entry})
    assert provider.describe(record).email is None


def test_validate_ok_expired_invalid():
    ok = provider.extract({"openai": oauth_entry()})
    assert provider.validate(ok) == Validity.OK

    expired = provider.extract({"openai": oauth_entry(expires=1)})
    assert provider.validate(expired) == Validity.EXPIRED

    api = provider.extract({"openai": {"type": "api", "key": "sk-abc"}})
    assert provider.validate(api) == Validity.OK
