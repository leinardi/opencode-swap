import time

import pytest

from opencode_swap import usage
from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AuthRecord, Validity
from opencode_swap.providers import PROVIDERS, get_provider
from tests.helpers import make_jwt

FRACTIONAL_EXPIRY = 1_730_000_000_000.5

# Providers whose `extract` accepts an oauth record. Kept explicit (not
# derived from a caught SchemaError) so a regression where an OAuth provider
# starts rejecting valid OAuth records fails the test below instead of
# silently skipping it.
OAUTH_CAPABLE_PROVIDERS = {"openai", "github-copilot", "poe", "xai"}


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


@pytest.mark.parametrize("provider_id", sorted(PROVIDERS))
def test_every_provider_splice_publishes_an_integer_expiry(provider_id):
    """`splice` is the only path by which opencode-swap content reaches
    OpenCode, and OpenCode drops entries whose `expires` is not an integer
    without reporting it. A provider added later that forgets
    `published_raw` fails here rather than silently unregistering itself
    from OpenCode at runtime."""
    provider = get_provider(provider_id)
    entry = {"type": "oauth", "refresh": "token", "access": make_jwt({"chatgpt_account_id": "acct-1"}), "expires": FRACTIONAL_EXPIRY}

    if provider_id not in OAUTH_CAPABLE_PROVIDERS:
        # An API-only provider must reject the oauth record outright rather
        # than accept and mis-handle it -- splice never sees a fractional
        # expiry because extract fails closed first.
        with pytest.raises(SchemaError):
            provider.extract({provider_id: entry})
        return

    record = provider.extract({provider_id: entry})
    assert record is not None

    spliced = provider.splice({}, record)[provider_id]

    assert spliced["expires"] == int(FRACTIONAL_EXPIRY)
    assert isinstance(spliced["expires"], int)


@pytest.mark.parametrize("provider_id", sorted(PROVIDERS))
def test_provider_without_usage_source_returns_none_and_does_no_io(provider_id, monkeypatch):
    provider = PROVIDERS[provider_id]
    if provider.usage_record_types:
        pytest.skip(f"{provider_id} has a usage source")

    def boom(*a, **k):
        raise AssertionError("fetch_usage must not perform I/O for a provider with no usage source")

    monkeypatch.setattr(usage.urllib.request, "urlopen", boom)
    assert provider.fetch_usage(AuthRecord(type="api", raw={"key": "k"})) is None
    assert provider.fetch_usage(AuthRecord(type="oauth", raw={"refresh": "r", "access": "a"})) is None


def test_zai_provider_shape_matches_generic_api_but_declares_a_usage_source():
    provider = PROVIDERS["zai-coding-plan"]
    assert provider.usage_record_types == frozenset({"api"})
    auth = {"zai-coding-plan": {"type": "api", "key": "zk"}}
    record = provider.extract(auth)
    assert record is not None
    assert provider.identity(record) == "api-key\0zk"
    assert provider.splice({}, record) == auth
    # no key in the record -> not looked up, rather than a request with an empty bearer
    assert provider.fetch_usage(AuthRecord(type="api", raw={})) is None
