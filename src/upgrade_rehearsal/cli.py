import argparse
from functools import partial
import os
from pathlib import Path
import signal
from tempfile import TemporaryDirectory

from .agent import analyze_failure, gpt_response
from .backup import validate_backup
from .docker import preflight
from .evidence import FAIL, NOT_RUN, PASS, UNKNOWN, CheckError, PathResult, Report
from .executor import classify, generate_backup, run_rehearsals, save_example
from .spec import DIRECT, STAGED, Config, ConfigError, Timeouts, load_config


def parser():
    cli = argparse.ArgumentParser(prog="upgrade-rehearsal", description="Rehearse Immich Compose upgrades in isolation.")
    commands = cli.add_subparsers(dest="mode", required=True)
    demo = commands.add_parser("demo", help="seed Immich and compare the two historical paths")
    run = commands.add_parser("run", help="rehearse from a supplied backup")
    seed = commands.add_parser("seed", help="generate a reusable synthetic backup")
    for subcommand in (demo, run, seed):
        subcommand.add_argument("--report-dir", type=Path, default=Path("rehearsal-output"))
    for subcommand in (demo, run):
        subcommand.add_argument("--gpt-model", metavar="MODEL", help="opt in to GPT analysis via OPENAI_API_KEY")
    run.add_argument("--config", type=Path, required=True)
    seed.add_argument("--output", type=Path, default=Path("sample-backup"))
    return cli


def config_check(action):
    try:
        return action()
    except (ConfigError, OSError, RuntimeError) as exc:
        raise CheckError("configuration-invalid", "Configuration, credentials or output locations are invalid.") from exc


def checked_config(config_path, report_dir):
    config = load_config(config_path)
    output = report_dir.resolve()
    media = config.backup.media.resolve()
    protected = {config_path.resolve(), config.backup.dump.resolve()}
    if output.is_relative_to(media) or any(output / name in protected for name in ("report.md", "report.json")):
        raise ConfigError("Reports must be written outside the source backup.")
    return config


def run_cli(args, report):
    config = None
    if args.mode == "run":
        def validate_config():
            nonlocal config
            config = config_check(lambda: checked_config(args.config, args.report_dir))
            return "configuration-valid", "The configuration lists only supported upgrade paths and valid asset selectors."

        report.step(report.setup, "configuration", validate_config)
        report.step(report.setup, "validate backup", lambda: validate_backup(config.backup))
    report.step(report.setup, "Docker preflight", preflight)
    if args.mode == "seed":
        def create_output():
            args.output.mkdir(mode=0o700, parents=True)
            return "sample-directory", "An exclusive output directory was created for the synthetic backup."

        report.step(report.setup, "sample output", create_output)
        backup = generate_backup(report, args.output, Timeouts())

        def write_config():
            save_example(backup, args.output)
            return "sample-config", "The synthetic asset ID and SHA-256 were recorded in the example configuration."

        report.step(report.setup, "sample manifest", write_config)
        report.status = PASS
        return
    model = None
    if args.gpt_model and (key := os.environ.get("OPENAI_API_KEY")):
        model = partial(analyze_failure, respond=partial(gpt_response, key, args.gpt_model))
    elif args.gpt_model:
        report.analysis.append({"mode": "GPT fallback", "diagnosis": "OPENAI_API_KEY was absent. Built-in analysis used"})
    if args.mode == "demo":
        with TemporaryDirectory(prefix="upgrade-rehearsal-backup-") as folder:
            backup = generate_backup(report, Path(folder), Timeouts())
            run_rehearsals(report, Config(backup, (DIRECT, STAGED)), analyze=model)
    else:
        run_rehearsals(report, config, analyze=model)
    report.status = classify(report, args.mode == "demo")


def interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    args = parser().parse_args(argv)
    report = Report(args.mode)
    code = None
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        run_cli(args, report)
    except CheckError as exc:
        if exc.code == "configuration-invalid":
            # An invalid config might designate the requested report directory as source media.
            print("Configuration invalid. Check the YAML, credentials and report location.")
            return 2
    except KeyboardInterrupt:
        code = 130
        report.status = UNKNOWN
    except OSError:
        print("Could not prepare the rehearsal workspace.")
    finally:
        signal.signal(signal.SIGTERM, previous)
    if args.mode == "demo":
        for name, versions in (("Path A", DIRECT), ("Path B", STAGED)):
            if not any(path.versions == versions for path in report.paths):
                report.paths.append(PathResult(name, versions, assets_requested=1))
    try:
        report.write(args.report_dir)
    except OSError:
        print("Could not write report.md and report.json.")
        return code or 2
    print(f"UpgradeRehearsal {report.mode} — {report.status}")
    for stage in report.setup:
        if stage.status != PASS:
            print(f"Setup — {stage.name} — {report.evidence_for(stage).detail}")
            break
    for path in report.paths:
        print(f"{path.name} {' -> '.join(path.versions)} — {path.status}")
        if failure := path.first_failure:
            print(f"  {failure.name} — {report.evidence_for(failure).detail}")
        coverage = path.asset_coverage()[path.versions[-1]]
        if coverage["requested"] is not None:
            print(f"  Target original SHA-256 — {coverage['matched']}/{coverage['requested']} matched "
                  f"({coverage['attempted']} attempted)")
    print("Wrote report.md and report.json to the report directory.")
    return code or {PASS: 0, FAIL: 1, UNKNOWN: 2, NOT_RUN: 2}[report.status]
