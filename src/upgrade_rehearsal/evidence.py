from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import monotonic

from .spec import DOCS


PASS = "passed"
FAIL = "check failed"
UNKNOWN = "inconclusive"
NOT_RUN = "not run"

INVALID_PATH = "Migrations failed: Error: Invalid upgrade path"
INVALID_PATH_V137 = INVALID_PATH + ". For more information, see https://immich.app/errors#typeorm-upgrade"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
APP_LOG = re.compile(r"\[(?:Nest|Immich)\].*?\b(?:ERROR|LOG|DEBUG)\s+\[[^\]\r\n]+\]\s+(.*)")
MIGRATION_ERROR = re.compile(r"(?:Kysely migrations|Migrations) failed:")


def application_signal(logs: str, version: str) -> str | None:
    """Extract fixed signals from application logs without retaining untrusted text."""
    signals = set()
    for line in ANSI.sub("", logs).splitlines():
        match = APP_LOG.search(line)
        if not match:
            continue
        message = match[1]
        if message.strip() in {INVALID_PATH, INVALID_PATH_V137}:
            signals.add("invalid-upgrade-path")
        elif MIGRATION_ERROR.match(message):
            signals.add("migration-failed")
        elif message.startswith("Immich Server is listening on ") and f"[{version}]" in message:
            signals.add("application-listening")
    return next((signal for signal in ("invalid-upgrade-path", "migration-failed", "application-listening")
                 if signal in signals), None)


SIGNAL_TEXT = {
    "invalid-upgrade-path": INVALID_PATH,
    "migration-failed": "Immich logged a migration exception. Details omitted.",
    "application-listening": "Immich logged that it is listening at the requested version after bootstrap.",
}


class CheckError(Exception):
    def __init__(self, code: str, detail: str, status: str = UNKNOWN):
        super().__init__(detail)
        self.code = code
        self.status = status


@dataclass
class Evidence:
    id: str
    code: str
    detail: str


@dataclass
class Stage:
    name: str
    version: str | None
    status: str
    seconds: float
    evidence_id: str
    asset: str | None = None


@dataclass
class PathResult:
    name: str
    versions: tuple[str, ...]
    stages: list[Stage] = field(default_factory=list)
    status: str = NOT_RUN
    assets_requested: int | None = None

    @property
    def first_failure(self) -> Stage | None:
        return next((stage for stage in self.stages if stage.status != PASS), None)

    @property
    def final_originals(self) -> list[Stage]:
        return [stage for stage in self.stages if stage.version == self.versions[-1] and stage.name == "original SHA-256"]

    def asset_coverage(self) -> dict:
        coverage = {}
        for version in self.versions:
            checks = [s for s in self.stages if s.version == version and s.name == "original SHA-256"]
            matched = sum(s.status == PASS for s in checks)
            coverage[version] = {
                "requested": self.assets_requested,
                "attempted": len(checks),
                "matched": matched,
                "matched_fraction": matched / self.assets_requested if self.assets_requested else None,
            }
        return coverage

    def finish(self) -> None:
        first = self.first_failure
        if first is None:
            self.status = PASS
        elif first.version == self.versions[0] or any(s.name == "cleanup" and s.status != PASS for s in self.stages):
            self.status = UNKNOWN
        else:
            self.status = first.status


