from datetime import datetime

import pytest

from iett_bot.schedule import Schedule


def local(value):
    return datetime.fromisoformat(value + "+03:00")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-19T06:59:59", False),
        ("2026-10-19T07:00:00", True),
        ("2026-10-19T09:59:59", True),
        ("2026-10-19T10:00:00", False),
        ("2026-10-19T15:00:00", True),
        ("2026-10-24T08:00:00", False),
    ],
)
def test_weekday_boundaries(value, expected):
    schedule = Schedule.parse("1,2,3,4,5 07:00-10:00,15:00-19:00")
    assert schedule.is_open(local(value)) is expected


def test_overnight_uses_start_day_and_week_boundary():
    monday = Schedule.parse("1 22:00-02:00")
    assert monday.is_open(local("2026-10-20T01:59:59"))
    assert not monday.is_open(local("2026-10-20T02:00:00"))
    assert not monday.is_open(local("2026-10-20T22:30:00"))
    sunday = Schedule.parse("7 22:00-02:00")
    assert sunday.is_open(local("2026-10-19T01:00:00"))


def test_next_window_and_json_roundtrip():
    schedule = Schedule.parse("1 22:00-02:00")
    assert schedule.next_open(local("2026-10-20T03:00:00")) == local("2026-10-26T22:00:00")
    assert Schedule.from_json(schedule.to_json()) == schedule
    assert Schedule.parse("1,2,3,4,5 all").is_open(local("2026-10-19T23:59:59"))
    assert Schedule.parse("all").is_open(local("2026-10-25T23:59:59"))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0 07:00-09:00",
        "8 07:00-09:00",
        "1 24:00-09:00",
        "1 7:00-09:00",
        "1 07:00-07:00",
        "1 07:00:00-09:00",
        "1 nonsense",
        "1 00:00-01:00," * 5,
    ],
)
def test_invalid_schedules(value):
    with pytest.raises(ValueError):
        Schedule.parse(value)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        Schedule().is_open(datetime(2026, 10, 21))
