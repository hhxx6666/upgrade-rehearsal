from collections.abc import Callable
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import signal
from tempfile import TemporaryDirectory

import yaml

from .backup import copy_media, restore_database
from .docker import Project
from .evidence import FAIL, PASS, UNKNOWN, CheckError, PathResult
from .immich import Immich, png_image
from .spec import DIRECT, DOCS, SOURCE, STAGED, TARGET, Asset, Backup, Login, Timeouts


@contextmanager
def cleanup_on_exit(report, stages, project):
    try:
        yield
    finally:
        # An initial interrupt reaches this block. A repeat during cleanup should not cut it short.
        handlers = {}
        try:
            for name in (signal.SIGINT, signal.SIGTERM):
                try:
                    handlers[name] = signal.signal(name, signal.SIG_IGN)
                except ValueError:
                    pass  # The library may run in a background thread without signal handlers.
            try:
                report.step(stages, "cleanup", project.cleanup)
            except CheckError:
                pass  # The failed cleanup stage remains visible and affects the verdict.
        finally:
            for name, handler in handlers.items():
                signal.signal(name, handler)


def boot(report, stages, project, api, version):
    report.step(stages, "image pull", lambda: project.pull(version), version=version)
    report.step(stages, "dependencies", project.dependencies, version=version)
    start_application(report, stages, project, api, version)


def start_application(report, stages, project, api, version):
    report.step(stages, "container start", project.start, version=version)
    report.step(stages, "migration", api.migration, version=version)
    report.step(stages, "API readiness", api.ready, version=version)


def verify(report, stages, api, backup, version):
    report.step(stages, "authentication", lambda: api.authenticate(backup.login), version=version)
    for index, asset in enumerate(backup.assets, start=1):
        alias = f"asset-{index}"
        report.step(stages, "read asset", lambda: api.asset(asset), version=version, asset=alias)
        report.step(stages, "original SHA-256", lambda: api.original(asset), version=version, asset=alias)


def run_path(report, config, versions, *, project_factory=Project, api_factory=Immich):
    path = PathResult("Path A" if versions == DIRECT else "Path B", versions,
                      assets_requested=len(config.backup.assets))
    report.paths.append(path)
    try:
        with TemporaryDirectory(prefix="upgrade-rehearsal-") as temporary:
            step = report.step
            stages = path.stages
            project = project_factory(Path(temporary), config.timeouts)
            api = api_factory(project)
            with cleanup_on_exit(report, stages, project):
                step(stages, "image pull", lambda: project.pull(SOURCE), version=SOURCE)
                step(stages, "dependencies", project.dependencies, version=SOURCE)
                step(stages, "restore database", lambda: restore_database(project, config.backup.dump), version=SOURCE)
                step(stages, "copy media", lambda: copy_media(project, config.backup.media), version=SOURCE)
                start_application(report, stages, project, api, SOURCE)
                verify(report, stages, api, config.backup, SOURCE)
                for version in versions[1:]:
                    boot(report, stages, project, api, version)
                    verify(report, stages, api, config.backup, version)
    except CheckError:
        pass
    except OSError:
        def unavailable():
            raise CheckError("workspace-unavailable", "The isolated project workspace could not be used.")

        try:
            report.step(path.stages, "temporary workspace", unavailable)
        except CheckError:
            pass
    finally:
        path.finish()
    return path


def export_backup(project, destination):
    deadline = project.timeouts.restore
    stopped = project.compose("stop", "-t", "15", "immich", timeout=project.timeouts.startup)
    if stopped.code:
        raise CheckError("sample-stop-failed", "Could not stop Immich before taking the synthetic backup.")
    with (destination / "database.dump").open("wb") as stream:
        dumped = project.compose("exec", "-T", "database", "pg_dump", "--username=postgres", "--dbname=immich",
                                 "--format=custom", "--no-owner", "--no-acl", timeout=deadline, stdout=stream)
    if dumped.code:
        raise CheckError("sample-dump-failed", "pg_dump could not export the synthetic database.")
    media = destination / "media"
    media.mkdir()
    copied = project.compose("cp", "probe:/media/.", str(media.resolve()), timeout=deadline)
    if copied.code:
        raise CheckError("sample-media-failed", "Compose could not export the synthetic media.")
    return "sample-exported", "The custom-format database dump and media tree were exported from the stopped instance."


