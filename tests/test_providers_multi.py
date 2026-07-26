import time

import pytest

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import Validity
from opencode_swap.providers import get_provider
from tests.helpers import make_jwt

FRACTIONAL_EXPIRY = 1_730_000_000_000.5


def test_generic_api_provider_preserves_string_metadata():
    provider = get_provider("anthropic")
    auth = {"anthropic": {"type": "api", "key": "secret", "metadata": {"region": "us"}}}

    record = provider.extract(auth)

    assert record is not None
    assert provider.splice({"openai": {"type": "api", "key": "keep"}}, record) == {
        "openai": {"type": "api", "key": "keep"},
        "anthropic": auth["anthropic"],
    }
    assert provider.identity_is_stable(record)
    assert provider.credential_values(record) == {"secret"}


def test_generic_api_provider_rejects_oauth_and_non_string_metadata():
    provider = get_provider("anthropic")
    with pytest.raises(SchemaError, match="not supported"):
        provider.extract({"anthropic": {"type": "oauth"}})
    with pytest.raises(SchemaError, match="metadata"):
        provider.extract({"anthropic": {"type": "api", "key": "secret", "metadata": {"region": 1}}})


def test_copilot_zero_expiry_is_valid():
    provider = get_provider("github-copilot")
    record = provider.extract({"github-copilot": {"type": "oauth", "refresh": "token", "access": "token", "expires": 0}})

    assert record is not None
    assert provider.validate(record) is Validity.OK
    assert provider.identity_is_stable(record)


def test_poe_oauth_uses_non_rotating_access_identity():
    provider = get_provider("poe")
    record = provider.extract({"poe": {"type": "oauth", "refresh": "api-key", "access": "api-key", "expires": int((time.time() + 60) * 1000)}})

    assert record is not None
    assert provider.identity(record) == "oauth\0api-key"
    assert provider.validate(record) is Validity.OK


@pytest.mark.parametrize("provider_id", ["poe", "xai", "github-copilot"])
def test_oauth_providers_reject_expiry_outside_javascript_number_range(provider_id):
    provider = get_provider(provider_id)
    with pytest.raises(SchemaError, match="expires"):
        provider.extract({provider_id: {"type": "oauth", "refresh": "token", "access": "token", "expires": 10**1000}})


@pytest.mark.parametrize("provider_id", ["poe", "xai"])
def test_oauth_describe_preserves_fractional_expiry(provider_id):
    provider = get_provider(provider_id)
    record = provider.extract({provider_id: {"type": "oauth", "refresh": "token", "access": "token", "expires": FRACTIONAL_EXPIRY}})

    assert record is not None
    assert provider.describe(record).expires == FRACTIONAL_EXPIRY


def test_xai_oauth_identity_survives_token_rotation():
    provider = get_provider("xai")
    first = provider.extract(
        {
            "xai": {
                "type": "oauth",
                "refresh": "refresh-1",
                "access": make_jwt({"iss": "https://accounts.x.ai", "sub": "user-1"}),
                "expires": 1,
            }
        }
    )
    rotated = provider.extract(
        {
            "xai": {
                "type": "oauth",
                "refresh": "refresh-2",
                "access": make_jwt({"iss": "https://accounts.x.ai", "sub": "user-1"}),
                "expires": 2,
            }
        }
    )

    assert first is not None and rotated is not None
    assert provider.identity(first) == provider.identity(rotated)


def test_xai_opaque_oauth_token_fails_closed():
    provider = get_provider("xai")
    record = provider.extract({"xai": {"type": "oauth", "refresh": "refresh", "access": "opaque", "expires": 1}})
    assert record is not None
    with pytest.raises(SchemaError, match="stable JWT subject"):
        provider.identity(record)


def test_xai_oauth_without_issuer_fails_closed():
    provider = get_provider("xai")
    record = provider.extract({"xai": {"type": "oauth", "refresh": "refresh", "access": make_jwt({"sub": "user-1"}), "expires": 1}})
    assert record is not None
    with pytest.raises(SchemaError, match="stable JWT subject"):
        provider.identity(record)
