# Linux cloud deployment

Qagent runs as two `runit` services on the current Ubuntu 20.04 cloud host. The
host's PID 1 is `/sbin/launcher`, not systemd, and `/usr/bin/runsvdir` supervises
`/etc/service`. The backend and frontend bind only to `127.0.0.1:8000` and
`127.0.0.1:5173`. Both processes run as `luozhenkun` with `TZ=Asia/Shanghai` and
are restarted by runit whenever they exit.

The persistent root is `/home/luozhenkun/qagent` (`QAGENT_HOME`). It must be on a
mounted persistent `/home` filesystem. The layout is `releases/<commit>` for
immutable application checkouts, `current` and `previous` symlinks, `state/qagent.db`
for the sole production ledger, `backups/` for SQLite and peer-control receipts,
`logs/` for service logs, `config/qagent.env` for runtime configuration, `research/`
for immutable research bundles, and `research-data/` for their evidence and outputs.
Runit-definition rollback archives are stored in `backups/deploy-rollback/` so they
survive the loss of ephemeral `/etc` contents.
GitHub is code-only: clone and fetch over public HTTPS;
do not put a GitHub write token, deploy credential, `.env`, database, or backup in
the repository.

## Prepare a release without starting Qagent

The current cloud image does not include Python 3.11 or `uv`; both are runtime
prerequisites that must be supplied by the image/build environment. Do not install
them as part of the rehydration step. Install Python 3.11+ (including `venv`), `uv`, a supported Node.js LTS release
with npm, `runit`, `cron`, `curl`, and `iproute2`. Ubuntu 20.04's default Python
3.8 is not supported by the backend. Create an immutable checkout named for the
exact release commit (use the full commit SHA selected for this deployment):

```bash
RELEASE_COMMIT='<full-release-commit-sha>'
QAGENT_HOME=/home/luozhenkun/qagent
sudo install -d -m 0755 -o luozhenkun -g luozhenkun "$QAGENT_HOME/releases"
sudo -u luozhenkun git clone https://github.com/ZackerRoo/Qagent.git \
  "$QAGENT_HOME/releases/$RELEASE_COMMIT"
sudo -u luozhenkun git -C "$QAGENT_HOME/releases/$RELEASE_COMMIT" \
  checkout --detach "$RELEASE_COMMIT"
sudo QAGENT_HOME="$QAGENT_HOME" ./scripts/switch_linux_release.sh "$QAGENT_HOME/releases/$RELEASE_COMMIT"
sudo QAGENT_HOME="$QAGENT_HOME" ./scripts/install_linux_runit.sh
```

The installer refuses to proceed unless `/home` is mounted. It places the database,
backups, logs, config, and releases under `QAGENT_HOME`; an existing production
database is never created or copied implicitly. Preserve/migrate an existing ledger
only through the explicit snapshot/cutover procedure below, after comparing manifests.
The service user can read `config/qagent.env` through its group; keep secrets out of
the repository.

### Image/container restart recovery

Only `/home/luozhenkun/qagent` is the persistent boundary. Paths under `/etc` are
ephemeral: the runit definitions, backup cron, peer-control cron, logrotate config,
and `/etc/qagent/qagent.env` link must be reconstructed after an image/container
replacement. The platform startup command must invoke this repository script after
the persistent `/home` mount is ready and before `runsvdir` or `cron` can consume
definitions:

```bash
sudo QAGENT_HOME=/home/luozhenkun/qagent \
  /home/luozhenkun/qagent/current/scripts/bootstrap_linux_persistent_home.sh
```

The script requires the existing `state/qagent.db`, verifies its read-only
`quick_check`, checks that the persistent config points to that DB and is readable
by `luozhenkun`, then restores definitions. With no
`state/.single-writer-approved` marker it leaves services down and all Qagent cron
files disabled. That marker is created only by the explicit enable procedure; when
it already exists, bootstrap restores the previously enabled service links and cron
files so runsvdir/cron can resume after restart. Bootstrap never creates the DB or
marker. Startup integration is an external image/platform prerequisite: a script
stored under `/home` cannot invoke itself after restart. The platform must arrange
and verify this invocation/order. The persisted application scheduler state is
unchanged; no manual API start is performed.

