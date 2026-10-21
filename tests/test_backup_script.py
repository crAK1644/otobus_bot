import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

OPS = Path(__file__).parent.parent / "ops"
PING = "https://hc-ping.example/backup-uuid"
SETTINGS = "RESTIC_REPOSITORY=s3:x\nRESTIC_PASSWORD=hunter2-secret\n"

# Stub executables record every call in $STUB_LOG; scenarios are chosen with STUB_* variables.
STUBS = {
    "docker": """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  *pg_dump*)
    [ "$STUB_DUMP" = empty ] || printf PGDMP-data
    [ "$STUB_DUMP" != fail ] ;;
  *"pg_restore --list"*) cat > /dev/null; [ "$STUB_LIST" != fail ] ;;
  *pg_restore*) cat > /dev/null ;;
  *logs*) [ -n "$STUB_NEVER_READY" ] || echo "PostgreSQL init process complete; ready" ;;
  *compose*psql*) echo "$STUB_LIVE" ;;
  *exec*psql*) echo "$STUB_RESTORED" ;;
esac
""",
    "restic": """#!/bin/sh
echo "restic $*" >> "$STUB_LOG"
case "$1" in
  snapshots) echo '[{"id":"abcd1234ffff","short_id":"abcd1234"}]' ;;
  restore)
    while [ $# -gt 0 ]; do
      [ "$1" = --target ] && printf PGDMP > "$2/iett.dump"
      shift
    done ;;
  backup) cat > /dev/null; [ "$STUB_BACKUP" != fail ] ;;
esac
""",
    "curl": '#!/bin/sh\necho "curl $*" >> "$STUB_LOG"\n',
    "date": '#!/bin/sh\n[ "$1" = "+%u" ] && echo "$STUB_WEEKDAY" || exec /bin/date "$@"\n',
    "sleep": "#!/bin/sh\n",
}


def run(tmp_path, script, settings=SETTINGS, mode=0o600, **stub_env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    env_file = tmp_path / ".env.backup"
    env_file.write_text(settings + f"BACKUP_PING_URL={PING}\n")
    env_file.chmod(mode)
    log = tmp_path / "calls.log"
    log.touch()
    result = subprocess.run(
        ["bash", str(OPS / script)],
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BACKUP_ENV_FILE": str(env_file),
            "STUB_LOG": str(log),
            "STUB_DUMP": "ok",
            "STUB_WEEKDAY": "1",
            "STUB_LIVE": "5",
            "STUB_RESTORED": "5",
            "TMPDIR": str(scratch),
            **stub_env,
        },
        capture_output=True,
        text=True,
    )
    return result, log.read_text(), sorted(p.name for p in scratch.iterdir())


def test_successful_backup_uploads_prunes_and_pings(tmp_path):
    result, calls, leftovers = run(tmp_path, "backup.sh")
    assert result.returncode == 0, result.stderr
    assert "restic backup --stdin --stdin-filename iett.dump" in calls
    assert "restic forget --tag iett-db --group-by tags --keep-daily 7 --keep-weekly 4" in calls
    assert f"curl -fsS -m 10 --retry 3 -o /dev/null {PING}\n" in calls
    assert "restic check" not in calls
    assert leftovers == []  # the plaintext dump is always removed


def test_sunday_backup_also_checks_the_repository(tmp_path):
    _, calls, _ = run(tmp_path, "backup.sh", STUB_WEEKDAY="7")
    assert "restic check" in calls


@pytest.mark.parametrize(
    "failure", [{"STUB_DUMP": "fail"}, {"STUB_DUMP": "empty"}, {"STUB_LIST": "fail"}]
)
def test_bad_dump_is_never_uploaded_and_reports_failure(tmp_path, failure):
    result, calls, leftovers = run(tmp_path, "backup.sh", **failure)
    assert result.returncode != 0
    assert "restic" not in calls
    assert f"{PING}/fail" in calls
    assert leftovers == []


def test_upload_failure_reports_failure_and_never_prunes_or_reports_success(tmp_path):
    result, calls, leftovers = run(tmp_path, "backup.sh", STUB_BACKUP="fail")
    assert result.returncode != 0
    assert "restic forget" not in calls
    assert f"{PING}/fail" in calls and f"{PING}\n" not in calls
    assert leftovers == []


def test_missing_setting_fails_loudly_before_any_work(tmp_path):
    result, calls, _ = run(tmp_path, "backup.sh", settings="RESTIC_PASSWORD=pw\n")
    assert result.returncode != 0 and "RESTIC_REPOSITORY is not set" in result.stderr
    assert f"{PING}/fail" in calls
    assert "restic" not in calls and "docker" not in calls


@pytest.mark.parametrize("script", ["backup.sh", "restore-drill.sh"])
def test_settings_file_readable_or_writable_by_others_is_rejected(tmp_path, script):
    result, calls, _ = run(tmp_path, script, mode=0o644)
    assert result.returncode != 0 and "must be mode 600" in result.stderr
    assert calls == ""  # nothing ran, and the secret was never sourced or echoed
    assert "hunter2-secret" not in result.stdout + result.stderr


def test_plaintext_from_a_killed_run_is_swept_after_12h_but_a_recent_one_is_kept(tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    stale, fresh = scratch / "iett-dump.stale", scratch / "iett-dump.fresh"
    stale.write_text("old")
    fresh.write_text("new")
    old = time.time() - 13 * 3600
    os.utime(stale, (old, old))
    recent = time.time() - 3 * 3600  # a slow but live run must survive
    os.utime(fresh, (recent, recent))
    result, _, leftovers = run(tmp_path, "backup.sh")
    assert result.returncode == 0, result.stderr
    assert leftovers == ["iett-dump.fresh"]


def test_restore_drill_restores_the_resolved_snapshot_and_cleans_up(tmp_path):
    result, calls, leftovers = run(tmp_path, "restore-drill.sh")
    assert result.returncode == 0, result.stderr
    assert "restic restore abcd1234 --target" in calls  # an exact ID, not "latest"
    assert "users: restored=5 live=5" in result.stdout
    name = next(part for part in calls.split() if part.startswith("iett-restore-drill-"))
    assert f"docker rm -f {name}" in calls  # unique per run, cleaned up by that name
    assert leftovers == []


def test_restore_drill_fails_when_backup_restores_empty_but_live_has_data(tmp_path):
    result, _, _ = run(tmp_path, "restore-drill.sh", STUB_RESTORED="0")
    assert result.returncode == 1


def test_restore_drill_reports_a_container_that_never_becomes_ready(tmp_path):
    result, calls, leftovers = run(
        tmp_path, "restore-drill.sh", STUB_NEVER_READY="1", DRILL_WAIT_SECONDS="2"
    )
    assert result.returncode == 1 and "did not become ready" in result.stderr
    assert "pg_restore" not in calls
    assert leftovers == []
