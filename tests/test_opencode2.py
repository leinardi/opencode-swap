# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""migrate-to-opencode2: the mapping, the destination checks and the CLI.

The destination is a fake `opencode` executable on PATH that prints canned
`--version` and `auth export` output; the vault is real, built in tmp_path
with the file secret backend (or a fake Keychain), and every scenario checks
that it stays byte-for-byte unchanged."""

import json
import os
import stat
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from opencode_swap import cli, macos_keychain, opencode2, process_detection
from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AuthRecord, Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "opencode2_legacy_import.json").read_text())
FUTURE_MS = int((time.time() + 3600) * 1000)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Like tests/test_cli.py's: paths.py points into tmp_path, the file
    secret backend is forced, no OpenCode process is seen, and any network
    call fails the test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # OpenCode's config dir too: migrate-to-opencode2 reads tui.json and cli.json there.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    monkeypatch.setattr(Platform, "detect", classmethod(lambda cls: Platform.UNKNOWN))
    monkeypatch.setattr(process_detection, "is_opencode_running", lambda: False)

    def no_network(*args, **kwargs):
        raise AssertionError("migrate-to-opencode2 must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return tmp_path


class FakeOpenCode:
    """A fake `opencode` on PATH. `version` and `export` are what it prints;
    `export_exit` its `auth export` exit code. Every call is logged."""

    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "fake-bin"
        self.dir.mkdir()
        self.state = self.dir / "state.json"
        self.log = self.dir / "calls.jsonl"
        script = self.dir / "opencode"
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "base = pathlib.Path(__file__).parent\n"
            "state = json.loads((base / 'state.json').read_text())\n"
            "with open(base / 'calls.jsonl', 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print(state['version'])\n"
            "    sys.exit(0)\n"
            "if sys.argv[1:3] == ['auth', 'export']:\n"
            "    sys.stdout.write(state['export'])\n"
            "    sys.exit(state['export_exit'])\n"
            "sys.exit(64)\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{self.dir}:{os.environ['PATH']}")
        self.set()

    def set(self, *, version="opencode v2.0.29", export=None, export_exit=0, raw_export=None):
        text = raw_export if raw_export is not None else json.dumps(export or [])
        self.state.write_text(json.dumps({"version": version, "export": text, "export_exit": export_exit}))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    return FakeOpenCode(tmp_path, monkeypatch)


def auth_path(tmp_path):
    return tmp_path / "opencode" / "auth.json"


def data_root(tmp_path):
    return tmp_path / "opencode-swap"


def openai_entry(account_id, refresh, **overrides):
    entry = {
        "type": "oauth",
        "refresh": refresh,
        "access": make_jwt({"chatgpt_account_id": account_id, "planted": f"access-secret-{refresh}"}),
        "expires": FUTURE_MS,
        "accountId": account_id,
    }
    entry.update(overrides)
    return entry


def write_auth(tmp_path, data):
    path = auth_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def writable_switcher(tmp_path):
    return Switcher(auth_path(tmp_path), data_root(tmp_path), platform=Platform.UNKNOWN)


def build_vault(tmp_path):
    """openai/work (registry-active, then live), openai/home and
    zai-coding-plan/work, all with planted secret values."""
    switcher = writable_switcher(tmp_path)
    write_auth(tmp_path, {"openai": openai_entry("acct-home", "planted-refresh-home")})
    switcher.add_account("home", "openai")
    write_auth(tmp_path, {"zai-coding-plan": {"type": "api", "key": "planted-zai-key-K0-0123456789"}})
    switcher.add_account("work", "zai-coding-plan")
    write_auth(
        tmp_path,
        {
            "openai": openai_entry("acct-work", "planted-refresh-work"),
            "zai-coding-plan": {"type": "api", "key": "planted-zai-key-K0-0123456789"},
        },
    )
    switcher.add_account("work", "openai")
    return switcher


PLANTED = ("planted-refresh", "planted-zai-key", "access-secret", "planted-foreign")


def vault_snapshot(tmp_path):
    root = data_root(tmp_path)
    if not root.exists():
        return None
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.name != ".lock"}


def run_cli(tmp_path, capsys, *args):
    before = vault_snapshot(tmp_path)
    auth_before = auth_path(tmp_path).read_bytes() if auth_path(tmp_path).exists() else None
    code = cli.main(["migrate-to-opencode2", *args])
    out, err = capsys.readouterr()
    assert vault_snapshot(tmp_path) == before, "the vault must stay byte-for-byte unchanged"
    assert (auth_path(tmp_path).read_bytes() if auth_path(tmp_path).exists() else None) == auth_before
    for secret in PLANTED:
        assert secret not in err
    return code, out, err


def legacy_entry(integration, label, value, *, cid="cred_13aff4d0f0018e21Y2KdIhU8Zn", active=True):
    return {"id": cid, "integrationID": integration, "label": label, "active": active, "value": value}


def legacy_value(record_raw, integration):
    return opencode2.to_value(AuthRecord(type=record_raw["type"], raw=record_raw), integration, "test")


# --- mapping ---------------------------------------------------------------


@pytest.mark.parametrize("provider_id", ["openai", "zai-coding-plan", "github-copilot", "opencode", "anthropic"])
def test_mapping_matches_opencode2_legacy_import_golden_fixture(provider_id):
    raw = FIXTURE["legacy_auth_json"][provider_id]
    imported = {entry["integrationID"]: entry["value"] for entry in FIXTURE["imported"]}

    assert legacy_value(raw, opencode2.integration_id(provider_id)) == imported[provider_id]


def test_wellknown_is_skipped_rather_than_imported_as_a_key():
    raw = FIXTURE["legacy_auth_json"]["https://wk.example.com/"]

    assert opencode2.integration_id("https://wk.example.com/") == "https://wk.example.com"
    assert legacy_value(raw, "https://wk.example.com") is None


def test_fractional_expires_opencode2_drops_is_refused():
    assert "xai" in FIXTURE["skipped"]
    with pytest.raises(SchemaError, match="expires"):
        legacy_value(FIXTURE["legacy_auth_json"]["xai"], "xai")


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "oauth", "refresh": "r", "access": "a", "expires": True},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": -1},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": "1"},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 1, "accountId": 7},
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 1, "extra": "x"},
        {"type": "api", "key": "k", "metadata": {"a": 1}},
        {"type": "api", "key": "k", "metadata": ["a"]},
        {"type": "api", "key": ""},
        {"type": "mystery", "key": "k"},
    ],
)
def test_mapping_never_coerces(raw):
    with pytest.raises(SchemaError) as excinfo:
        legacy_value(raw, "openai")
    assert "'k'" not in str(excinfo.value) and "'r'" not in str(excinfo.value)


def test_method_ids_mirror_the_legacy_import():
    assert opencode2.method_id("openai") == "chatgpt-browser"
    assert {opencode2.method_id(i) for i in ("github-copilot", "opencode", "xai")} == {"device"}
    assert opencode2.method_id("anthropic") == "oauth"


def test_credential_ids_are_stable_and_shaped_like_opencode_ids():
    first = opencode2.credential_id("openai", "work")

    assert first == opencode2.credential_id("openai", "work")
    assert first == "cred_6oLVJs8DDqXMjgb2DLnlmFiVQr"  # pinned: a changed id would re-import every account
    assert len(first) == len("cred_") + 26
    assert set(first[len("cred_") :]) <= set(opencode2.ID_ALPHABET)
    assert len({first, opencode2.credential_id("openai", "home"), opencode2.credential_id("zai-coding-plan", "work")}) == 3


def test_reverse_mapping_round_trips_and_marks_the_unverifiable():
    raw = openai_entry("acct-x", "r")
    value = legacy_value(raw, "openai")

    assert opencode2.from_value("openai", value).raw == raw
    assert opencode2.from_value("openai", {**value, "methodID": "chatgpt-token-sharing"}) is None
    assert opencode2.from_value("openai", {"type": "external", "methodID": "x"}) is None
    assert opencode2.from_value("zai-coding-plan", {"type": "key", "key": "k", "configuration": {}}) is None
    assert opencode2.from_value("zai-coding-plan", {"type": "key", "key": "k"}).raw == {"type": "api", "key": "k"}


# --- destination parsing ---------------------------------------------------


@pytest.mark.parametrize(
    "export",
    [
        {"not": "a list"},
        [{"id": "cred_a", "integrationID": "openai", "label": "x", "active": True}],
        [legacy_entry("openai", "x", {"type": "mystery"})],
        [legacy_entry("openai", "x", {"type": "key", "key": "k", "extra": 1})],
        [{**legacy_entry("openai", "x", {"type": "key", "key": "k"}), "extra": 1}],
        [legacy_entry("openai", "x", {"type": "oauth", "methodID": "m", "refresh": "r", "access": "a", "expires": 1.5})],
        [legacy_entry("openai", "x", {"type": "key", "key": "k"}, active="yes")],
    ],
)
def test_destination_parse_is_strict(export):
    with pytest.raises(SchemaError):
        opencode2.parse_destination(json.dumps(export))


def test_destination_parse_accepts_every_known_shape():
    export = [
        legacy_entry(
            "openai", "a", {"type": "oauth", "methodID": "m", "refresh": "r", "access": "a", "expires": 1, "metadata": {"x": [1]}}, cid="c1"
        ),
        legacy_entry("zai-coding-plan", "b", {"type": "key", "key": "k", "metadata": {}, "configuration": {"f": "v"}}, cid="c2"),
        legacy_entry("azure", "c", {"type": "external", "methodID": "azure-cli"}, cid="c3"),
    ]

    parsed = opencode2.parse_destination(json.dumps(export))

    assert [(c.id, c.integration, c.label) for c in parsed] == [("c1", "openai", "a"), ("c2", "zai-coding-plan", "b"), ("c3", "azure", "c")]
    assert "'k'" not in repr(parsed)


# --- the command -------------------------------------------------------------


def test_fresh_destination_gets_every_account_including_the_live_one(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    entries = json.loads(out)
    assert [(e["integrationID"], e["label"], e["active"]) for e in entries] == [
        ("openai", "work", True),
        ("openai", "home", False),
        ("zai-coding-plan", "work", True),
    ]
    assert entries[0]["id"] == opencode2.credential_id("openai", "work")
    assert entries[0]["value"]["refresh"] == "planted-refresh-work"
    assert entries[0]["value"]["metadata"] == {"accountID": "acct-work"}
    assert entries[2]["value"] == {"type": "key", "key": "planted-zai-key-K0-0123456789"}
    assert "from OpenCode 1's live login" in err
    assert fake_opencode.calls() == [["--version"], ["auth", "export", "--standalone"]]


def test_completed_legacy_import_with_the_live_account_skips_it(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    live = openai_entry("acct-work", "planted-refresh-work")
    zai = {"type": "api", "key": "planted-zai-key-K0-0123456789"}
    fake_opencode.set(
        export=[
            legacy_entry("openai", "OAuth", legacy_value(live, "openai"), cid="cred_legacy1"),
            legacy_entry("zai-coding-plan", "API key", legacy_value(zai, "zai-coding-plan"), cid="cred_legacy2"),
        ]
    )

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    entries = json.loads(out)
    assert [(e["integrationID"], e["label"], e["active"]) for e in entries] == [("openai", "home", False)]
    assert "openai/work: already in OpenCode 2 as 'OAuth' (rename it in /connect with ctrl+r)" in err
    assert "zai-coding-plan/work: already in OpenCode 2 as 'API key'" in err
    assert "warning" not in err


def test_foreign_unverifiable_credential_is_kept_and_warned_about(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    foreign = {"type": "oauth", "methodID": "chatgpt-token-sharing", "refresh": "planted-foreign-r", "access": "planted-foreign-a", "expires": 1}
    fake_opencode.set(export=[legacy_entry("openai", "OpenAI", foreign, cid="cred_foreign")])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    entries = json.loads(out)
    assert [(e["label"], e["active"]) for e in entries if e["integrationID"] == "openai"] == [("work", False), ("home", False)]
    assert "openai/work: OpenCode 2 has openai credentials it cannot compare ('OpenAI'); the import may duplicate one of them" in err


def test_foreign_verifiable_credential_is_no_duplicate(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    other = legacy_value(openai_entry("acct-other", "planted-foreign-r"), "openai")
    fake_opencode.set(export=[legacy_entry("openai", "OAuth", other, cid="cred_other")])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert len([e for e in json.loads(out) if e["integrationID"] == "openai"]) == 2
    assert "warning" not in err


def test_removed_auto_imported_row_is_emitted_again(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    zai = {"type": "api", "key": "planted-zai-key-K0-0123456789"}
    # The legacy import ran, then the user removed the imported openai row.
    fake_opencode.set(export=[legacy_entry("zai-coding-plan", "API key", legacy_value(zai, "zai-coding-plan"), cid="cred_legacy2")])

    code, out, _err = run_cli(tmp_path, capsys)

    assert code == 0
    assert [(e["integrationID"], e["label"], e["active"]) for e in json.loads(out)] == [("openai", "work", True), ("openai", "home", False)]


def test_rotated_live_record_is_exported_and_the_vault_is_untouched(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    # OpenCode 1 rotated work's tokens in place (R0 in the vault, R1 live).
    write_auth(tmp_path, {"openai": openai_entry("acct-work", "planted-refresh-work-R1")})

    code, out, _err = run_cli(tmp_path, capsys)

    assert code == 0
    work = next(e for e in json.loads(out) if e["label"] == "work" and e["integrationID"] == "openai")
    assert work["value"]["refresh"] == "planted-refresh-work-R1"


def test_changed_key_under_the_same_name_is_an_id_conflict(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    old = legacy_value({"type": "api", "key": "planted-zai-key-K-OLD-0123456789"}, "zai-coding-plan")
    cid = opencode2.credential_id("zai-coding-plan", "work")
    fake_opencode.set(export=[legacy_entry("zai-coding-plan", "work", old, cid=cid)])
    out_file = tmp_path / "out.json"

    code, out, err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 1
    assert f"zai-coding-plan/work: zai-coding-plan credential {cid} ('work')" in err
    assert "opencode auth logout <integration> <id>" in err and "--skip" in err
    assert not out_file.exists() and out == ""


def test_recreated_account_is_an_id_conflict_and_skip_proceeds(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    recreated = legacy_value(openai_entry("acct-before", "planted-refresh-before"), "openai")
    cid = opencode2.credential_id("openai", "work")
    fake_opencode.set(export=[legacy_entry("openai", "work", recreated, cid=cid)])

    code, _out, err = run_cli(tmp_path, capsys)
    assert code == 1
    assert f"openai/work: openai credential {cid}" in err

    code, out, err = run_cli(tmp_path, capsys, "--skip", "openai/WORK")
    assert code == 0
    assert [(e["integrationID"], e["label"]) for e in json.loads(out)] == [("openai", "home"), ("zai-coding-plan", "work")]
    assert "openai/work: skipped (--skip)" in err


def test_verified_same_id_entry_is_already_present(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    zai = legacy_value({"type": "api", "key": "planted-zai-key-K0-0123456789"}, "zai-coding-plan")
    fake_opencode.set(export=[legacy_entry("zai-coding-plan", "work", zai, cid=opencode2.credential_id("zai-coding-plan", "work"))])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert all(e["integrationID"] != "zai-coding-plan" for e in json.loads(out))
    assert "zai-coding-plan/work: already present in OpenCode 2 as 'work'" in err


def test_unknown_skip_target_is_refused(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)

    code, out, err = run_cli(tmp_path, capsys, "--skip", "openai/nobody")

    assert code == 1 and out == ""
    assert "no such saved account" in err


@pytest.mark.parametrize(
    "value",
    [
        {"type": "key", "key": "planted-foreign-k", "configuration": {"region": "eu"}},
        {"type": "external", "methodID": "zai-cli"},
    ],
)
def test_key_with_configuration_and_external_are_unverifiable(tmp_path, capsys, fake_opencode, value):
    build_vault(tmp_path)
    fake_opencode.set(export=[legacy_entry("zai-coding-plan", "other", value, cid="cred_unverifiable")])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    zai = [e for e in json.loads(out) if e["integrationID"] == "zai-coding-plan"]
    assert [(e["label"], e["active"]) for e in zai] == [("work", False)]
    assert "zai-coding-plan/work: OpenCode 2 has zai-coding-plan credentials it cannot compare ('other')" in err


@pytest.mark.parametrize(
    "raw_export",
    [
        json.dumps([legacy_entry("openai", "x", {"type": "mystery", "planted": "planted-foreign-x"})]),
        json.dumps([{**legacy_entry("openai", "x", {"type": "key", "key": "planted-foreign-k"}), "surprise": True}]),
        "not json planted-foreign-text",
    ],
)
def test_unknown_destination_shapes_are_refused(tmp_path, capsys, fake_opencode, raw_export):
    build_vault(tmp_path)
    fake_opencode.set(raw_export=raw_export)
    out_file = tmp_path / "out.json"

    code, out, _err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 1 and out == ""
    assert not out_file.exists()


def test_ambiguous_attribution_refuses_with_nothing_written(tmp_path, capsys, fake_opencode):
    switcher = writable_switcher(tmp_path)
    # An unstable identity (no accountId, no claim) for the registry-active
    # account, then a live record of the same type matching no saved account.
    write_auth(tmp_path, {"openai": {"type": "oauth", "refresh": "planted-refresh-u0", "access": make_jwt({}), "expires": FUTURE_MS}})
    switcher.add_account("work", "openai")
    write_auth(tmp_path, {"openai": {"type": "oauth", "refresh": "planted-refresh-u1", "access": make_jwt({}), "expires": FUTURE_MS}})
    out_file = tmp_path / "out.json"

    code, out, err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 1 and out == ""
    assert "cannot tell whether OpenCode 1's live openai login is the saved account openai/work" in err
    assert not out_file.exists()
    assert fake_opencode.calls() == []


def test_orphaned_sealed_file_refuses_and_is_kept(tmp_path, capsys, fake_opencode, monkeypatch):
    keychain = {}
    monkeypatch.setattr(Platform, "detect", classmethod(lambda cls: Platform.MACOS))
    monkeypatch.setattr(macos_keychain, "get_password", lambda service, key: keychain.get(key))
    monkeypatch.setattr(macos_keychain, "set_password", lambda service, key, value: keychain.__setitem__(key, value))
    monkeypatch.setattr(macos_keychain, "delete_password", lambda service, key: keychain.pop(key, None))
    switcher = Switcher(auth_path(tmp_path), data_root(tmp_path), platform=Platform.MACOS)
    write_auth(tmp_path, {"openai": openai_entry("acct-home", "planted-refresh-home")})
    switcher.add_account("home", "openai")
    write_auth(tmp_path, {})
    keychain.clear()  # the data key is gone; the sealed file stays behind

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 1 and out == ""
    assert "openai:home" in err and "orphaned sealed file" in err
    assert list((data_root(tmp_path) / "secrets").glob("v3-*.enc"))


def test_v1_registry_is_read_without_migrating_it(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    registry_path = data_root(tmp_path) / "registry.json"
    v2 = json.loads(registry_path.read_text())
    registry_path.write_text(json.dumps({"version": 1, "active": "work", "accounts": v2["accounts"]["openai"]}))

    code, out, _err = run_cli(tmp_path, capsys)

    assert code == 0
    assert [(e["label"], e["active"]) for e in json.loads(out)] == [("work", True), ("home", False)]
    assert not (data_root(tmp_path) / "registry.v1.json.bak").exists()


def test_missing_data_root_is_refused_and_not_created(tmp_path, capsys, fake_opencode):
    out_file = tmp_path / "out.json"

    code, out, err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 1 and out == ""
    assert "no saved accounts" in err
    assert not data_root(tmp_path).exists()
    assert not out_file.exists()


@pytest.mark.parametrize(
    ("version", "message"),
    [("1.19.0", "not OpenCode 2"), ("opencode v2.0.17", "upgrade to OpenCode 2.0.20"), ("garbage", "could not read the OpenCode version")],
)
def test_wrong_opencode_version_is_refused(tmp_path, capsys, fake_opencode, version, message):
    build_vault(tmp_path)
    fake_opencode.set(version=version)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 1 and out == ""
    assert message in err
    assert ["auth", "export", "--standalone"] not in fake_opencode.calls()


def test_failing_export_is_refused_without_echoing_it(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    fake_opencode.set(raw_export="planted-foreign-partial-output", export_exit=1)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 1 and out == ""
    assert "'opencode auth export' failed (exit 1)" in err


def test_missing_opencode_binary_is_refused(tmp_path, capsys):
    build_vault(tmp_path)

    code, _out, err = run_cli(tmp_path, capsys, "--opencode", str(tmp_path / "nowhere" / "opencode"))

    assert code == 1
    assert "install OpenCode 2 or pass --opencode PATH" in err


def test_output_file_is_private_and_never_overwritten(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    out_file = tmp_path / "out.json"

    code, out, err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 0
    assert out == ""
    assert stat.S_IMODE(out_file.stat().st_mode) == 0o600
    assert len(json.loads(out_file.read_text())) == 3
    assert f"opencode auth import {out_file}" in err and "delete it" in err
    before = out_file.read_bytes()

    code, out, err = run_cli(tmp_path, capsys, "--output", str(out_file))

    assert code == 1 and out == ""
    assert "already exists" in err
    assert out_file.read_bytes() == before


def test_terminal_stdout_is_refused(tmp_path, capsys, fake_opencode, monkeypatch):
    build_vault(tmp_path)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 2 and out == ""
    assert "refusing to print credentials to a terminal" in err
    assert fake_opencode.calls() == []


def test_unmanaged_live_login_gets_a_log_in_again_note(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    write_auth(
        tmp_path,
        {
            "openai": openai_entry("acct-stranger", "planted-foreign-r"),
            "deepseek": {"type": "api", "key": "planted-foreign-deepseek-key"},
        },
    )

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert len(json.loads(out)) == 3
    assert "OpenCode 1's openai login is not a saved account and is not exported; log in again in OpenCode 2 with 'opencode auth login openai'" in err
    assert "OpenCode 1's deepseek login is not a saved account" in err


def test_unmanaged_live_login_the_destination_holds_gets_no_note(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    stranger = {"type": "api", "key": "planted-foreign-deepseek-key"}
    write_auth(tmp_path, {"deepseek": stranger})
    fake_opencode.set(export=[legacy_entry("deepseek", "API key", legacy_value(stranger, "deepseek"), cid="cred_ds")])

    code, _out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert "deepseek login" not in err


def test_stdout_payload_is_the_only_place_secrets_appear(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert "planted-refresh-work" in out  # the payload itself, piped to auth import
    for secret in PLANTED:
        assert secret not in err


def test_wellknown_saved_account_is_skipped_and_reported(tmp_path, capsys, fake_opencode):
    switcher = writable_switcher(tmp_path)
    write_auth(tmp_path, {"openai": {"type": "wellknown", "key": "WK", "token": "planted-foreign-wk-token"}})
    switcher.add_account("wk", "openai")

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert json.loads(out) == []
    assert "openai/wk: skipped, a 'wellknown' login is not migrated" in err


def unstable_openai(refresh):
    """An OpenAI login with no accountId and no account claim: its identity
    falls back to the refresh token, which OpenCode rotates."""
    return {"type": "oauth", "refresh": refresh, "access": make_jwt({}), "expires": FUTURE_MS}


def test_unstable_identity_refreshed_by_opencode2_is_a_possible_duplicate(tmp_path, capsys, fake_opencode):
    switcher = writable_switcher(tmp_path)
    write_auth(tmp_path, {"openai": unstable_openai("planted-refresh-u0")})
    switcher.add_account("work", "openai")
    write_auth(tmp_path, {})
    # OpenCode 2 imported this account and has refreshed it since.
    refreshed = legacy_value(unstable_openai("planted-foreign-u1"), "openai")
    fake_opencode.set(export=[legacy_entry("openai", "OAuth", refreshed, cid="cred_legacy1")])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert [e["label"] for e in json.loads(out)] == ["work"]
    assert "openai/work: OpenCode 2 has openai credentials it cannot compare ('OAuth'); the import may duplicate one of them" in err


def test_unstable_identity_with_the_same_refresh_token_is_owned(tmp_path, capsys, fake_opencode):
    switcher = writable_switcher(tmp_path)
    write_auth(tmp_path, {"openai": unstable_openai("planted-refresh-u0")})
    switcher.add_account("work", "openai")
    write_auth(tmp_path, {})
    same = legacy_value(unstable_openai("planted-refresh-u0"), "openai")
    fake_opencode.set(export=[legacy_entry("openai", "OAuth", same, cid="cred_legacy1")])

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert json.loads(out) == []
    assert "openai/work: already in OpenCode 2 as 'OAuth'" in err


def test_stable_identities_that_differ_are_no_duplicate():
    stable = opencode2._identity("openai", AuthRecord(type="oauth", raw=openai_entry("acct-a", "r1")))
    other = opencode2.DestinationCredential("c", "openai", "x", True, legacy_value(openai_entry("acct-b", "r2"), "openai"))
    unstable = opencode2.DestinationCredential("d", "openai", "y", True, legacy_value(unstable_openai("r3"), "openai"))

    assert opencode2._ownership("openai", stable, other) is False
    assert opencode2._ownership("openai", stable, unstable) is None


def test_stdout_redirected_to_a_file_is_refused(tmp_path, capsys, fake_opencode, monkeypatch):
    build_vault(tmp_path)
    target = tmp_path / "redirected.json"
    with target.open("w") as redirected:
        monkeypatch.setattr(sys, "stdout", redirected)
        code, _out, err = run_cli(tmp_path, capsys)

    assert code == 2
    assert "refusing to print credentials to a file" in err and "--output FILE" in err
    assert target.read_text() == ""
    assert fake_opencode.calls() == []


def test_stdout_pipe_receives_the_credentials(tmp_path, capsys, fake_opencode, monkeypatch):
    build_vault(tmp_path)
    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "w") as pipe:
        monkeypatch.setattr(sys, "stdout", pipe)
        code, _out, _err = run_cli(tmp_path, capsys)
    with os.fdopen(read_fd) as reader:
        entries = json.loads(reader.read())

    assert code == 0
    assert len(entries) == 3


# --- the 0.4.x plugin in OpenCode's config ---------------------------------


def write_config(tmp_path, name, text):
    path = tmp_path / "config" / "opencode" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("name", "text"),
    [
        # What 0.4.x's install instructions produce, and what OpenCode 2 copies it into.
        ("tui.json", '{\n  // comments are fine\n  "plugin": ["@leinardi/opencode-swap@0.4"],\n  "plugin_enabled": {"opencode-swap": true}\n}\n'),
        ("cli.json", '{"plugins": ["@leinardi/opencode-swap", "opencode-swap"]}\n'),
    ],
)
def test_a_config_listing_the_plugin_gets_a_warning(tmp_path, capsys, fake_opencode, name, text):
    build_vault(tmp_path)
    config = write_config(tmp_path, name, text)

    code, out, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert len(json.loads(out)) == 3
    assert f"warning: {config} lists the opencode-swap plugin" in err
    assert "installs npm's unrelated 'opencode-swap' package" in err
    assert config.read_text() == text, "the config is only read, never changed"


def test_a_config_without_the_plugin_gets_no_warning(tmp_path, capsys, fake_opencode):
    build_vault(tmp_path)
    write_config(tmp_path, "tui.json", '{"plugin": ["some-other-plugin"]}\n')
    write_config(tmp_path, "cli.json", "not even json")

    code, _, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert "lists the opencode-swap plugin" not in err


def test_opencode_config_dir_override_is_checked_as_well(tmp_path, capsys, fake_opencode, monkeypatch):
    build_vault(tmp_path)
    override = tmp_path / "custom-config"
    override.mkdir()
    (override / "cli.json").write_text('{"plugins": ["opencode-swap"]}\n')
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(override))

    code, _, err = run_cli(tmp_path, capsys)

    assert code == 0
    assert f"warning: {override / 'cli.json'} lists the opencode-swap plugin" in err
