# Operational Scripts

This directory contains operational scripts for local and production
agent-monitoring workflows.

Supported environments:

- `local`
- `prod`

There is no staging environment in this repository.

## Database Backups

Create a database backup:

```bash
ENVIRONMENT=local infra/scripts/db_backup/backup_db.sh
ENVIRONMENT=prod TAG=v1.2.3 infra/scripts/db_backup/backup_db.sh
```

Backups are PostgreSQL custom-format dumps created with:

- `pg_dump --format=custom`
- `--no-owner`
- `--no-privileges`

Default storage location:

- `/var/backups/agent-monitoring/<environment>` when that parent directory is
  writable or the script runs as root
- `.agent/backups/db/<environment>` as a local developer fallback

Backup filenames use UTC timestamps:

```text
agent_monitoring_<environment>_<YYYYMMDDTHHMMSSZ>.dump
```

## Database Restore

Restore replaces the target database `public` schema from one backup file.

```bash
ENVIRONMENT=local infra/scripts/db_backup/restore_db.sh .agent/backups/db/local/agent_monitoring_local_20260520T210000Z.dump
ENVIRONMENT=prod TAG=v1.2.3 infra/scripts/db_backup/restore_db.sh /var/backups/agent-monitoring/prod/agent_monitoring_prod_20260520T210000Z.dump
```

The restore script asks for `yes` before replacing the target schema.

## Build

Build the tagged prod app image:

```bash
TAG=v1.2.3 infra/scripts/release/build.sh
```

If `TAG` is omitted, the script uses the exact Git tag checked out in the
working tree.

Build behavior:

- builds only prod-style tagged images: `prod-agent-monitoring:<TAG>`
- refuses to build with uncommitted changes unless `EMERGENCY=true`
- supports `NO_CACHE=true` when a full rebuild is required
- records the last built tag under the script state directory
- prunes older local images, keeping only the built tag

## Release

Build and deploy the tagged prod app image in one command:

```bash
TAG=v1.2.3 infra/scripts/release/release.sh
```

For non-interactive automation, pass approval through to deploy:

```bash
AUTO_APPROVE=true TAG=v1.2.3 infra/scripts/release/release.sh
```

Release behavior:

- runs `infra/scripts/release/build.sh`
- runs `infra/scripts/release/deploy.sh`
- preserves the deploy script's backup, migration, confirmation, and
  `current_tag` behavior

## Deploy

Deploy an already-built prod image:

```bash
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

For non-interactive automation, pass approval explicitly:

```bash
AUTO_APPROVE=true TAG=v1.2.3 infra/scripts/release/deploy.sh
```

Deploy behavior:

- verifies the local image `prod-agent-monitoring:<TAG>` exists
- creates the production Postgres host data directory before starting `db`;
  default: `/var/lib/agent-monitoring/postgresql`, override with
  `POSTGRES_DATA_DIR`
- mounts the project context prompt directory into the app container;
  default: `private/`, override with `PROJECT_CONTEXT_PROMPT_DIR`.
  The app validates the mandatory prompt file at runtime.
- mounts the app log directory into the app container; default:
  `/var/log/agent-monitoring`, override with `LOGS_DIR`.
  The app writes dated JSON logs there.
- asks for confirmation before mutating the target stack unless
  `AUTO_APPROVE=true`
- creates a database backup unless `SKIP_BACKUP=true`
- starts the database service
- applies committed migrations with the container `migrate` command unless
  `SKIP_MIGRATE=true`
- runs the one-shot monitoring command, defaulting to `log_analysis`
- records the deployed tag under the script state directory after the command
  succeeds

Production Postgres data is a host bind mount, not a Compose-managed Docker
volume. Normal Docker volume prune commands will not delete it. Deploy and prod
backup commands expect this marker file to exist:

```text
$POSTGRES_DATA_DIR/data/pgdata/PG_VERSION
```

For a brand-new production VPS only, initialize the empty Postgres data
directory deliberately:

```bash
TAG=v1.2.3 doppler run -- infra/scripts/release/build.sh

ALLOW_EMPTY_POSTGRES_DATA_DIR=true \
SKIP_BACKUP=true \
TAG=v1.2.3 \
doppler run -- infra/scripts/release/deploy.sh
```

Use that first-init override once. After `$POSTGRES_DATA_DIR/data/pgdata/PG_VERSION`
exists, use the normal deploy command so the script takes a backup before
running migrations.

Dry run:

```bash
TAG=v1.2.3 DRY_RUN=true infra/scripts/release/deploy.sh
```

Run another one-shot command during deploy:

```bash
MONITORING_COMMAND=sitemap-analysis TAG=v1.2.3 infra/scripts/release/deploy.sh
```