This bootstrap currently restores the backend/frontend runit definitions, backup
cron, and the pinned peer-control v5 daily/forward cron only. It does not recreate
the G1 observation or G2 collector crons. Those definitions remain disabled until
their bundle/data paths have been moved under `QAGENT_HOME`, required frozen models
are restored and checksum-verified, and their own explicit persistent opt-in and
rehydration rules are reviewed. The repository does not contain the frozen G2 model
files, so bootstrap must not infer that collector can safely resume.

The installed backup policy keeps five days by default and reserves 10 GiB after
the estimated next backup. Override either value only for a documented disk plan,
for example `QAGENT_BACKUP_KEEP_DAYS=7` or
`QAGENT_BACKUP_MIN_FREE_BYTES=21474836480` on the installer command; the rendered
cron entry records the effective values rather than depending on a cron environment.

The installer creates the virtualenv, installs dependencies, builds the frontend,
and renders definitions into `/etc/sv`. It does **not** link them into
`/etc/service` and leaves `/etc/cron.d/qagent-backup.disabled` disabled. Its
backend check uses a disposable temporary database, confirms the scheduler remains
disabled, and refuses an inherited `QAGENT_DATABASE_URL`; it cannot restore the
production scheduler. It also copies only the six standard HTTP proxy variables
(`http_proxy`, `https_proxy`, `HTTP_PROXY`, `HTTPS_PROXY`, `no_proxy`, and
`NO_PROXY`) from `/etc/environment` without sourcing or executing that file. Proxy
values are never printed. Existing secrets in `config/qagent.env` are preserved,
and an explicit proxy value already in that file takes precedence over the
corresponding host default. Both `no_proxy` and `NO_PROXY` retain their
existing entries and include `localhost`, `127.0.0.1`, and `::1`. The installer
sets the environment file to root-owned, service-group-readable mode `0640`; this
lets the service read it while denying other users access. A symlink under `/etc`
points to this persistent file.

Put provider credentials in `/home/luozhenkun/qagent/config/qagent.env` (root-owned,
group `luozhenkun`, mode `0640`). Never copy the local `.env` file as a whole. The
prior `/etc` copy is ephemeral; make the persistent copy before relying on restart
recovery. Preserve the exact existing Ranking V3/V4 signing identities by setting
`QAGENT_RANKING_V3_ATTESTATION_KEY_FILE` and
`QAGENT_RANKING_V4_EVIDENCE_ATTESTATION_KEY_FILE` to key files under
`/home/luozhenkun/qagent/config/`; place each original 32-byte key there with owner
`luozhenkun` and mode `0600`. Never print or regenerate these keys during recovery.
Keep the generated production database settings unchanged.

## Initialize a brand-new single paper ledger

Use this path only when there is no historical database to migrate. The persistent
`state/` directory must already exist, with no `qagent.db` or SQLite sidecars;
backend/frontend services and Qagent cron must be stopped. Run from the selected
release with its backend virtualenv, after `/home` is mounted:

```bash
cd /home/luozhenkun/qagent/current
sudo -u luozhenkun env PYTHONPATH="$PWD/backend" \
  "$PWD/backend/.venv/bin/python" scripts/initialize_fresh_paper_ledger.py \
  --home /home/luozhenkun/qagent --confirm-new-ledger \
  --label 'A股研究模拟盘' --initial-capital '<confirmed-new-capital>' \
  --allocation-per-trade-pct '<confirmed-allocation-pct>' \
  --max-positions '<confirmed-max-positions>' \
  --transaction-cost-bps '<confirmed-cost-bps>' \
  --slippage-bps '<confirmed-slippage-bps>' \
  --take-profit-pct '<confirmed-take-profit-pct>'
```

