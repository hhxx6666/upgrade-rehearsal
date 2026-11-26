from dataclasses import dataclass
from importlib.resources import files
import os
from pathlib import Path
import re
import subprocess
from time import monotonic, sleep
from uuid import uuid4

from .evidence import CheckError
from .spec import BRIDGE, PG_VECTORCHORD, PG_VECTORS, REDIS, SOURCE, TARGET, TARGET_VALKEY, VALKEY


PROJECT_LABEL = "io.upgrade-rehearsal.project"
DOCKER_ENV = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT", "HOME", "USERPROFILE",
              "TEMP", "TMP", "XDG_RUNTIME_DIR", "DOCKER_CONFIG", "SSH_AUTH_SOCK"}
IMAGES = {SOURCE: (PG_VECTORS, REDIS), BRIDGE: (PG_VECTORS, VALKEY), TARGET: (PG_VECTORCHORD, TARGET_VALKEY)}
SERVER_DIGESTS = {
    SOURCE: "7e5b6729b12b5e5cc5d98bcc6f7c27f723fabae4ee77696855808ebd5200bbf8",
    BRIDGE: "6680d88486251b0264a78a1934fe82eef875555aa6d84d703a0980328a5d5c31",
    TARGET: "dd81bc99d0243bc0a7e89478fdbb30e55399f2e25d694f7f0c34a041d89a3297",
}


@dataclass
class Command:
    code: int
    stdout: bytes
    stderr: bytes


