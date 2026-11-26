from base64 import b64encode
from hashlib import sha256
from importlib.resources import files
import json
import struct
from time import monotonic
from uuid import UUID
import zlib

from .docker import remaining, wait_for
from .evidence import FAIL, CheckError, SIGNAL_TEXT, application_signal


class Immich:
    def __init__(self, project):
        self.project = project
        self.probe_code = files(__package__).joinpath("probe.py").read_text(encoding="utf-8")
        self.token = None

    def probe(self, operation, *, timeout=None, **inputs):
        timeout = self.project.timeouts.api if timeout is None else timeout
        message = {"operation": operation, "timeout": max(0.1, min(timeout, 30)), **inputs}
        if self.token and operation in {"asset", "original", "upload"}:
            message["token"] = self.token
        try:
            result = self.project.compose("exec", "-T", "probe", "python", "-c", self.probe_code,
                                          timeout=timeout, data=json.dumps(message).encode())
        except CheckError as exc:
            if exc.code == "command-timeout":
                return {"status": 0}
            raise
        if result.code:
            return {"status": 0}
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            return {"status": 0}

    def migration(self):
        version = self.project.version

        def check(left):
            logs = self.project.compose("logs", "--no-color", "--no-log-prefix", "--tail", "200", "immich",
                                        timeout=left)
            if logs.code:
                raise CheckError("migration-log-unavailable", "Compose could not read the Immich bootstrap log.")
            signal = application_signal((logs.stdout + b"\n" + logs.stderr).decode(errors="replace"), version)
            if signal in {"invalid-upgrade-path", "migration-failed"}:
                raise CheckError(signal, SIGNAL_TEXT[signal], FAIL)
            if signal == "application-listening":
                return True
            states = self.project.compose("ps", "--all", "--status", "exited", "--quiet", "immich", timeout=left)
            if states.code == 0 and states.stdout.strip():
                # Docker can report a stopped container before the log stream has flushed.
                stopped_logs = self.project.compose("logs", "--no-color", "--no-log-prefix", "--tail", "200", "immich",
                                                   timeout=left)
                signal = application_signal((stopped_logs.stdout + b"\n" + stopped_logs.stderr).decode(errors="replace"), version)
                if signal in {"invalid-upgrade-path", "migration-failed"}:
                    raise CheckError(signal, SIGNAL_TEXT[signal], FAIL)
                raise CheckError("application-exited", "Immich exited during bootstrap without a recognized migration error.")
            return False

        try:
            wait_for(self.project.timeouts.migration, check, "migration-timeout", "Immich did not confirm bootstrap before the migration deadline.")
        except CheckError as exc:
            if exc.code != "command-timeout":
                raise
            raise CheckError("migration-timeout", "Immich bootstrap diagnostics exceeded the migration deadline.") from exc
        return "application-listening", SIGNAL_TEXT["application-listening"]

    def ready(self):
        deadline = monotonic() + self.project.timeouts.startup

        def check(left):
            ping = self.probe("ping", timeout=min(left, self.project.timeouts.api, 5))
            if ping != {"status": 200, "value": "pong"} or not remaining(deadline):
                return False
            version = self.probe("version", timeout=min(remaining(deadline), self.project.timeouts.api, 5))
            if version.get("status") == 200 and version.get("value") != self.project.version:
                raise CheckError("version-mismatch", "The API reported a version other than the requested release.", FAIL)
            return version == {"status": 200, "value": self.project.version}

        wait_for(remaining(deadline), check, "startup-timeout", "The in-network ping and version API did not become ready.")
        return "api-ready", "The in-network ping returned pong and the version API matched the requested release."

    def authenticate(self, login, *, create=False):
        credentials = {"email": login.email, "password": login.password}
        if create:
            response = self.probe("signup", fields={**credentials, "name": "Rehearsal"})
            if response.get("status") != 201:
                raise CheckError("signup-failed", "Immich did not create the synthetic account.")
        response = self.probe("login", fields=credentials)
        self.token = response.get("value")
        if response.get("status") not in {200, 201} or not isinstance(self.token, str) or not self.token:
            raise CheckError("authentication-failed", "Immich login did not yield an access token.")
        return "authenticated", "Immich login returned an access token inside the project network."

    def asset(self, asset):
        result = self.probe("asset", asset_id=asset.id)
        if result.get("status") == 404:
            raise CheckError("asset-missing", "The authenticated asset API returned HTTP 404.", FAIL)
        if result.get("status") != 200:
            raise CheckError("asset-api-failed", "The authenticated asset API did not return a usable response.")
        if result.get("value") != asset.id:
            raise CheckError("asset-mismatch", "The API response did not identify the requested asset.", FAIL)
        return "asset-read", "The authenticated API returned the requested asset."

    def original(self, asset):
        result = self.probe("original", asset_id=asset.id, sha256=asset.sha256)
        status = result.get("status")
        if status in {404, 410}:
            raise CheckError("original-missing", f"The authenticated original endpoint returned HTTP {status}.", FAIL)
        if status == 500:
            raise CheckError("original-download-failed", "The authenticated original endpoint returned HTTP 500.", FAIL)
        if status != 200 or type(result.get("match")) is not bool:
            raise CheckError("original-api-failed", "Downloading the original did not yield a complete response.")
        if not result["match"]:
            raise CheckError("sha256-mismatch", "The downloaded original differed from the expected SHA-256.", FAIL)
        return "sha256-match", "The streamed original matched the expected SHA-256."

    def upload(self, image):
        stamp = "2025-04-01T00:00:00.000Z"
        fields = {"deviceAssetId": "rehearsal-image", "deviceId": "rehearsal-device",
                  "fileCreatedAt": stamp, "fileModifiedAt": stamp}
        response = self.probe("upload", fields=fields, png=b64encode(image).decode("ascii"))
        try:
            identifier = str(UUID(response.get("value")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CheckError("upload-failed", "Immich did not return a synthetic asset ID.") from exc
        if response.get("status") != 201:
            raise CheckError("upload-failed", "Immich did not create the synthetic asset.")
        return identifier, sha256(image).hexdigest()


def png_image():
    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    size = 64
    pixels = b"".join(b"\x00" + bytes(channel for x in range(size) for channel in (x * 4, y * 4, 128))
                      for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
