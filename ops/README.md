# Production backups and external monitoring

The bot can run without an operator's laptop or browser. Restic encrypts database dumps on the VM
before sending them to a private object-storage bucket. The scheduled monitor runs on GitHub's
servers and sends an alert to the operator's private Telegram chat when readiness fails for ten
minutes, or when a backup/restore test fails or the newest successful backup is over thirty hours old.
Recovery sends one further message. A short incident that recovers inside the grace period is silent.

GitHub attempts checks every five minutes; jobs can start late or be dropped during high load.
Detection therefore takes at least two observations across ten minutes and is not a guaranteed
deadline. An unreachable VM is one bot/server incident; it does not also create a speculative backup
incident. Operators needing a tighter service guarantee can also use `HEALTHCHECK_PING_URL`.

## Storage and recovery

Use a dedicated Standard bucket with public access disabled. A dedicated service identity should
have only `read buckets` and `manage objects` for that bucket in the selected region. Restic needs
delete permission for pruning; this identity must not belong to an administrator group. Disable
console sign-in and unrelated credential types. Scope the policy with `target.bucket.name` and
`request.region`. Never place a tenancy administrator key on the VM.

The private server `.env.backup` contains the settings shown in the main README. Keep it root-owned
and mode 600. Keep a separate recovery copy of the repository URL, S3 credentials and restic password
outside the VM. Losing every copy of the password makes the encrypted backups unrecoverable.
Recovery files belong in ignored `backups/`, never in Git. Server replacement requires restoring the
database and separately recreating the bot's `.env` from its private recovery copy.

Run `sudo ops/backup.sh` and `sudo ops/restore-drill.sh` once before installing the supplied cron
file in `/etc/cron.d/iett-backup` and logrotate file in `/etc/logrotate.d/iett-backup`. The cron file
assumes `/home/ubuntu/apps/otobus_bot` and UTC. It schedules a daily backup at 03:17 Istanbul time
and a monthly restore drill at 04:41 on the first day. Adjust paths and clock for another server.
Do not remove the temporary PostgreSQL container's network isolation. It restores into memory and
removes the container and plaintext dump on exit. The drill confirms the archive restores and that
tables with live data are not empty; it does not prove every logical record matches the live database.

## Restricted status access

Generate a separate Ed25519 key solely for monitoring. Keep the existing administrator SSH key out
of GitHub. Install `monitor_probe.py` as `/usr/local/lib/iett-monitor/probe.py`, owned by root and
not writable by the SSH user. Install this wrapper as root-owned `/usr/local/bin/iett-monitor-status`:

```sh
#!/bin/sh
exec /usr/bin/sudo -n /usr/bin/python3 /usr/local/lib/iett-monitor/probe.py
```

The production Ubuntu user already has passwordless sudo. Other servers should give the monitoring
user sudo permission only for this exact probe. Add the monitor's **public** key to `authorized_keys`:

```text
restrict,command="/usr/local/bin/iett-monitor-status" ssh-ed25519 <public-key> iett-production-monitor
```

`restrict` disables port/agent forwarding, PTYs and user startup scripts. The forced command ignores
the requested SSH command and returns only a readiness boolean, observation time, and backup markers.
The probe fixes the container name to `iett-alerts-bot-1`; edit it for a differently named deployment.
Keep the SSH server's already-verified host keys. The client requires strict host-key checking and
does not automatically trust a new host.

## GitHub configuration

Enable `.github/workflows/monitor.yaml` with the repository variable `IETT_MONITOR_ENABLED=true`.
Store these values as encrypted **Actions secrets** in your own repository:

| Secret | Value |
| --- | --- |
| `IETT_MONITOR_HOST` | VM address |
| `IETT_MONITOR_HOST_KEY` | Matching trusted `known_hosts` entries |
| `IETT_MONITOR_SSH_KEY` | Dedicated restricted private key |
| `IETT_MONITOR_BOT_TOKEN` | Bot token used only for operator notifications |
| `IETT_MONITOR_CHAT_ID` | Operator's private Telegram chat ID |

The operator must have pressed Start in that notification bot. The bot service's main token and
database/storage credentials are not needed by GitHub. Someone able to change trusted workflows can
use their secrets, so review contributions before merging and restrict repository write access.
The monitor runs only on the default branch's schedule or manual dispatch, never on pull requests.

The workflow has repository-content write permission solely to retain incident state in the
`ops-monitor-state` branch. That file is public in a public repository: it contains only incident
timestamps and notification flags, with no addresses, credentials, chat IDs or subscriber data.
State changes are saved after successful Telegram delivery, so failed sends retry on the next run.
Telegram delivery is at least once: a lost response or failed state save can cause a repeated alert.
A weekly status commit records that monitoring is still operating and avoids the repository becoming
inactive during quiet periods. Workflow-generated commits do not trigger the CI workflow.

After installation, manually dispatch **Production monitor** and confirm both checks are true.
For a controlled drill, use `external_monitor.py --state-file <private-file>` from a separate machine
with the same five secret variables. Set `IETT_MONITOR_TEST_LABEL=MONITORING TEST` and temporarily
use `IETT_MONITOR_OUTAGE_SECONDS=60`; this affects only that local drill, not the production monitor.
Arrange an independent server-side restart before stopping the bot, observe an actual failure and
recovery, and verify an unchanged check produces no additional message. Keep the production grace
at 600 seconds. No drill should alter subscriber line selections or inject a fake service notice.
