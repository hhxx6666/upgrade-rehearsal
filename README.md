# UpgradeRehearsal v0.1

UpgradeRehearsal restores an Immich backup in isolated Docker Compose projects, rehearses version upgrades, and verifies original photos by SHA-256 through the authenticated API.

[简体中文](README.zh-CN.md) · MIT

```sh
python -m pip install -e '.[test]'
upgrade-rehearsal demo
```

Use Python 3.11 or newer and Docker Compose v2 with a Linux x86_64 Docker daemon. The demo requires access to the image registries and runs without a model API key.

Actual demo output from Ubuntu 24.04 on x86_64 with Docker Compose 2.40.3.

```text
UpgradeRehearsal demo — passed
Path A v1.131.3 -> v1.137.0 — check failed
  migration — Migrations failed: Error: Invalid upgrade path
Path B v1.131.3 -> v1.132.3 -> v1.137.0 — passed
  Original SHA-256 matches — 1/1 checked
Wrote report.md and report.json to the report directory.
```

## Supported releases and images

| Stage | Immich server tag | PostgreSQL 14 image | Cache image |
| --- | --- | --- | --- |
| Source | `v1.131.3` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `redis:6.2-alpine` |
| Intermediate | `v1.132.3` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `valkey/valkey:8-bookworm` |
| Target | `v1.137.0` | `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` | `valkey/valkey:8-bookworm` |