The script requires every account setting explicitly, validates them, and atomically
claims a previously nonexistent `state/qagent.db` to start the sole `default`
account. A 2026-09-15 active-account snapshot in
[`PROJECT_GOAL.md`](../PROJECT_GOAL.md) recorded 10% allocation, 10 maximum
positions, 5 bps cost, 5 bps slippage, and 50% take-profit; it did not establish
the latest initial capital or current account settings. Confirm the intended new
capital and all settings before filling the command. A fresh ledger starts with
zero trades and a new session ID: it cannot restore the exact prior session,
positions, events, cash, or scheduler state from this historical note. It does not create the single-writer approval marker, start
services, or enable the persisted scheduler. An existing DB is refused without
opening it for writes. If initialization fails after claiming a new file, preserve
that file for inspection; never rerun by overwriting it. Verify the new account and
database before following the explicit enable procedure below.

## Freeze, copy, and install the single database

On the Mac, stop the persisted scheduler first and wait for any scan/job to reach a
terminal state. Then stop the local backend and frontend so they cannot write again:

```bash
curl -X POST http://127.0.0.1:8000/api/automation/scheduler/stop
./scripts/qagent_dev.sh stop
./scripts/uninstall_macos_launch_agent.sh
./scripts/backup_sqlite.sh data/qagent.db /tmp/qagent-migration 30
./scripts/sqlite_cutover_manifest.py --preflight data/qagent.db \
  --output /tmp/qagent-migration/ledger-manifest.json
shasum -a 256 /tmp/qagent-migration/qagent-*.db
scp /tmp/qagent-migration/qagent-*.db luozhenkun@CLOUD:/tmp/qagent-migration.db
scp /tmp/qagent-migration/ledger-manifest.json \
  luozhenkun@CLOUD:/tmp/ledger-manifest.mac.json
```

Do not continue while a local backend, automation scheduler, or process holding the
Mac database open for writing remains. On the cloud host, services must still be
disabled:

```bash
sudo ./scripts/install_sqlite_snapshot.sh \
  /tmp/qagent-migration.db /home/luozhenkun/qagent/state/qagent.db
sha256sum /tmp/qagent-migration.db /home/luozhenkun/qagent/state/qagent.db
./scripts/sqlite_cutover_manifest.py --preflight /home/luozhenkun/qagent/state/qagent.db \
  --output /tmp/ledger-manifest.cloud.json
diff -u /tmp/ledger-manifest.mac.json /tmp/ledger-manifest.cloud.json
sudo ./scripts/enable_linux_runit.sh --confirm-local-writers-stopped
sudo ./scripts/verify_linux_deployment.sh
```

The confirmation flag is an operator assertion that the Mac writers are stopped.
The enable script refuses a missing or corrupt database. The scheduler's persisted
enabled/disabled state is preserved by the snapshot; installing software never
changes it. If the scheduler was stopped before the snapshot, start it later through
the API/UI only after cloud verification. Enabling waits for `runsvdir` to notice
both new `/etc/service` links before asking `sv` to start them. Verification uses
root access because runit's `supervise` state is intentionally not made readable to
ordinary users; it fails fast for non-root callers, so invoke it consistently with
`sudo`. If `sv up` fails, enabling
restores both `down` files, makes a bounded attempt to confirm both services down,
keeps backup cron disabled, and does not leave a single-writer approval marker.
It removes the service links after a readiness failure while the `down` files are
still present, or after a failed start only when both services are confirmed down;
an incomplete shutdown deliberately retains both links under runit supervision.

## Access from the Mac

The persistent launchd tunnel maps both loopback ports. It needs only the cloud
host SSH key, not a GitHub token:

```bash
export QAGENT_CLOUD_HOST='luozhenkun@CLOUD'
./scripts/install_macos_cloud_tunnel.sh
```

Open `http://127.0.0.1:5173`; the API remains at
`http://127.0.0.1:8000/api`. Remove the tunnel with
`./scripts/uninstall_macos_cloud_tunnel.sh`.

## Backup and rollback

