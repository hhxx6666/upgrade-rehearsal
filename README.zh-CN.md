# UpgradeRehearsal v0.1

UpgradeRehearsal 把 Immich 备份还原到隔离的 Docker Compose 项目里，演练版本升级，并通过已认证的 API 按 SHA-256 校验原始照片。

[English](README.md) · MIT

```sh
python -m pip install -e '.[test]'
upgrade-rehearsal demo
```

需要 Python 3.11 或更新版本，以及 Docker Compose v2 和 Linux x86_64 的 Docker 守护进程。演示需要能访问镜像仓库，但不需要模型 API Key。

以下输出是在 Ubuntu 24.04 / x86_64、Docker Compose 2.40.3 上实际运行的结果。

```text
UpgradeRehearsal demo — passed
Path A v1.131.3 -> v1.137.0 — check failed
  migration — Migrations failed: Error: Invalid upgrade path
Path B v1.131.3 -> v1.132.3 -> v1.137.0 — passed
  Original SHA-256 matches — 1/1 checked
Wrote report.md and report.json to the report directory.
```

## 支持的发版与镜像

| 阶段 | Immich server 标签 | PostgreSQL 14 镜像 | 缓存镜像 |
| --- | --- | --- | --- |
| 起点 | `v1.131.3` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `redis:6.2-alpine` |
| 中间 | `v1.132.3` | `tensorchord/pgvecto-rs:pg14-v0.2.0` | `valkey/valkey:8-bookworm` |
| 目标 | `v1.137.0` | `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` | `valkey/valkey:8-bookworm` |

