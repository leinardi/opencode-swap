import stat

from opencode_swap import backup


def test_bak_roundtrip(tmp_path):
    backup.write_bak(tmp_path, {"openai": {"type": "api", "key": "x"}})
    assert backup.read_bak(tmp_path) == {"openai": {"type": "api", "key": "x"}}


def test_read_bak_none_when_absent(tmp_path):
    assert backup.read_bak(tmp_path) is None


def test_pristine_written_once(tmp_path):
    backup.write_pristine_if_absent(tmp_path, {"openai": {"type": "api", "key": "first"}})
    backup.write_pristine_if_absent(tmp_path, {"openai": {"type": "api", "key": "second"}})
    assert backup.read_pristine(tmp_path) == {"openai": {"type": "api", "key": "first"}}


def test_read_pristine_none_when_absent(tmp_path):
    assert backup.read_pristine(tmp_path) is None


def test_write_unclaimed_returns_path_and_content(tmp_path):
    path = backup.write_unclaimed(tmp_path, "openai", {"type": "oauth", "accountId": "acct-x"})
    assert path.exists()
    assert path.name.startswith("unclaimed-openai-")


def test_backups_dir_and_files_are_locked_down(tmp_path):
    backup.write_bak(tmp_path, {"a": 1})
    backup.write_pristine_if_absent(tmp_path, {"a": 1})
    backup.write_unclaimed(tmp_path, "openai", {"a": 1})

    backups_dir = tmp_path / backup.BACKUP_DIRNAME
    assert stat.S_IMODE(backups_dir.stat().st_mode) == 0o700
    for f in backups_dir.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
