import stat

import pytest

from opencode_swap import backup
from opencode_swap.exceptions import BackupError


def test_bak_roundtrip(tmp_path):
    backup.write_bak(tmp_path, {"openai": {"type": "api", "key": "x"}})
    assert backup.read_bak(tmp_path) == {"openai": {"type": "api", "key": "x"}}


def test_read_bak_none_when_absent(tmp_path):
    assert backup.read_bak(tmp_path) is None


@pytest.mark.parametrize("content", ["[]", "null", '"credential"'])
def test_read_bak_rejects_non_object_json(tmp_path, content):
    path = tmp_path / backup.BACKUP_DIRNAME / backup.BAK_FILENAME
    path.parent.mkdir()
    path.write_text(content)

    with pytest.raises(BackupError, match="JSON object"):
        backup.read_bak(tmp_path)


def test_pristine_written_once(tmp_path):
    backup.write_pristine_if_absent(tmp_path, {"openai": {"type": "api", "key": "first"}})
    backup.write_pristine_if_absent(tmp_path, {"openai": {"type": "api", "key": "second"}})
    assert backup.read_pristine(tmp_path) == {"openai": {"type": "api", "key": "first"}}


def test_read_pristine_none_when_absent(tmp_path):
    assert backup.read_pristine(tmp_path) is None


def test_write_unclaimed_returns_path_and_content(tmp_path):
    path = backup.write_unclaimed(tmp_path, "openai", {"type": "oauth", "accountId": "acct-x"})
    assert path.exists()
    assert path.name.startswith("unclaimed-")
    assert "openai" not in path.name


def test_unclaimed_backups_do_not_collide_within_second(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.time, "strftime", lambda *args: "20260726T120000Z")
    first = backup.write_unclaimed(tmp_path, "openai", {"accountId": "acct-a"})
    second = backup.write_unclaimed(tmp_path, "openai", {"accountId": "acct-b"})

    assert first != second
    assert first.exists()
    assert second.exists()


def test_unclaimed_backup_retries_repeated_random_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.time, "strftime", lambda *args: "20260726T120000Z")
    suffixes = iter(["same", "same", "unique"])
    monkeypatch.setattr(backup.secrets, "token_hex", lambda *args: next(suffixes))
    first = backup.write_unclaimed(tmp_path, "openai", {"accountId": "acct-a"})
    second = backup.write_unclaimed(tmp_path, "openai", {"accountId": "acct-b"})

    assert first != second
    assert first.exists()
    assert second.exists()


def test_backups_dir_and_files_are_locked_down(tmp_path):
    backup.write_bak(tmp_path, {"a": 1})
    backup.write_pristine_if_absent(tmp_path, {"a": 1})
    backup.write_unclaimed(tmp_path, "openai", {"a": 1})

    backups_dir = tmp_path / backup.BACKUP_DIRNAME
    assert stat.S_IMODE(backups_dir.stat().st_mode) == 0o700
    for f in backups_dir.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
