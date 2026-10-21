import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

LINE_CODE = re.compile(r"[A-ZÇĞİÖŞÜ0-9]+(?:-[A-ZÇĞİÖŞÜ0-9]+)*\Z")


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_code(value: str) -> str:
    value = value.strip().upper()
    if len(value) > 20 or not LINE_CODE.fullmatch(value):
        raise ValueError("Invalid line code")
    return value


def normalize_notice(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_notice(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Line:
    code: str
    name: str

    @property
    def url(self) -> str:
        return "https://iett.istanbul/RouteDetail?" + urlencode({"hkod": self.code})


@dataclass(frozen=True)
class Snapshot:
    line_code: str
    checked_at: datetime
    notices: tuple[str, ...]
    raw: tuple[str, ...]
