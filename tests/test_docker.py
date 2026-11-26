import pytest

from upgrade_rehearsal.docker import PROJECT_LABEL, Command, Project
from upgrade_rehearsal.evidence import CheckError, UNKNOWN
from upgrade_rehearsal.spec import SOURCE, TARGET, Timeouts


def test_cleanup_uses_compose_project_and_both_resource_labels(tmp_path, monkeypatch):
    project = Project(tmp_path, Timeouts())
    project.begun = True
    seen = []

    def invoke(args, **kwargs):
        seen.append(args)
        if args[1] == "compose":
            assert args[args.index("--project-name") + 1] == project.name
            assert "--volumes" in args and "down" in args
        elif args[2] == "ls":
            assert f"label=com.docker.compose.project={project.name}" in args
            assert f"label={PROJECT_LABEL}={project.name}" in args
            # Simulate a container that Compose left behind.
            if args[1] == "container" and not any(command[1:3] == ["container", "rm"] for command in seen):
                return Command(0, b"cab123\n", b"")
        else:
            assert args == ["docker", "container", "rm", "-f", "cab123"]
        return Command(0, b"", b"")

    monkeypatch.setattr("upgrade_rehearsal.docker.invoke", invoke)
    assert project.cleanup()[0] == "cleanup-verified"
    assert len(seen) == 6


def test_images_and_project_are_fixed_before_launch(tmp_path, monkeypatch):
    workspaces = [tmp_path / name for name in ("first", "second")]
    for workspace in workspaces:
        workspace.mkdir()
    first, second = (Project(workspace, Timeouts()) for workspace in workspaces)
    assert first.name != second.name
    assert first.env["UR_DB_PASSWORD"] != second.env["UR_DB_PASSWORD"]
    observed = []

    def invoke(args, *, env, **kwargs):
        observed.append((args, env))
        return Command(0, b"", b"")

    monkeypatch.setattr("upgrade_rehearsal.docker.invoke", invoke)
    first.pull(SOURCE)
    first.pull(TARGET)
    assert f":{SOURCE}@sha256:" in observed[0][1]["UR_SERVER"] and "pgvecto-rs" in observed[0][1]["UR_DATABASE"]
    assert f":{TARGET}@sha256:" in observed[1][1]["UR_SERVER"] and "vectorchord" in observed[1][1]["UR_DATABASE"]
    assert "legacy.yaml" in " ".join(observed[0][0])
    assert "legacy.yaml" not in " ".join(observed[1][0])


def test_registry_timeout_is_independent_of_migration_and_redacted(tmp_path, monkeypatch):
    project = Project(tmp_path, Timeouts())
    monkeypatch.setattr(project, "compose", lambda *args, **kwargs: Command(1, b"", b"i/o timeout /private/file"))
    with pytest.raises(CheckError) as error:
        project.pull(TARGET)
    assert error.value.code == "registry-timeout" and error.value.status == UNKNOWN
    assert "private" not in str(error.value)
