import json
import subprocess
import sys

import pytest


@pytest.mark.docker
def test_real_demo(tmp_path):
    result = subprocess.run([sys.executable, "-m", "upgrade_rehearsal", "demo", "--report-dir", str(tmp_path)],
                            capture_output=True, text=True, timeout=2400)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    direct, staged = report["paths"]
    assert [direct["status"], staged["status"]] == ["check failed", "passed"]
    evidence = {item["id"]: item["code"] for item in report["evidence"]}
    assert evidence[direct["first_failure"]["evidence_id"]] == "invalid-upgrade-path"
    assert [stage["status"] for stage in staged["final_originals"]] == ["passed"]
    assert direct["asset_coverage"]["v1.137.0"] == {
        "requested": 1, "attempted": 0, "matched": 0, "matched_fraction": 0,
    }
    assert staged["asset_coverage"]["v1.137.0"] == {
        "requested": 1, "attempted": 1, "matched": 1, "matched_fraction": 1,
    }
    for path in (direct, staged):
        baseline = [stage for stage in path["stages"] if stage["version"] == "v1.131.3"]
        assert baseline[-1]["name"] == "original SHA-256" and baseline[-1]["status"] == "passed"
        assert evidence[path["stages"][-1]["evidence_id"]] == "cleanup-verified"