Cron makes an online SQLite backup at 03:30 Asia/Shanghai, validates
`PRAGMA quick_check`, atomically publishes it under
`/home/luozhenkun/qagent/backups`, and
deletes backups older than five days by default. Before creating its temporary
database, the backup checks that the destination filesystem has room for the
larger of the source file size and SQLite logical page size, plus 10 GiB that
must remain free. A failed capacity preflight creates no temporary or final
backup and does not run retention cleanup, preserving the last known-good set.
The retention and reserve can be overridden with the installer variables above,
or with the script's optional `KEEP_DAYS` and `MIN_FREE_BYTES` arguments.

The current Debian/Ubuntu `cron` daemon
matches crontab fields in the host timezone; setting `TZ` or `CRON_TZ` in a
crontab changes only the command environment and does not change the match
timezone. Because this cloud host is fixed at UTC, the installed cron expression
is therefore `30 19 * * *` (19:30 UTC, which is 03:30 Asia/Shanghai on the next
calendar day). The installer refuses a missing, non-UTC, or non-zero-offset host
timezone instead of silently installing a shifted schedule. Verify the invariant
before installation:

```bash
cat /etc/timezone  # UTC or Etc/UTC
env -u TZ date '+%Z %z'  # UTC +0000
```

Logs are written to `/home/luozhenkun/qagent/logs` and rotated daily for 14 rotations without
requiring `svlogd`.

For a release rollback, disable writers, atomically swap the `current` and
`previous` symlinks, reinstall definitions/build artifacts, then explicitly enable:

```bash
sudo ./scripts/disable_linux_runit.sh
sudo ./scripts/rollback_linux_release.sh
sudo QAGENT_HOME=/home/luozhenkun/qagent ./scripts/install_linux_runit.sh
sudo ./scripts/enable_linux_runit.sh --confirm-local-writers-stopped
```

These operations do not modify the database. `rollback_linux_runit.sh` separately
restores an archived service-definition version from
`/home/luozhenkun/qagent/backups/deploy-rollback/` while leaving services disabled.
The disable script keeps each `/etc/service` link in place until runit reports the
service down, its observed process tree has exited, and ports 8000 and 5173 are no
longer listening. Immediately after recording both `down` intents, it disables the
backup cron and removes the single-writer marker before waiting for shutdown. A
timeout therefore leaves the links managed by runit with their `down` files
present, cron disabled, and no marker, and reports the remaining processes or
ports; resolve that failure before attempting rollback.

## Read-only unattended health check

Run the bounded diagnostic on the cloud host without changing scheduler state,
the production database, paper accounts/trades/events, strategy settings, or
alert configuration:

```bash
sudo /home/luozhenkun/qagent/current/scripts/check_linux_unattended_health.py
```

It prints one compact JSON document. Exit `0` means every required check passed,
exit `1` means at least one operational invariant failed, and exit `2` is invalid
command input. The checks cover backend health, frontend reachability, the
persisted scheduler checkpoint (enabled, idle or bounded in-flight, not overdue,
and no `last_error`), replay-readiness visibility/unknown evidence, read-only
`PRAGMA quick_check` for production and latest-backup databases, backup freshness,
backup-filesystem usage, capacity for the estimated next atomic backup, the
enabled backup cron, running `cron`/`crond` and `runsvdir` daemons, SysV cron
boot links for runlevels 2-5, and both runit service links/statuses. An optional
duplicate runit `crond` definition is not used as evidence: this host's effective
cron is the daemonizing SysV service. The default backup freshness limit is 36
hours, the backup-filesystem failure threshold is 85% used, the required
post-backup reserve is 10 GiB, and the default maximum in-flight scheduler age is
6 hours. Use the corresponding command flags only when an operator has a
documented host-specific reason. A 60-second scheduler overdue grace avoids
failing on the normal bounded clock-recheck race; older due checkpoints fail the
check.

The diagnostic intentionally does not call `GET /api/automation/scheduler`.
That application route invokes scheduler due-work refresh and can advance a due
cycle, so the diagnostic reads the persisted checkpoint through SQLite
`mode=ro` with `PRAGMA query_only=ON` instead. Its only HTTP calls are GETs to
the simple backend health endpoint, frontend root, and the explicitly read-only
execution replay-readiness endpoint.
