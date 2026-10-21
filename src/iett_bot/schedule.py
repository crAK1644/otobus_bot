import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")


def minute(value: str) -> int:
    parsed = time.fromisoformat(value)
    if len(value) != 5 or parsed.second or parsed.microsecond or parsed.tzinfo:
        raise ValueError("Use HH:MM")
    return parsed.hour * 60 + parsed.minute


@dataclass(frozen=True)
class Schedule:
    # Days anchor the START of a window, including overnight windows.
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    windows: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.days or any(day not in range(7) for day in self.days):
            raise ValueError("Choose days 1–7")
        if len(self.windows) > 4:
            raise ValueError("At most four windows")
        for start, end in self.windows:
            if start not in range(1440) or end not in range(1440) or start == end:
                raise ValueError("Invalid window")

    @classmethod
    def parse(cls, value: str) -> "Schedule":
        if value.strip().lower() == "all":
            return cls()
        day_text, window_text = value.split()
        days = tuple(sorted({int(day) - 1 for day in day_text.split(",")}))
        windows = ()
        if window_text.lower() != "all":
            pairs = [window.split("-") for window in window_text.split(",")]
            windows = tuple((minute(start), minute(end)) for start, end in pairs)
        return cls(days, windows)

    @classmethod
    def from_json(cls, value: str | dict) -> "Schedule":
        data = json.loads(value) if isinstance(value, str) else value
        return cls(tuple(data["days"]), tuple(tuple(pair) for pair in data["windows"]))

    def to_json(self) -> str:
        return json.dumps({"days": self.days, "windows": self.windows})

    def is_open(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("Use timezone-aware timestamps")
        local = now.astimezone(ISTANBUL)
        day = local.weekday()
        current = local.hour * 60 + local.minute
        if not self.windows:
            return day in self.days
        for start, end in self.windows:
            if start < end and day in self.days and start <= current < end:
                return True
            if start > end and (
                (day in self.days and current >= start)
                or ((day - 1) % 7 in self.days and current < end)
            ):
                return True
        return False

    def next_open(self, now: datetime) -> datetime:
        if self.is_open(now):
            return now
        local = now.astimezone(ISTANBUL)
        for offset in range(8):
            date = local.date() + timedelta(days=offset)
            if date.weekday() not in self.days:
                continue
            for start in sorted({pair[0] for pair in self.windows} or {0}):
                candidate = datetime.combine(date, time(start // 60, start % 60), ISTANBUL)
                if candidate > local:
                    return candidate
        raise ValueError("No next window")

    def describe(self) -> str:
        if self == Schedule():
            return "1,2,3,4,5,6,7 · 00:00–24:00"
        days = ",".join(str(day + 1) for day in self.days)
        windows = ", ".join(
            f"{start // 60:02}:{start % 60:02}–{end // 60:02}:{end % 60:02}"
            for start, end in self.windows
        )
        return f"{days} · {windows or '00:00–24:00'}"