适配器按 digest 锁定每一个镜像，使用 `ghcr.io/immich-app/immich-server` 以及各发版资产里的 PostgreSQL 和缓存 digest。网络探测使用 `docker.io/library/python:3.12.11-alpine3.22`。切到目标版本时，它用该发版的镜像重启 PostgreSQL，挂载的是同一个仅供演练使用的 PostgreSQL 14 数据卷。媒体挂载点保持在 `/usr/src/app/upload`，目标版本仍然支持这个位置。[v1.131.3 Compose](https://github.com/immich-app/immich/releases/download/v1.131.3/docker-compose.yml) · [v1.132.3 Compose](https://github.com/immich-app/immich/releases/download/v1.132.3/docker-compose.yml) · [v1.137.0 Compose](https://github.com/immich-app/immich/releases/download/v1.137.0/docker-compose.yml) · [v1.137.0 发版说明](https://github.com/immich-app/immich/releases/tag/v1.137.0)

适配器调用各发版对应的 `/api/server/ping`、`/api/server/version`、`/api/auth/login`、`/api/assets/{id}` 和 `/api/assets/{id}/original` 接口。seed 命令另外使用 `/api/auth/admin-sign-up` 和 multipart 的 `/api/assets`。[v1.131.3 API](https://github.com/immich-app/immich/blob/v1.131.3/open-api/immich-openapi-specs.json) · [v1.132.3 API](https://github.com/immich-app/immich/blob/v1.132.3/open-api/immich-openapi-specs.json) · [v1.137.0 API](https://github.com/immich-app/immich/blob/v1.137.0/open-api/immich-openapi-specs.json)

每条路径都从同一份源备份的新还原开始，并先用源版本校验基线。路径 A 直接升到 `v1.137.0`；路径 B 先验证 `v1.132.3`，再尝试 `v1.137.0`。只有当路径 A 出现文档记载的迁移错误、路径 B 抵达目标版本且指定照片完好时，演示才算通过。执行器必须先看到确切的应用迁移信号，才会把失败归因于缺少中间版本。[Immich TypeORM 升级错误](https://docs.immich.app/errors/#typeorm-upgrade)

## 生成并复用样例备份

```sh
python examples/generate_sample.py --output sample-backup
. ./sample-backup/sample.env
upgrade-rehearsal run --config sample-backup/rehearsal.yaml --report-dir rehearsal-output/from-backup
```

生成脚本会启动源版本、创建合成账号、上传一张生成的 PNG，并把返回的 asset UUID 和 PNG 的 SHA-256 写进 `sample-backup/rehearsal.yaml`。样例目录里还包含一份自定义的 PostgreSQL dump、与它匹配的媒体目录树，以及存有合成凭据的本地 `sample.env`。示例配置同时选择两条路径，所以正常的 `run` 在路径 A 复现迁移失败时会返回 `1`。只想演练分段路径，就只写路径 B。

## 用自己的备份

请提供可信、静默的 Immich `v1.131.3` 逻辑备份，运行环境为 PostgreSQL 14 加 pgvecto.rs 0.2.0。使用 PostgreSQL 14 的 `pg_dump` custom 归档，只含一个数据库，不需要 owner 或 ACL，扩展名为 `.dump`。在源应用停止时导出与之匹配的 `UPLOAD_LOCATION` 目录树，保留其子目录结构，包括 `.immich` 标记文件。受支持的备份会把所需资产存放在这棵树里。[Immich 备份与还原](https://docs.immich.app/administration/backup-and-restore/)

```sh
docker compose stop immich-server
docker compose exec -T database pg_dump --username=postgres --dbname=immich \
  --format=custom --no-owner --no-acl > database.dump
```

当前版本接受 `PGDMP` custom 归档和普通的媒体目录树。纯 SQL、压缩 SQL、物理数据目录、媒体压缩包、外部库挂载以及媒体树内的符号链接都不在支持范围内。工具会在启动 Immich 之前用 `pg_restore --single-transaction --exit-on-error --no-owner --no-acl` 还原。[PostgreSQL 14 pg_dump](https://www.postgresql.org/docs/14/app-pgdump.html) · [PostgreSQL 14 pg_restore](https://www.postgresql.org/docs/14/app-pgrestore.html)

复制[示例 YAML](examples/rehearsal.yaml)，填入 dump 与媒体路径，替换示例里的 UUID 和 SHA-256，并为还原后备份中能访问所选资产的账号设置两个凭据环境变量。相对路径以配置文件所在目录为基准。可以只选一条路径，也可以两条都要。可选的 `timeouts` 映射以秒为单位控制镜像拉取、还原与媒体操作、迁移、启动与就绪、API 请求和清理。

```sh
upgrade-rehearsal run --config rehearsal.yaml
```

| 退出码 | `run` 结果 | 含义 |
| --- | --- | --- |
| `0` | `passed` | 所有配置的路径都通过基线检查与升级检查，且清理完成。 |
| `1` | `check failed` | 至少一条基线已验证的路径得出了明确的升级失败结论。各路径结论不同时，以各自的结论为准。 |
| `2` | `inconclusive` | 证据不足以判定，例如输入无效、Docker 不可用、还原出错、超时或清理未验证。 |
| `130` | Interrupted | 写报告前先执行清理。 |

## 报告与可选的 GPT 分析

CLI 默认把 `report.md` 和 `report.json` 写到 `rehearsal-output`。报告记录版本路径、各检查耗时、第一个失败的检查、证据 ID、最终的原始文件校验结果，以及实际验证到的范围。每条路径都有独立的临时目录、内部 Compose 网络、PostgreSQL 数据卷和媒体卷。源备份从宿主机读取并流式写入演练项目。整个过程不发布任何端口，HTTP 检查由探测容器发起。执行器依据项目的 Compose 身份和标签，在完成或受控中断之后核对容器、卷和网络的清理情况。Docker 守护进程可能会缓存已拉取的镜像。

证据来自白名单内的应用日志、API 返回结果和命令状态。报告使用 `asset-1` 这类资产代号，不写入 dump 内容、源路径、原始日志、照片字节、UUID、哈希或凭据。要启用 GPT 分析，加上 `--gpt-model MODEL` 并设置 `OPENAI_API_KEY`。模型拿到的也是同一份已脱敏证据、各阶段结果、官方文档索引和尚未尝试的配置路径。它的结构化回答需要引用证据，可以为一条允许的剩余路径排定优先级。每条路径都用原始备份和新的项目重跑，结论由执行器的检查决定。内置分析不需要 GPT 服务。

演练覆盖应用启动与迁移、API 就绪与版本、认证、资产元数据，以及指定资产原始文件的完整性。它不涉及生产流量、外部库、后台任务、机器学习、移动端客户端，也不做回滚。收到停止信号时可以清理；进程或守护进程被强行终止时，可能还需要手动清理临时项目。

## 测试

```sh
python -m pytest -q -m 'not docker'
python -m pytest -q -m docker
```

GitHub Actions 默认在 Python 3.11 和 3.12 上运行不依赖 Docker 的测试。工作流的手动触发还会在 Linux x86_64 上跑真实容器的集成测试，并上传已脱敏的报告。

## 许可

[MIT](LICENSE)
