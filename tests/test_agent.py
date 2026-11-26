import json

import pytest

from upgrade_rehearsal.agent import analyze_failure
from upgrade_rehearsal.evidence import FAIL, INVALID_PATH_V137, CheckError, PathResult, Report, application_signal
from upgrade_rehearsal.spec import DIRECT, DOCS, STAGED, TARGET


def test_application_log_requires_the_exact_documented_error():
    line = "immich-1  | \x1b[31m[Nest] 7  - 01/01/2025, 00:00:00 ERROR [Api:DatabaseRepository] "
    assert application_signal(line + "Migrations failed: Error: Invalid upgrade path\x1b[0m", TARGET) == "invalid-upgrade-path"
    assert application_signal(line + INVALID_PATH_V137, TARGET) == "invalid-upgrade-path"
    assert application_signal("docker: Migrations failed: Error: Invalid upgrade path", TARGET) is None
    assert application_signal(line + "Migrations failed: password=secret /private/photograph.png", TARGET) == "migration-failed"
    assert application_signal(line + "Migrations failed: Error: Invalid upgrade path elsewhere", TARGET) == "migration-failed"


@pytest.mark.parametrize("result, expected", [("valid", "GPT"), ("unknown-path", "GPT fallback"),
                                              ("secret", "GPT fallback"), ("unavailable", "GPT fallback")])
def test_model_analysis_is_evidence_bound_and_cannot_expand_paths(result, expected):
    report = Report("run")
    path = PathResult("Path A", DIRECT)

    def fail():
        raise CheckError("invalid-upgrade-path", "Migrations failed: Error: Invalid upgrade path", FAIL)

    with pytest.raises(CheckError):
        report.step(path.stages, "migration", fail, version=TARGET)
    path.finish()

    def respond(prompt, payload):
        assert "untried" in prompt and payload["candidates"] == (STAGED,)
        assert payload["documents"]["typeorm"] == DOCS["typeorm"]
        assert "E001" in json.dumps(payload)
        if result == "unavailable":
            raise TimeoutError("super-secret")
        return {"failure_stage": "migration", "evidence_ids": ["E001"],
                "documentation_links": [DOCS["typeorm"]],
                "next_path": ["v1.142.0"] if result == "unknown-path" else list(STAGED),
                "diagnosis": "super-secret" if result == "secret" else "typeorm-upgrade-path"}

    item = analyze_failure(report, path, (STAGED, ("v1.142.0",)), respond)
    assert item["mode"] == expected
    assert item["next_path"] == (list(STAGED) if result == "valid" else None)
    assert "super-secret" not in json.dumps(item)
