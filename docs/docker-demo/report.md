# UpgradeRehearsal

Mode **demo** · Result **passed**

- **Path A** · First failed check migration (E026) · Final originals not run
- **Path B** · First failed check none · Final originals asset-1 passed

## Setup

| Version | Check | Asset | Result | Seconds | Evidence |
| --- | --- | --- | --- | ---: | --- |
| — | Docker preflight | — | passed | 0.222 | E001 |
| v1.131.3 | image pull | — | passed | 22.105 | E002 |
| v1.131.3 | dependencies | — | passed | 7.173 | E003 |
| v1.131.3 | container start | — | passed | 0.252 | E004 |
| v1.131.3 | migration | — | passed | 3.435 | E005 |
| v1.131.3 | API readiness | — | passed | 0.485 | E006 |
| v1.131.3 | create synthetic account | — | passed | 0.359 | E007 |
| v1.131.3 | upload synthetic PNG | asset-1 | passed | 0.142 | E008 |
| v1.131.3 | read asset | asset-1 | passed | 0.130 | E009 |
| v1.131.3 | original SHA-256 | asset-1 | passed | 0.128 | E010 |
| v1.131.3 | export synthetic backup | — | passed | 0.383 | E011 |
| — | cleanup | — | passed | 10.333 | E012 |

## Path A — v1.131.3 → v1.137.0 — check failed

| Version | Check | Asset | Result | Seconds | Evidence |
| --- | --- | --- | --- | ---: | --- |
| v1.131.3 | image pull | — | passed | 0.138 | E013 |
| v1.131.3 | dependencies | — | passed | 1.623 | E014 |
| v1.131.3 | restore database | — | passed | 0.204 | E015 |
| v1.131.3 | copy media | — | passed | 0.066 | E016 |
| v1.131.3 | container start | — | passed | 0.233 | E017 |
| v1.131.3 | migration | — | passed | 3.482 | E018 |
| v1.131.3 | API readiness | — | passed | 0.493 | E019 |
| v1.131.3 | authentication | — | passed | 0.201 | E020 |
| v1.131.3 | read asset | asset-1 | passed | 0.141 | E021 |
| v1.131.3 | original SHA-256 | asset-1 | passed | 0.124 | E022 |
| v1.137.0 | image pull | — | passed | 18.880 | E023 |
| v1.137.0 | dependencies | — | passed | 7.918 | E024 |
| v1.137.0 | container start | — | passed | 0.246 | E025 |
| v1.137.0 | migration | — | check failed | 2.295 | E026 |
| — | cleanup | — | passed | 10.361 | E027 |

## Path B — v1.131.3 → v1.132.3 → v1.137.0 — passed

| Version | Check | Asset | Result | Seconds | Evidence |
| --- | --- | --- | --- | ---: | --- |
| v1.131.3 | image pull | — | passed | 0.138 | E028 |
| v1.131.3 | dependencies | — | passed | 1.632 | E029 |
| v1.131.3 | restore database | — | passed | 0.192 | E030 |
| v1.131.3 | copy media | — | passed | 0.065 | E031 |
| v1.131.3 | container start | — | passed | 0.227 | E032 |
| v1.131.3 | migration | — | passed | 3.448 | E033 |
| v1.131.3 | API readiness | — | passed | 0.463 | E034 |
| v1.131.3 | authentication | — | passed | 0.188 | E035 |
| v1.131.3 | read asset | asset-1 | passed | 0.132 | E036 |
| v1.131.3 | original SHA-256 | asset-1 | passed | 0.128 | E037 |
| v1.132.3 | image pull | — | passed | 18.165 | E038 |
| v1.132.3 | dependencies | — | passed | 8.071 | E039 |
| v1.132.3 | container start | — | passed | 0.253 | E040 |
| v1.132.3 | migration | — | passed | 3.430 | E041 |
| v1.132.3 | API readiness | — | passed | 0.246 | E042 |
| v1.132.3 | authentication | — | passed | 0.191 | E043 |
| v1.132.3 | read asset | asset-1 | passed | 0.130 | E044 |
| v1.132.3 | original SHA-256 | asset-1 | passed | 0.127 | E045 |
| v1.137.0 | image pull | — | passed | 0.144 | E046 |
| v1.137.0 | dependencies | — | passed | 0.738 | E047 |
| v1.137.0 | container start | — | passed | 0.252 | E048 |
| v1.137.0 | migration | — | passed | 3.435 | E049 |
| v1.137.0 | API readiness | — | passed | 0.253 | E050 |
| v1.137.0 | authentication | — | passed | 0.188 | E051 |
| v1.137.0 | read asset | asset-1 | passed | 0.134 | E052 |
| v1.137.0 | original SHA-256 | asset-1 | passed | 0.129 | E053 |
| — | cleanup | — | passed | 10.346 | E054 |