The adapter pins every image by digest. It uses `ghcr.io/immich-app/immich-server` and the PostgreSQL and cache digests from each release asset. The network probe uses `docker.io/library/python:3.12.11-alpine3.22`. At the target transition it restarts PostgreSQL with the release image against the same rehearsal-only PostgreSQL 14 volume. It keeps the source media mount at `/usr/src/app/upload`, a location the target release still supports. [v1.131.3 Compose](https://github.com/immich-app/immich/releases/download/v1.131.3/docker-compose.yml) · [v1.132.3 Compose](https://github.com/immich-app/immich/releases/download/v1.132.3/docker-compose.yml) · [v1.137.0 Compose](https://github.com/immich-app/immich/releases/download/v1.137.0/docker-compose.yml) · [v1.137.0 release notes](https://github.com/immich-app/immich/releases/tag/v1.137.0)

The adapter calls the release-specific `/api/server/ping`, `/api/server/version`, `/api/auth/login`, `/api/assets/{id}` and `/api/assets/{id}/original` endpoints. The seed command also uses `/api/auth/admin-sign-up` and multipart `/api/assets`. [v1.131.3 API](https://github.com/immich-app/immich/blob/v1.131.3/open-api/immich-openapi-specs.json) · [v1.132.3 API](https://github.com/immich-app/immich/blob/v1.132.3/open-api/immich-openapi-specs.json) · [v1.137.0 API](https://github.com/immich-app/immich/blob/v1.137.0/open-api/immich-openapi-specs.json)

Each path starts with a fresh restore of the same source backup and checks the baseline against the source release. Path A upgrades directly to `v1.137.0`. Path B verifies `v1.132.3` before trying `v1.137.0`. The demo passes when Path A produces the documented migration error and Path B reaches the target with the requested photo intact. The executor requires the exact application migration signal before attributing a failure to the missing intermediate release. [Immich TypeORM upgrade error](https://docs.immich.app/errors/#typeorm-upgrade)

## Generate and reuse a sample backup

```sh
python examples/generate_sample.py --output sample-backup
. ./sample-backup/sample.env
upgrade-rehearsal run --config sample-backup/rehearsal.yaml --report-dir rehearsal-output/from-backup
```

The generator starts the source release, creates a synthetic account, uploads a generated PNG, and records the returned asset UUID and the PNG SHA-256 in `sample-backup/rehearsal.yaml`. The sample directory also contains a custom PostgreSQL dump, its matching media tree, and a local `sample.env` with the synthetic credentials. The example configuration selects both paths, so a normal `run` returns `1` when Path A reproduces the migration failure. List only Path B to rehearse the staged route by itself.

## Use your own backup

Provide a trusted, quiescent logical backup from Immich `v1.131.3` running PostgreSQL 14 with pgvecto.rs 0.2.0. Use a PostgreSQL 14 `pg_dump` custom archive with one database, no owner or ACL requirements, and a `.dump` extension. Export the matching `UPLOAD_LOCATION` tree while the source application is stopped. Keep its subdirectory layout, including the `.immich` marker files. Supported backups store the requested assets inside that tree. [Immich backup and restore](https://docs.immich.app/administration/backup-and-restore/)

```sh
docker compose stop immich-server
docker compose exec -T database pg_dump --username=postgres --dbname=immich \
  --format=custom --no-owner --no-acl > database.dump
```

This version accepts the `PGDMP` custom archive and a regular media directory tree. Plain SQL, compressed SQL, physical data directories, media archives, external library mounts and links within the media tree are outside its supported input format. The tool restores with `pg_restore --single-transaction --exit-on-error --no-owner --no-acl` before starting Immich. [PostgreSQL 14 pg_dump](https://www.postgresql.org/docs/14/app-pgdump.html) · [PostgreSQL 14 pg_restore](https://www.postgresql.org/docs/14/app-pgrestore.html)

Copy [the sample YAML](examples/rehearsal.yaml), set the dump and media paths, replace the sample UUID and SHA-256, and set the two credential environment variables for an account in the restored backup with access to the selected assets. Relative paths are resolved from the configuration file. Select either or both supported paths. The optional `timeouts` mapping controls image pulls, restore and media operations, migration, startup and readiness, API requests, and cleanup in seconds.

```sh
upgrade-rehearsal run --config rehearsal.yaml
```

| Exit code | `run` result | Meaning |
| --- | --- | --- |
| `0` | `passed` | All configured paths passed the baseline and upgrade checks, including cleanup. |
| `1` | `check failed` | At least one path with a verified baseline produced a conclusive upgrade failure. See the individual path verdicts when results differ. |
| `2` | `inconclusive` | The run lacked enough evidence for a verdict, such as an invalid input, unavailable Docker, restore error, timeout or unverified cleanup. |
| `130` | Interrupted | Cleanup runs before the report is written. |

## Reports and optional GPT analysis

The CLI writes `report.md` and `report.json` to `rehearsal-output` by default. Reports record the version path, check timings, first failed check, evidence IDs, final original-file checks and verified scope. Each path has an independent temporary directory, internal Compose network, PostgreSQL volume and media volume. The source backup is read from the host and streamed into the rehearsal project. There are no published ports. HTTP checks run from the probe container. The executor uses the project's Compose identity and labels to verify container, volume and network cleanup after completion or a handled interruption. The Docker daemon may cache pulled images.

Evidence is derived from allowlisted application log messages, API outcomes and command statuses. Reports use asset labels such as `asset-1` and omit dump contents, source paths, raw logs, photo bytes, UUIDs, hashes and credentials. To enable GPT analysis, add `--gpt-model MODEL` and set `OPENAI_API_KEY`. The model receives the same sanitized evidence, stage results, the official documentation index and the untried configured paths. Its structured response cites evidence and can prioritize a permitted remaining path. Each path reuses the original backup with a new project. Executor checks determine verdicts. Built-in analysis runs without the GPT service.

The rehearsal covers application bootstrap and migration, API readiness and version, authentication, asset metadata, and original-file integrity for the configured assets. It does not exercise production traffic, external libraries, background jobs, machine learning, mobile clients or a rollback. Stop signals permit cleanup while abrupt process or daemon termination may require manual cleanup of the temporary project.

## Test

```sh
python -m pytest -q -m 'not docker'
python -m pytest -q -m docker
```

GitHub Actions runs the Docker-free tests on Python 3.11 and 3.12 by default. The workflow's manual dispatch also runs the real-container integration test on Linux x86_64 and uploads the sanitized reports.
