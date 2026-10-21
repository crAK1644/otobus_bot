#!/usr/bin/env python3
"""Root-owned, forced SSH command exposing readiness and backup timestamps only."""

import json
import subprocess
import time
from pathlib import Path

STATUS = Path("/var/lib/iett-monitor")
READY = """
import json, urllib.request, urllib.error
try:
    response = urllib.request.urlopen('http://localhost:8080/readyz', timeout=5)
except urllib.error.HTTPError as exc:
    response = exc
data = json.load(response)
print(json.dumps({'ready': data.get('ready') is True}))
"""


def probe():
    ready = False
    try:
        result = subprocess.run(
            ["/usr/bin/docker", "exec", "iett-alerts-bot-1", "python", "-c", READY],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            ready = json.loads(result.stdout).get("ready") is True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    success = STATUS / "backup-success"
    return {
        "observed_at": int(time.time()),
        "bot_ready": ready,
        "backup_last_success": int(success.stat().st_mtime) if success.exists() else None,
        "backup_failed": (STATUS / "backup-failed").exists(),
        "restore_failed": (STATUS / "restore-failed").exists(),
    }


if __name__ == "__main__":
    print(json.dumps(probe()))
