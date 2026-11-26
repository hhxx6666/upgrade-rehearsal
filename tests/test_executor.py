import json
from pathlib import Path
import tarfile

import pytest

from upgrade_rehearsal.docker import Command
from upgrade_rehearsal.evidence import FAIL, PASS, UNKNOWN, CheckError, Report, SIGNAL_TEXT
from upgrade_rehearsal.executor import classify, export_backup, run_path, run_rehearsals
from upgrade_rehearsal.immich import Immich
from upgrade_rehearsal.spec import DIRECT, SOURCE, STAGED, TARGET, Asset, Backup, Config, Login, Timeouts


@pytest.fixture
def config(tmp_path):
    dump = tmp_path / "private-backup.dump"
    dump.write_bytes(b"PGDMP private-database-content")
    media = tmp_path / "private-media"
    media.mkdir()
    (media / "private-photo.png").write_bytes(b"private-photo-bytes")
    login = Login("private-email@example.test", "private-password")
    return Config(Backup(dump, media, login, (Asset("9c898770-b501-4a62-88b0-1f6175fff885", "1" * 64),)), (DIRECT, STAGED))


class FakeProject:
    def __init__(self, work, timeouts, *, failure=None):
        self.work = work
        self.timeouts = timeouts
        self.failure = failure
        self.version = SOURCE
        self.dump = None
        self.media = None
        self.cleaned = False

    def pull(self, version):
        self.version = version
        return "image", "Images ready."

    def dependencies(self):
        return "dependencies", "Dependencies ready."

    def start(self):
        if self.failure == "interrupt":
            raise KeyboardInterrupt
        return "start", "Container started."

    def compose(self, *args, **options):
        stream = options["stdin"]
        if "pg_restore" in args:
            assert "--single-transaction" in args
            self.dump = stream.read()
            if self.failure == "restore":
                return Command(1, b"", b"private database error /private-path")
        else:
            with tarfile.open(fileobj=stream) as archive:
                file = archive.extractfile("private-photo.png")
                assert file is not None
                self.media = file.read()
        return Command(0, b"", b"")

    def cleanup(self):
        self.cleaned = True
        return "cleanup-verified", "Project resources removed."


class FakeAPI(Immich):
    def migration(self):
        if self.project.version == TARGET and self.project.failure == "migration":
            raise CheckError("invalid-upgrade-path", SIGNAL_TEXT["invalid-upgrade-path"], FAIL)
        return "migrated", "Bootstrap completed."

    def ready(self):
        if self.project.version == TARGET and self.project.failure == "startup":
            raise CheckError("startup-timeout", "API readiness timed out.")
        return "ready", "API ready."

    def probe(self, operation, **arguments):
        if operation == "login":
            assert arguments["fields"]["password"] == "private-password"
            return {"status": 201, "value": "private-token"}
        if operation == "asset":
            return {"status": 200, "value": arguments["asset_id"]}
        if self.project.version == TARGET:
            if self.project.failure == "missing":
                return {"status": 404}
            if self.project.failure == "digest":
                return {"status": 200, "match": False}
        return {"status": 200, "match": True}


def test_paths_restore_the_same_unmodified_backup_in_separate_projects(config, tmp_path):
    report = Report("demo")
    projects = []

    def execute(report, config, versions):
        def factory(work, timeouts):
            project = FakeProject(work, timeouts, failure="migration" if versions == DIRECT else None)
            projects.append(project)
            return project

        return run_path(report, config, versions, project_factory=factory, api_factory=FakeAPI)

    before = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in (config.backup.dump, *config.backup.media.iterdir())}
    run_rehearsals(report, config, execute=execute)
    assert len(projects) == 2
    assert projects[0].work != projects[1].work
    assert all(project.cleaned and not project.work.exists() for project in projects)
    assert projects[0].media == projects[1].media == b"private-photo-bytes"
    assert projects[0].dump == projects[1].dump == b"PGDMP private-database-content"
    assert all((file.read_bytes(), file.stat().st_mtime_ns) == value for file, value in before.items())
    assert [path.status for path in report.paths] == [FAIL, PASS]
    assert classify(report, demonstration=True) == PASS
    assert classify(report, demonstration=False) == FAIL
    assert report.paths[1].final_originals[0].status == PASS
    assert report.analysis[0]["next_path"] == STAGED
    report.write(tmp_path / "reports")
    for file in (tmp_path / "reports").iterdir():
        text = file.read_text(encoding="utf-8")
        assert "private-" not in text and "9c898770" not in text and "1" * 64 not in text


@pytest.mark.parametrize("failure, code, verdict", [
    ("restore", "restore-failed", UNKNOWN),
    ("migration", "invalid-upgrade-path", FAIL),
    ("startup", "startup-timeout", UNKNOWN),
    ("missing", "original-missing", FAIL),
    ("digest", "sha256-mismatch", FAIL),
])
def test_failed_checks_have_distinct_evidence(config, failure, code, verdict):
    report = Report("run")
    outcome = run_path(report, config, DIRECT, api_factory=FakeAPI,
                       project_factory=lambda work, timeout: FakeProject(work, timeout, failure=failure))
    assert outcome.status == verdict
    assert report.evidence_for(outcome.first_failure).code == code
    assert outcome.stages[-1].name == "cleanup"
    assert outcome.stages[-1].status == PASS
    if failure == "restore":
        assert not any(stage.version == TARGET for stage in outcome.stages)
    if failure in {"missing", "digest"}:
        assert outcome.final_originals[0].status == FAIL


def test_interrupt_cleans_project_and_removes_temporary_workspace(config):
    report = Report("run")
    projects = []

    def factory(work, timeouts):
        project = FakeProject(work, timeouts, failure="interrupt")
        projects.append(project)
        return project

    with pytest.raises(KeyboardInterrupt):
        run_path(report, config, DIRECT, api_factory=FakeAPI, project_factory=factory)
    assert projects[0].cleaned and not projects[0].work.exists()
    assert report.paths[0].status == UNKNOWN
    assert report.evidence_for(report.paths[0].first_failure).code == "interrupted"


def test_startup_deadline_is_distinct_from_migration(monkeypatch):
    project = FakeProject(Path.cwd(), Timeouts())
    api = Immich(project)
    monkeypatch.setattr(api, "probe", lambda *args, **kwargs: {"status": 0})

    def deadline(seconds, check, code, message):
        assert not check(seconds)
        raise CheckError(code, message)

    monkeypatch.setattr("upgrade_rehearsal.immich.wait_for", deadline)
    with pytest.raises(CheckError) as error:
        api.ready()
    assert error.value.code == "startup-timeout"


def test_probe_passes_login_over_stdin_only():
    class Project:
        timeouts = Timeouts()

        def compose(self, *args, **options):
            assert not any("sensitive-password" in arg for arg in args)
            assert json.loads(options["data"])["fields"]["password"] == "sensitive-password"
            return Command(0, b'{"status":201,"value":"sensitive-token"}', b"")

    result = Immich(Project()).authenticate(Login("user@example.test", "sensitive-password"))
    assert "sensitive" not in str(result)


def test_seed_media_export_resolves_host_paths_before_changing_compose_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    destination = Path("sample-backup")
    destination.mkdir()

    class Project:
        timeouts = Timeouts()

        def compose(self, *args, **options):
            if args[0] == "cp":
                assert Path(args[-1]) == tmp_path / "sample-backup" / "media"
            if "pg_dump" in args:
                options["stdout"].write(b"PGDMP")
            return Command(0, b"", b"")

    assert export_backup(Project(), destination)[0] == "sample-exported"
    assert (destination / "database.dump").read_bytes() == b"PGDMP"
