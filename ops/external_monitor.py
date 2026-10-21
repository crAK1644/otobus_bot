#!/usr/bin/env python3
"""Check the VM from GitHub Actions; retain only non-secret incident state."""

import argparse
import base64
import copy
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_BRANCH = "ops-monitor-state"
STATE_PATH = "monitor-state.json"
LABELS = {"bot": "Bot / VM", "backup": "Encrypted backups"}


def request_json(url, *, method="GET", data=None, headers=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


class GitHubState:
    def __init__(self):
        self.base = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}"
        self.headers = {
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.sha = None

    def api(self, path, **kwargs):
        return request_json(self.base + path, headers=self.headers, **kwargs)

    def load(self):
        try:
            content = self.api(f"/contents/{STATE_PATH}?ref={STATE_BRANCH}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            try:
                self.api(f"/git/ref/heads/{STATE_BRANCH}")
            except urllib.error.HTTPError as missing:
                if missing.code != 404:
                    raise
                self.api(
                    "/git/refs",
                    method="POST",
                    data={"ref": f"refs/heads/{STATE_BRANCH}", "sha": os.environ["GITHUB_SHA"]},
                )
            return {}
        self.sha = content["sha"]
        return json.loads(base64.b64decode(content["content"]))

    def save(self, state):
        body = {
            "message": "Record monitoring state [skip ci]",
            "content": base64.b64encode((json.dumps(state, indent=2) + "\n").encode()).decode(),
            "branch": STATE_BRANCH,
        }
        if self.sha:
            body["sha"] = self.sha
        self.api(f"/contents/{STATE_PATH}", method="PUT", data=body)


def observations(probe, now):
    if probe is None:
        # An unreachable VM is one incident; backup state stays unknown until it returns.
        return {"bot": False, "backup": None}
    stamp = probe.get("observed_at")
    if not isinstance(stamp, int) or abs(now - stamp) > 120:
        raise ValueError("Invalid or stale status response")
    if not all(
        isinstance(probe.get(key), bool) for key in ("bot_ready", "backup_failed", "restore_failed")
    ):
        raise ValueError("Malformed status response")
    success = probe.get("backup_last_success")
    valid_backup = isinstance(success, int) and 0 <= now - success <= 30 * 3600
    return {
        "bot": probe["bot_ready"],
        "backup": valid_backup and not probe["backup_failed"] and not probe["restore_failed"],
    }


def advance(previous, checks, now, send, *, outage_seconds=600, label=""):
    state = copy.deepcopy(previous)
    state.setdefault("version", 1)
    state.setdefault("checks", {})
    for name, healthy in checks.items():
        if healthy is None:
            continue
        incident = state["checks"].setdefault(name, {"since": None, "alerted": False})
        prefix = f"{label}\n" if label else ""
        if healthy:
            if incident["alerted"]:
                send(f"{prefix}✅ IETT Notification: {LABELS[name]} recovered.")
            incident.update(since=None, alerted=False)
        else:
            if incident["since"] is None:
                incident["since"] = now
            grace = outage_seconds if name == "bot" else 0
            if not incident["alerted"] and now - incident["since"] >= grace:
                text = (
                    "The bot/VM is unreachable or the bot is not ready. Please check the service."
                    if name == "bot"
                    else "A backup or restore test failed, or the latest backup is over 30h old."
                )
                send(f"{prefix}⚠️ IETT Notification: {LABELS[name]} need attention. {text}")
                incident["alerted"] = True
    # A weekly status commit keeps a quiet public repository's scheduled workflow active.
    if now - state.get("heartbeat", 0) >= 7 * 86400:
        state["heartbeat"] = now
    return state


def read_probe():
    with tempfile.TemporaryDirectory(prefix="iett-monitor-") as folder:
        private_key, known_hosts = Path(folder) / "key", Path(folder) / "known_hosts"
        private_key.write_text(os.environ["IETT_MONITOR_SSH_KEY"].rstrip() + "\n")
        private_key.chmod(0o600)
        known_hosts.write_text(os.environ["IETT_MONITOR_HOST_KEY"].rstrip() + "\n")
        known_hosts.chmod(0o600)
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-i",
                    str(private_key),
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    f"UserKnownHostsFile={known_hosts}",
                    "-o",
                    "ConnectTimeout=15",
                    "-o",
                    "ServerAliveInterval=10",
                    "-o",
                    "ServerAliveCountMax=2",
                    f"ubuntu@{os.environ['IETT_MONITOR_HOST']}",
                    "status",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("Status check timed out.")
            return None
        if result.returncode:
            print(f"Status check unavailable (SSH exit {result.returncode}).")
            return None
        return json.loads(result.stdout)


def send_telegram(text):
    # Never print the token-bearing request URL, response body, or private chat ID.
    result = request_json(
        f"https://api.telegram.org/bot{os.environ['IETT_MONITOR_BOT_TOKEN']}/sendMessage",
        method="POST",
        data={"chat_id": os.environ["IETT_MONITOR_CHAT_ID"], "text": text},
    )
    if result.get("ok") is not True:
        raise RuntimeError("Telegram rejected the monitoring notification")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, help="Use a private local state file for drills")
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    try:
        result = read_probe()
    except ValueError:
        result = None
    now = int(time.time())
    try:
        checks = observations(result, now)
    except (ValueError, AttributeError):
        checks = {"bot": False, "backup": None}
    if args.probe_only:
        print(json.dumps(checks))
        return
    backend = None if args.state_file else GitHubState()
    previous = (
        json.loads(args.state_file.read_text())
        if args.state_file and args.state_file.exists()
        else backend.load()
        if backend
        else {}
    )
    state = advance(
        previous,
        checks,
        now,
        send_telegram,
        outage_seconds=int(os.environ.get("IETT_MONITOR_OUTAGE_SECONDS", "600")),
        label=os.environ.get("IETT_MONITOR_TEST_LABEL", ""),
    )
    if state != previous:
        if args.state_file:
            args.state_file.write_text(json.dumps(state))
            args.state_file.chmod(0o600)
        else:
            backend.save(state)
    print(
        json.dumps(
            {
                "checks": checks,
                "notifications": {key: value["alerted"] for key, value in state["checks"].items()},
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Monitoring run failed ({type(exc).__name__}); no credentials logged.")
        raise SystemExit(1) from None