## Original-file coverage

The denominator is the number of configured assets for each version.

| Path | Version | Requested | SHA-256 attempted | SHA-256 matched | Matched share |
| --- | --- | ---: | ---: | ---: | ---: |
| Path A | v1.131.3 | 1 | 1 | 1 | 100.0% |
| Path A | v1.137.0 | 1 | 0 | 0 | 0.0% |
| Path B | v1.131.3 | 1 | 1 | 1 | 100.0% |
| Path B | v1.132.3 | 1 | 1 | 1 | 100.0% |
| Path B | v1.137.0 | 1 | 1 | 1 | 100.0% |

## Evidence

- **E001** `docker-ready` — A supported Docker Compose plugin and a Linux x86_64 daemon responded.
- **E002** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E003** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E004** `container-started` — Compose started the requested Immich container.
- **E005** `application-listening` — Immich logged that it is listening at the requested version after bootstrap.
- **E006** `api-ready` — The in-network ping returned pong and the version API matched the requested release.
- **E007** `authenticated` — Immich login returned an access token inside the project network.
- **E008** `sample-uploaded` — Immich returned a new asset ID for the generated PNG.
- **E009** `asset-read` — The authenticated API returned the requested asset.
- **E010** `sha256-match` — The streamed original matched the expected SHA-256.
- **E011** `sample-exported` — The custom-format database dump and media tree were exported from the stopped instance.
- **E012** `cleanup-verified` — No containers, networks or volumes with both project labels remain.
- **E013** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E014** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E015** `database-restored` — pg_restore committed the archive into the fresh database.
- **E016** `media-copied` — The source media was read into a separate project volume.
- **E017** `container-started` — Compose started the requested Immich container.
- **E018** `application-listening` — Immich logged that it is listening at the requested version after bootstrap.
- **E019** `api-ready` — The in-network ping returned pong and the version API matched the requested release.
- **E020** `authenticated` — Immich login returned an access token inside the project network.
- **E021** `asset-read` — The authenticated API returned the requested asset.
- **E022** `sha256-match` — The streamed original matched the expected SHA-256.
- **E023** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E024** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E025** `container-started` — Compose started the requested Immich container.
- **E026** `invalid-upgrade-path` — Migrations failed: Error: Invalid upgrade path
- **E027** `cleanup-verified` — No containers, networks or volumes with both project labels remain.
- **E028** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E029** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E030** `database-restored` — pg_restore committed the archive into the fresh database.
- **E031** `media-copied` — The source media was read into a separate project volume.
- **E032** `container-started` — Compose started the requested Immich container.
- **E033** `application-listening` — Immich logged that it is listening at the requested version after bootstrap.
- **E034** `api-ready` — The in-network ping returned pong and the version API matched the requested release.
- **E035** `authenticated` — Immich login returned an access token inside the project network.
- **E036** `asset-read` — The authenticated API returned the requested asset.
- **E037** `sha256-match` — The streamed original matched the expected SHA-256.
- **E038** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E039** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E040** `container-started` — Compose started the requested Immich container.
- **E041** `application-listening` — Immich logged that it is listening at the requested version after bootstrap.
- **E042** `api-ready` — The in-network ping returned pong and the version API matched the requested release.
- **E043** `authenticated` — Immich login returned an access token inside the project network.
- **E044** `asset-read` — The authenticated API returned the requested asset.
- **E045** `sha256-match` — The streamed original matched the expected SHA-256.
- **E046** `images-pulled` — Compose pulled or confirmed the fixed release images.
- **E047** `dependencies-ready` — PostgreSQL accepted TCP probes and the cache replied PONG.
- **E048** `container-started` — Compose started the requested Immich container.
- **E049** `application-listening` — Immich logged that it is listening at the requested version after bootstrap.
- **E050** `api-ready` — The in-network ping returned pong and the version API matched the requested release.
- **E051** `authenticated` — Immich login returned an access token inside the project network.
- **E052** `asset-read` — The authenticated API returned the requested asset.
- **E053** `sha256-match` — The streamed original matched the expected SHA-256.
- **E054** `cleanup-verified` — No containers, networks or volumes with both project labels remain.

## Analysis

- **built-in** — Immich reported the documented TypeORM upgrade path error. migration E026. v1.131.3 → v1.132.3 → v1.137.0 [Immich documentation](https://docs.immich.app/errors/#typeorm-upgrade)

## Scope

Passed rows identify the operations actually verified. Asset checks cover the requested assets and versions. Machine learning, external libraries, background jobs and production traffic are outside scope.
