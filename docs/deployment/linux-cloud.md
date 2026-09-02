# Linux cloud deployment

Qagent runs as two `runit` services on the current Ubuntu 20.04 cloud host. The
host's PID 1 is `/sbin/launcher`, not systemd, and `/usr/bin/runsvdir` supervises
`/etc/service`. The backend and frontend bind only to `127.0.0.1:8000` and
`127.0.0.1:5173`. Both processes run as `luozhenkun` with `TZ=Asia/Shanghai` and
are restarted by runit whenever they exit.

The production database is `/var/lib/qagent/qagent.db`. It is deliberately
outside release checkouts. GitHub is code-only: clone and fetch over public HTTPS;
do not put a GitHub write token, deploy credential, `.env`, database, or backup in
the repository.

## Prepare a release without starting Qagent

Install Python 3.11+ (including `venv`), `uv`, a supported Node.js LTS release
with npm, `runit`, `cron`, `curl`, and `iproute2`. Ubuntu 20.04's default Python
3.8 is not supported by the backend. Create an immutable checkout named for the
exact release commit (use the full commit SHA selected for this deployment):

```bash
RELEASE_COMMIT='<full-release-commit-sha>'
sudo install -d -m 0755 -o luozhenkun -g luozhenkun /opt/qagent/releases
sudo -u luozhenkun git clone https://github.com/ZackerRoo/Qagent.git \
  "/opt/qagent/releases/$RELEASE_COMMIT"
sudo -u luozhenkun git -C "/opt/qagent/releases/$RELEASE_COMMIT" \
  checkout --detach "$RELEASE_COMMIT"
sudo ./scripts/switch_linux_release.sh "/opt/qagent/releases/$RELEASE_COMMIT"
sudo QAGENT_APP_DIR=/opt/qagent/current ./scripts/install_linux_runit.sh
```

The installer creates the virtualenv, installs dependencies, builds the frontend,
and renders definitions into `/etc/sv`. It does **not** link them into
`/etc/service` and leaves `/etc/cron.d/qagent-backup.disabled` disabled. Its
backend check uses a disposable temporary database, confirms the scheduler remains
disabled, and refuses an inherited `QAGENT_DATABASE_URL`; it cannot restore the
production scheduler. It also copies only the six standard HTTP proxy variables
(`http_proxy`, `https_proxy`, `HTTP_PROXY`, `HTTPS_PROXY`, `no_proxy`, and
`NO_PROXY`) from `/etc/environment` without sourcing or executing that file. Proxy
values are never printed. Existing secrets in `/etc/qagent/qagent.env` are
preserved, and an explicit proxy value already in that file takes precedence over
the corresponding host default. Both `no_proxy` and `NO_PROXY` retain their
existing entries and include `localhost`, `127.0.0.1`, and `::1`. The installer
corrects overly broad permissions without widening a stricter existing mode.

Put provider secrets only in `/etc/qagent/qagent.env` (root-owned, mode `0640`,
group `luozhenkun`). Never copy the local `.env` file as a whole. Preserve the
exact existing Ranking V3/V4 signing identities with
`QAGENT_RANKING_V3_ATTESTATION_KEY_FILE` and
`QAGENT_RANKING_V4_EVIDENCE_ATTESTATION_KEY_FILE`; copy their original 32-byte
key files separately, owner `luozhenkun`, mode `0600`. Keep the generated
production database settings unchanged.

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
  /tmp/qagent-migration.db /var/lib/qagent/qagent.db
sha256sum /tmp/qagent-migration.db /var/lib/qagent/qagent.db
./scripts/sqlite_cutover_manifest.py --preflight /var/lib/qagent/qagent.db \
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
`PRAGMA quick_check`, atomically publishes it under `/var/backups/qagent`, and
deletes backups older than 14 days. The current Debian/Ubuntu `cron` daemon
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

Logs are written to `/var/log/qagent` and rotated daily for 14 rotations without
requiring `svlogd`.

For a release rollback, disable writers, atomically swap the `current` and
`previous` symlinks, reinstall definitions/build artifacts, then explicitly enable:

```bash
sudo ./scripts/disable_linux_runit.sh
sudo ./scripts/rollback_linux_release.sh
sudo QAGENT_APP_DIR=/opt/qagent/current ./scripts/install_linux_runit.sh
sudo ./scripts/enable_linux_runit.sh --confirm-local-writers-stopped
```

These operations do not modify the database. `rollback_linux_runit.sh` separately
restores an archived service-definition version while leaving services disabled.
The disable script keeps each `/etc/service` link in place until runit reports the
service down, its observed process tree has exited, and ports 8000 and 5173 are no
longer listening. Immediately after recording both `down` intents, it disables the
backup cron and removes the single-writer marker before waiting for shutdown. A
timeout therefore leaves the links managed by runit with their `down` files
present, cron disabled, and no marker, and reports the remaining processes or
ports; resolve that failure before attempting rollback.
