from opencode_swap.oauth_jwt import decode_claims, extract_account_id, extract_email
from tests.helpers import make_jwt


def test_decode_claims_roundtrip():
    token = make_jwt({"chatgpt_account_id": "acct-1", "email": "a@example.com"})
    claims = decode_claims(token)
    assert claims["chatgpt_account_id"] == "acct-1"
    assert claims["email"] == "a@example.com"


def test_decode_claims_malformed_returns_empty():
    assert decode_claims("not-a-jwt") == {}
    assert decode_claims("a.b") == {}
    assert decode_claims("a.!!!notb64.c") == {}


def test_extract_account_id_direct_claim():
    assert extract_account_id({"chatgpt_account_id": "acct-1"}) == "acct-1"


def test_extract_account_id_nested_claim():
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-2"}}
    assert extract_account_id(claims) == "acct-2"


def test_extract_account_id_organizations_fallback():
    claims = {"organizations": [{"id": "org-1"}]}
    assert extract_account_id(claims) == "org-1"


def test_extract_account_id_none_when_absent():
    assert extract_account_id({}) is None


def test_extract_email_absent_like_real_token():
    # M0 spike finding: real OpenAI access tokens observed with no email claim.
    assert extract_email({"chatgpt_account_id": "acct-1"}) is None


def test_extract_email_present():
    assert extract_email({"email": "a@example.com"}) == "a@example.com"
