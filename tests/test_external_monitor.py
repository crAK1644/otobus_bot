import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

OPS = Path(__file__).parent.parent / "ops"
spec = importlib.util.spec_from_file_location("external_monitor", OPS / "external_monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def test_outage_survives_restarts_alerts_once_and_recovers_once():
    send = Mock()
    state = monitor.advance({}, {"bot": False}, 1000, send)
    assert not send.called
    # Serialize/reload as the GitHub state backend does between separate workflow runs.
    state = json.loads(json.dumps(state))
    assert monitor.advance(state, {"bot": False}, 1599, send) == state
    state = monitor.advance(state, {"bot": False}, 1600, send)
    assert send.call_count == 1 and "need attention" in send.call_args.args[0]
    assert monitor.advance(state, {"bot": False}, 1900, send) == state
    assert send.call_count == 1
    state = monitor.advance(state, {"bot": True}, 2000, send)
    assert send.call_count == 2 and "recovered" in send.call_args.args[0]
    assert monitor.advance(state, {"bot": True}, 2300, send) == state
    assert send.call_count == 2


def test_short_outage_and_healthy_checks_send_nothing():
    send = Mock()
    state = monitor.advance({}, {"bot": True, "backup": True}, 1000, send)
    state = monitor.advance(state, {"bot": False, "backup": None}, 1300, send)
    monitor.advance(state, {"bot": True, "backup": True}, 1600, send)
    assert not send.called


def test_failed_delivery_preserves_incident_for_retry():
    state = monitor.advance({}, {"bot": False}, 1000, Mock())
    with pytest.raises(OSError):
        monitor.advance(state, {"bot": False}, 1600, Mock(side_effect=OSError("offline")))
    assert state["checks"]["bot"] == {"since": 1000, "alerted": False}
    send = Mock()
    state = monitor.advance(state, {"bot": False}, 1900, send)
    assert send.call_count == 1
    with pytest.raises(OSError):
        monitor.advance(state, {"bot": True}, 2000, Mock(side_effect=OSError("offline")))
    assert state["checks"]["bot"]["alerted"] is True


def test_backup_failures_alert_immediately_and_remain_unknown_during_vm_outage():
    send = Mock()
    state = monitor.advance({}, {"bot": True, "backup": False}, 1000, send)
    assert send.call_count == 1 and "backups" in send.call_args.args[0]
    state = monitor.advance(state, {"bot": False, "backup": None}, 1100, send)
    assert state["checks"]["backup"]["alerted"] is True
    monitor.advance(state, {"bot": True, "backup": True}, 1200, send)
    assert send.call_count == 2


def test_readiness_and_backup_age_are_checked_without_exposing_personal_data():
    probe = {
        "observed_at": 200000,
        "bot_ready": True,
        "backup_last_success": 200000,
        "backup_failed": False,
        "restore_failed": False,
    }
    assert monitor.observations(probe, 200000) == {"bot": True, "backup": True}
    for change in (
        {"backup_last_success": None},
        {"backup_last_success": 1},
        {"backup_failed": True},
        {"restore_failed": True},
    ):
        assert monitor.observations({**probe, **change}, 200000)["backup"] is False
    assert monitor.observations(None, 200000) == {"bot": False, "backup": None}
    for change in ({"observed_at": 1}, {"bot_ready": "true"}, {"backup_failed": None}):
        with pytest.raises(ValueError):
            monitor.observations({**probe, **change}, 200000)


def test_weekly_status_updates_do_not_generate_healthy_notifications():
    send = Mock()
    state = monitor.advance({}, {"bot": True, "backup": True}, 1000000, send)
    assert monitor.advance(state, {"bot": True, "backup": True}, 1000300, send) == state
    updated = monitor.advance(state, {"bot": True, "backup": True}, 1000000 + 7 * 86400, send)
    assert updated["heartbeat"] != state["heartbeat"] and not send.called


def test_ssh_is_strictly_host_verified_and_private_key_is_removed(monkeypatch):
    monkeypatch.setenv("IETT_MONITOR_HOST", "192.0.2.1")
    monkeypatch.setenv("IETT_MONITOR_HOST_KEY", "192.0.2.1 ssh-ed25519 test")
    monkeypatch.setenv("IETT_MONITOR_SSH_KEY", "private-key-test")
    files = []

    def run(argv, **kwargs):
        key = Path(argv[argv.index("-i") + 1])
        files.append(key)
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.read_text() == "private-key-test\n"
        assert "StrictHostKeyChecking=yes" in argv
        assert "IdentitiesOnly=yes" in argv
        assert any(x.startswith("UserKnownHostsFile=") for x in argv)
        return subprocess.CompletedProcess(argv, 0, '{"bot_ready":true}', "")

    monkeypatch.setattr(monitor.subprocess, "run", run)
    assert monitor.read_probe() == {"bot_ready": True}
    assert not files[0].exists()


def test_github_state_reloads_and_updates_same_file(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    backend = monitor.GitHubState()
    state = {"version": 1, "checks": {}}
    monkeypatch.setattr(
        backend,
        "api",
        Mock(
            return_value={
                "sha": "abc",
                "content": monitor.base64.b64encode(json.dumps(state).encode()).decode(),
            }
        ),
    )
    assert backend.load() == state
    backend.save(state)
    call = backend.api.call_args
    assert call.kwargs["data"]["sha"] == "abc"
    assert call.kwargs["data"]["branch"] == "ops-monitor-state"


def test_telegram_failure_never_logs_credentials(monkeypatch, capsys):
    monkeypatch.setenv("IETT_MONITOR_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("IETT_MONITOR_CHAT_ID", "private-chat")
    monkeypatch.setattr(monitor, "request_json", Mock(return_value={"ok": False}))
    with pytest.raises(RuntimeError, match="rejected"):
        monitor.send_telegram("test")
    assert "secret-token" not in capsys.readouterr().out