@dataclass
class Report:
    mode: str
    paths: list[PathResult] = field(default_factory=list)
    setup: list[Stage] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    analysis: list[dict] = field(default_factory=list)
    status: str = UNKNOWN

    def step(self, stages, name, action, *, version=None, asset=None):
        start = monotonic()
        status = PASS
        try:
            code, detail = action()
        except CheckError as exc:
            status, code, detail = exc.status, exc.code, str(exc)
            raise
        except KeyboardInterrupt:
            status, code, detail = UNKNOWN, "interrupted", "The operation was interrupted."
            raise
        except OSError as exc:
            status, code, detail = UNKNOWN, "local-io", "A local file or process operation failed."
            raise CheckError(code, detail) from exc
        finally:
            if "code" in locals():
                evidence_id = f"E{len(self.evidence) + 1:03d}"
                self.evidence.append(Evidence(evidence_id, code, detail))
                stages.append(Stage(name, version, status, round(monotonic() - start, 3), evidence_id, asset))

    def evidence_for(self, stage: Stage) -> Evidence:
        return next(item for item in self.evidence if item.id == stage.evidence_id)

    def as_json(self) -> dict:
        result = asdict(self)
        result["documents"] = DOCS
        for path, item in zip(self.paths, result["paths"], strict=True):
            item["first_failure"] = asdict(path.first_failure) if path.first_failure else None
            item["final_originals"] = [asdict(stage) for stage in path.final_originals]
            item["asset_coverage"] = path.asset_coverage()
            item["verified_scope"] = [asdict(stage) for stage in path.stages if stage.status == PASS]
        return result

    def markdown(self) -> str:
        lines = ["# UpgradeRehearsal", "", f"Mode **{self.mode}** · Result **{self.status}**", ""]
        groups = [("Setup", self.setup)]
        for path in self.paths:
            versions = " → ".join(path.versions)
            first = path.first_failure
            failure = f"{first.name} ({first.evidence_id})" if first else "none"
            originals = ", ".join(f"{s.asset} {s.status}" for s in path.final_originals) or NOT_RUN
            groups.append((f"{path.name} — {versions} — {path.status}", path.stages))
            lines.extend([f"- **{path.name}** · First failed check {failure} · Final originals {originals}"])
        for heading, stages in groups:
            lines.extend(["", f"## {heading}", "", "| Version | Check | Asset | Result | Seconds | Evidence |",
                          "| --- | --- | --- | --- | ---: | --- |"])
            for stage in stages:
                lines.append(f"| {stage.version or '—'} | {stage.name} | {stage.asset or '—'} | {stage.status} | "
                             f"{stage.seconds:.3f} | {stage.evidence_id} |")
        if self.paths:
            lines.extend(["", "## Original-file coverage", "",
                          "The denominator is the number of configured assets for each version.", "",
                          "| Path | Version | Requested | SHA-256 attempted | SHA-256 matched | Matched share |",
                          "| --- | --- | ---: | ---: | ---: | ---: |"])
            for path in self.paths:
                for version, counts in path.asset_coverage().items():
                    requested = counts["requested"] if counts["requested"] is not None else "—"
                    fraction = counts["matched_fraction"]
                    share = f"{fraction:.1%}" if fraction is not None else "—"
                    lines.append(f"| {path.name} | {version} | {requested} | {counts['attempted']} | "
                                 f"{counts['matched']} | {share} |")
        lines.extend(["", "## Evidence", ""])
        lines.extend(f"- **{item.id}** `{item.code}` — {item.detail}" for item in self.evidence)
        if self.analysis:
            lines.extend(["", "## Analysis", ""])
            for item in self.analysis:
                links = " ".join(f"[Immich documentation]({url})" for url in item.get("documentation_links", []))
                ids = ", ".join(item.get("evidence_ids", []))
                next_path = " → ".join(item.get("next_path") or [])
                lines.append(f"- **{item['mode']}** — {item['diagnosis']}. "
                             f"{item.get('failure_stage', '')} {ids}. {next_path} {links}".rstrip())
        lines.extend(["", "## Scope", "", "Passed rows identify the operations actually verified. "
                      "Asset checks cover the requested assets and versions. "
                      "Machine learning, external libraries, background jobs and production traffic are outside scope."])
        return "\n".join(lines) + "\n"

    def write(self, output: Path) -> None:
        output.mkdir(parents=True, exist_ok=True)
        for name, text in (("report.json", json.dumps(self.as_json(), indent=2) + "\n"),
                           ("report.md", self.markdown())):
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as file:
                temporary = Path(file.name)
                file.write(text)
            try:
                temporary.replace(output / name)
            finally:
                temporary.unlink(missing_ok=True)