def generate_backup(report, destination: Path, timeouts: Timeouts) -> Backup:
    login = Login("rehearsal@example.test", secrets.token_urlsafe(24))
    with TemporaryDirectory(prefix="upgrade-rehearsal-seed-") as temporary:
        project = Project(Path(temporary), timeouts)
        api = Immich(project)
        stages = report.setup
        with cleanup_on_exit(report, stages, project):
            boot(report, stages, project, api, SOURCE)
            report.step(stages, "create synthetic account", lambda: api.authenticate(login, create=True), version=SOURCE)
            assets = []

            def upload():
                assets.append(Asset(*api.upload(png_image())))
                return "sample-uploaded", "Immich returned a new asset ID for the generated PNG."

            report.step(stages, "upload synthetic PNG", upload, version=SOURCE, asset="asset-1")
            asset = assets[0]
            report.step(stages, "read asset", lambda: api.asset(asset), version=SOURCE, asset="asset-1")
            report.step(stages, "original SHA-256", lambda: api.original(asset), version=SOURCE, asset="asset-1")
            report.step(stages, "export synthetic backup", lambda: export_backup(project, destination), version=SOURCE)
        if stages[-1].status != PASS:
            raise CheckError("seed-cleanup-failed", "Cleanup of the synthetic source project could not be verified.")
    return Backup(destination / "database.dump", destination / "media", login, (asset,))


def save_example(backup, destination):
    asset = backup.assets[0]
    config = {
        "source": SOURCE, "target": TARGET, "paths": [list(path) for path in (DIRECT, STAGED)],
        "backup": {"dump": "database.dump", "media": "media"},
        "auth": {"email_env": "IMMICH_REHEARSAL_EMAIL", "password_env": "IMMICH_REHEARSAL_PASSWORD"},
        "assets": [{"id": asset.id, "sha256": asset.sha256}],
    }
    (destination / "rehearsal.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    fd = os.open(destination / "sample.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as secret:
        secret.write(f"export IMMICH_REHEARSAL_EMAIL={backup.login.email}\n"
                     f"export IMMICH_REHEARSAL_PASSWORD={backup.login.password}\n")


def classify(report, demonstration):
    if any(stage.status != PASS for stage in report.setup) or not report.paths:
        return UNKNOWN
    if demonstration:
        if len(report.paths) != 2 or any(path.status == UNKNOWN for path in report.paths):
            return UNKNOWN
        direct, staged = report.paths
        failure = direct.first_failure
        if (direct.status == FAIL and failure is not None and failure.version == TARGET
                and failure.name == "migration" and report.evidence_for(failure).code == "invalid-upgrade-path"
                and staged.status == PASS and staged.final_originals):
            return PASS
        return FAIL
    statuses = {path.status for path in report.paths}
    if FAIL in statuses:
        return FAIL
    if UNKNOWN in statuses:
        return UNKNOWN
    return PASS


def explain_rules(report, path, candidates):
    stage = path.first_failure
    if stage is None or report.evidence_for(stage).code != "invalid-upgrade-path":
        return None
    item = {"mode": "built-in", "diagnosis": "Immich reported the documented TypeORM upgrade path error",
            "failure_stage": stage.name, "evidence_ids": [stage.evidence_id], "documentation_links": [DOCS["typeorm"]],
            "next_path": STAGED if STAGED in candidates else None}
    report.analysis.append(item)
    return item


def run_rehearsals(report, config, *, analyze: Callable | None = None, execute=run_path):
    pending = list(config.paths)
    while pending:
        versions = pending.pop(0)
        outcome = execute(report, config, versions)
        explanation = explain_rules(report, outcome, pending)
        suggested = explanation.get("next_path") if explanation else None
        if analyze is not None and outcome.first_failure is not None:
            item = analyze(report, outcome, tuple(pending))
            report.analysis.append(item)
            if item.get("next_path"):
                suggested = tuple(item["next_path"])
        if suggested in pending:
            pending.remove(suggested)
            pending.insert(0, suggested)
