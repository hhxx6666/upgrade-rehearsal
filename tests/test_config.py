from pathlib import Path

import pytest

from upgrade_rehearsal.backup import validate_backup
from upgrade_rehearsal.cli import checked_config
from upgrade_rehearsal.evidence import CheckError
from upgrade_rehearsal.spec import DIRECT, STAGED, ConfigError, load_config


@pytest.fixture
def example(tmp_path, monkeypatch):
    config_file = tmp_path / "rehearsal.yaml"
    config_file.write_bytes((Path(__file__).parent.parent / "examples" / "rehearsal.yaml").read_bytes())
    monkeypatch.setenv("IMMICH_REHEARSAL_EMAIL", "example@example.test")
    monkeypatch.setenv("IMMICH_REHEARSAL_PASSWORD", "test-secret")
    return config_file


def test_example_parses_and_paths_are_relative_to_config(example):
    config = load_config(example)
    assert config.paths == (DIRECT, STAGED)
    assert config.backup.media == example.parent / "media"
    assert "test-secret" not in repr(config)
    with pytest.raises(ConfigError, match="outside"):
        checked_config(example, config.backup.media / "reports")


def test_rejects_duplicate_keys_and_unlisted_versions(example):
    config = example.read_text(encoding="utf-8")
    example.write_text(config + "source: v1.131.3\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(example)
    example.write_text(config.replace("v1.132.3", "v1.133.0"), encoding="utf-8")
    with pytest.raises(ConfigError, match="Unsupported"):
        load_config(example)


def test_rejects_a_plain_sql_dump(example):
    config = load_config(example)
    config.backup.dump.write_bytes(b"CREATE TABLE example (id INTEGER);")
    config.backup.media.mkdir()
    with pytest.raises(CheckError) as error:
        validate_backup(config.backup)
    assert error.value.code == "unsupported-dump"