def invoke(args, *, timeout, env=None, cwd=None, data=None, stdin=None, stdout=subprocess.PIPE):
    if stdin is None:
        stdin = subprocess.DEVNULL
    options = {"stdin": stdin if data is None else subprocess.PIPE, "stdout": stdout, "stderr": subprocess.PIPE}
    try:
        with subprocess.Popen(args, env=env, cwd=cwd, **options) as process:
            try:
                output, errors = process.communicate(data, timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                process.kill()
                process.communicate()
                raise
            return Command(process.returncode, output or b"", errors or b"")
    except FileNotFoundError as exc:
        raise CheckError("docker-missing", "Docker CLI was not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckError("command-timeout", "A Docker command exceeded its deadline.") from exc


def docker_env():
    return {key: value for key, value in os.environ.items() if key.upper() in DOCKER_ENV}


def preflight():
    env = docker_env()
    compose = invoke(["docker", "compose", "version", "--short"], timeout=15, env=env)
    if compose.code or not re.match(rb"v?2\.", compose.stdout.strip()):
        raise CheckError("compose-unavailable", "Docker Compose v2 is required.")
    engine = invoke(["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"], timeout=15, env=env)
    if engine.code:
        raise CheckError("engine-unavailable", "The Docker daemon could not be reached.")
    if engine.stdout.strip() not in {b"linux/amd64", b"linux/x86_64"}:
        raise CheckError("engine-platform", "The Docker daemon must run Linux on x86_64.")
    return "docker-ready", "Docker Compose v2 and a Linux x86_64 daemon responded."


def remaining(deadline):
    return max(0, deadline - monotonic())


def wait_for(timeout, check, code, message):
    deadline = monotonic() + timeout
    while (left := remaining(deadline)) > 0:
        if check(left):
            return
        sleep(min(1, remaining(deadline)))
    raise CheckError(code, message)


class Project:
    def __init__(self, work: Path, timeouts):
        self.name = "upgrade-rehearsal-" + uuid4().hex
        self.work = work
        self.timeouts = timeouts
        self.env = docker_env()
        self.env.update(UR_PROJECT=self.name, UR_DB_PASSWORD=uuid4().hex)
        self.begun = False
        self.version = SOURCE
        self.work.joinpath("compose.yaml").write_bytes(files(__package__).joinpath("compose.yaml").read_bytes())
        self.env_file = self.work / "compose.env"
        self.env_file.touch()
        self.work.joinpath("legacy.yaml").write_text("""services:
  database:
    command: ['postgres', '-c', 'shared_preload_libraries=vectors.so', '-c', 'search_path="$$user", public, vectors']
""", encoding="utf-8")
        self.work.joinpath("target.yaml").write_text("""services:
  immich:
    environment:
      IMMICH_MEDIA_LOCATION: /usr/src/app/upload
    tmpfs:
      - /data
""", encoding="utf-8")

    def compose(self, *args, timeout, **io):
        database, redis = IMAGES[self.version]
        server = f"ghcr.io/immich-app/immich-server:{self.version}@sha256:{SERVER_DIGESTS[self.version]}"
        env = dict(self.env, UR_SERVER=server, UR_DATABASE=database, UR_REDIS=redis)
        arguments = ["docker", "compose", "--project-name", self.name, "--project-directory", str(self.work),
                     "--env-file", str(self.env_file), "-f", str(self.work / "compose.yaml")]
        # The target image declares /data as a volume. Keep the restored legacy mount as the
        # active location and cover the unused /data with tmpfs to avoid an anonymous volume.
        overlay = "target.yaml" if self.version == TARGET else "legacy.yaml"
        arguments.extend(("-f", str(self.work / overlay)))
        return invoke([*arguments, *args], cwd=self.work, env=env, timeout=timeout, **io)

    def pull(self, version):
        self.version = version
        try:
            command = self.compose("pull", "--policy", "missing", timeout=self.timeouts.pull)
        except CheckError as exc:
            if exc.code != "command-timeout":
                raise
            raise CheckError("image-pull-timeout", "The image pull exceeded its deadline.") from exc
        if command.code:
            diagnostics = (command.stdout + command.stderr).lower()
            if any(marker in diagnostics for marker in (b"i/o timeout", b"deadline exceeded", b"tls handshake timeout")):
                raise CheckError("registry-timeout", "An image registry request timed out during the Compose pull.")
            if any(marker in diagnostics for marker in (b"manifest unknown", b"manifest not found", b"no matching manifest")):
                raise CheckError("image-unavailable", "The registry did not return a matching release image manifest.")
            raise CheckError("image-pull-failed", "Compose returned an error while pulling the release images.")
        return "images-pulled", "Compose pulled or confirmed the fixed release images."

    def dependencies(self):
        self.begun = True
        deadline = monotonic() + self.timeouts.startup
        try:
            if self.version != SOURCE:
                stopped = self.compose("stop", "-t", "15", "immich", timeout=remaining(deadline))
                if stopped.code:
                    raise CheckError("runtime-stop-failed", "Compose could not stop the preceding Immich server.")
            command = self.compose("up", "-d", "--no-deps", "--pull", "never", "database", "redis", "probe",
                                   timeout=remaining(deadline))
            if command.code:
                raise CheckError("dependency-start-failed", "Compose could not start PostgreSQL, cache or probe.")

            def ready(left):
                db = self.compose("exec", "-T", "database", "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                                  "-d", "immich", timeout=left)
                cache = self.compose("exec", "-T", "redis", "redis-cli", "ping", timeout=remaining(deadline))
                return db.code == 0 and cache.code == 0 and cache.stdout.strip() == b"PONG"

            wait_for(remaining(deadline), ready, "dependency-timeout", "PostgreSQL or cache readiness timed out.")
        except CheckError as exc:
            if exc.code != "command-timeout":
                raise
            raise CheckError("dependency-timeout", "Starting or checking the dependencies timed out.") from exc
        return "dependencies-ready", "PostgreSQL accepted TCP probes and the cache replied PONG."

    def start(self):
        try:
            result = self.compose("up", "-d", "--no-deps", "--pull", "never", "immich", timeout=self.timeouts.startup)
        except CheckError as exc:
            if exc.code != "command-timeout":
                raise
            raise CheckError("container-start-timeout", "Compose did not start the Immich container in time.") from exc
        if result.code:
            raise CheckError("container-start-failed", "Compose could not start the Immich container.")
        return "container-started", "Compose started the requested Immich container."

    def cleanup(self):
        if not self.begun:
            return "no-resources", "No project containers, volumes or networks were started."
        deadline = monotonic() + self.timeouts.cleanup
        try:
            self.compose("down", "--timeout", "10", "--volumes", "--remove-orphans", timeout=remaining(deadline) / 2)
        except CheckError:
            pass
        filters = ("--filter", f"label=com.docker.compose.project={self.name}",
                   "--filter", f"label={PROJECT_LABEL}={self.name}")
        for resource, listing, remove in (("container", "-aq", ("-f",)), ("network", "-q", ()), ("volume", "-q", ())):
            def listing_ids():
                result = invoke(["docker", resource, "ls", listing, *filters], env=self.env,
                                timeout=remaining(deadline))
                values = result.stdout.decode("ascii", errors="replace").split()
                if result.code or any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", val) for val in values):
                    raise CheckError("cleanup-unverified", "Could not verify project resource cleanup.")
                return values

            if ids := listing_ids():
                invoke(["docker", resource, "rm", *remove, *ids], env=self.env, timeout=remaining(deadline))
                if listing_ids():
                    raise CheckError("cleanup-incomplete", "Labeled project resources remain on the Docker daemon.")
        return "cleanup-verified", "No containers, networks or volumes with both project labels remain."
