from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from uuid import UUID

import yaml


SOURCE = "v1.131.3"
BRIDGE = "v1.132.3"
TARGET = "v1.137.0"
DIRECT = (SOURCE, TARGET)
STAGED = (SOURCE, BRIDGE, TARGET)
SUPPORTED_PATHS = (DIRECT, STAGED)

DOCS = {
    "typeorm": "https://docs.immich.app/errors/#typeorm-upgrade",
    "backup": "https://docs.immich.app/administration/backup-and-restore/",
    "source": "https://github.com/immich-app/immich/releases/tag/v1.131.3",
    "bridge": "https://github.com/immich-app/immich/releases/tag/v1.132.3",
    "target": "https://github.com/immich-app/immich/releases/tag/v1.137.0",
}

PG_VECTORS = "docker.io/tensorchord/pgvecto-rs:pg14-v0.2.0@sha256:739cdd626151ff1f796dc95a6591b55a714f341c737e27f045019ceabf8e8c52"
PG_VECTORCHORD = "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:32324a2f41df5de9efe1af166b7008c3f55646f8d0e00d9550c16c9822366b4a"
REDIS = "docker.io/redis:6.2-alpine@sha256:148bb5411c184abd288d9aaed139c98123eeb8824c5d3fce03cf721db58066d8"
VALKEY = "docker.io/valkey/valkey:8-bookworm@sha256:42cba146593a5ea9a622002c1b7cba5da7be248650cbb64ecb9c6c33d29794b1"
TARGET_VALKEY = "docker.io/valkey/valkey:8-bookworm@sha256:facc1d2c3462975c34e10fccb167bfa92b0e0dbd992fc282c29a61c3243afb11"


class ConfigError(ValueError):
    """A safe configuration diagnostic without user-supplied values."""


class ConfigLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in seen:
                raise ConfigError("YAML mappings require distinct string keys")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class Timeouts:
    pull: int = 600
    restore: int = 300
    migration: int = 300
    startup: int = 120
    api: int = 60
    cleanup: int = 60


@dataclass(frozen=True)
class Login:
    email: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Asset:
    id: str = field(repr=False)
    sha256: str = field(repr=False)


@dataclass(frozen=True)
class Backup:
    dump: Path = field(repr=False)
    media: Path = field(repr=False)
    login: Login
    assets: tuple[Asset, ...]


@dataclass(frozen=True)
class Config:
    backup: Backup
    paths: tuple[tuple[str, ...], ...]
    timeouts: Timeouts = Timeouts()


def mapping(value, allowed, required):
    if not isinstance(value, dict) or not required <= value.keys() <= allowed:
        raise ConfigError("Configuration fields do not match the documented schema")
    return value


def parse_asset(value) -> Asset:
    fields = {"id", "sha256"}
    record = mapping(value, fields, fields)
    try:
        identifier = UUID(record["id"])
    except (ValueError, AttributeError, TypeError) as exc:
        raise ConfigError("Asset IDs must be UUID strings") from exc
    digest = record["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ConfigError("Each asset requires a 64-digit SHA-256")
    return Asset(str(identifier), digest.lower())


def parse_paths(source, target, raw) -> tuple[tuple[str, ...], ...]:
    if (source, target) != DIRECT:
        raise ConfigError("Unsupported source and target combination")
    if not isinstance(raw, list) or not raw:
        raise ConfigError("paths must list one or both supported upgrade paths")
    paths = []
    for path in raw:
        if not isinstance(path, list) or tuple(path) not in SUPPORTED_PATHS or tuple(path) in paths:
            raise ConfigError("Unsupported or duplicate upgrade path")
        paths.append(tuple(path))
    return tuple(paths)


def credential(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ConfigError("Credentials must reference environment variable names")
    value = os.environ.get(name)
    if not value:
        raise ConfigError("An Immich login environment variable is missing or empty")
    return value


def load_config(path: Path) -> Config:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError("Unable to read a valid rehearsal YAML configuration") from exc
    required = {"source", "target", "paths", "backup", "auth", "assets"}
    data = mapping(data, required | {"timeouts"}, required)
    paths = parse_paths(data["source"], data["target"], data["paths"])
    auth_fields = {"email_env", "password_env"}
    auth = mapping(data["auth"], auth_fields, auth_fields)
    backups = {"dump", "media"}
    files = mapping(data["backup"], backups, backups)
    if any(not isinstance(value, str) or not value.strip() or "\x00" in value for value in files.values()):
        raise ConfigError("Expected dump and media paths")
    if not isinstance(data["assets"], list) or not data["assets"]:
        raise ConfigError("Specify at least one asset to verify")
    assets = tuple(map(parse_asset, data["assets"]))
    if len({asset.id for asset in assets}) != len(assets):
        raise ConfigError("Asset IDs must be unique")
    timeouts = mapping(data.get("timeouts", {}), Timeouts.__dataclass_fields__.keys(), set())
    if any(type(value) is not int or value <= 0 or value > 3600 for value in timeouts.values()):
        raise ConfigError("Timeouts must be whole seconds between 1 and 3600")
    login = Login(credential(auth["email_env"]), credential(auth["password_env"]))
    parent = path.absolute().parent
    backup = Backup(parent / files["dump"], parent / files["media"], login, assets)
    return Config(backup, paths, Timeouts(**timeouts))
