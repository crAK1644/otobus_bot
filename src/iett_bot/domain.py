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


def turkish_upper(text: str) -> str:
    """Upper-case with Turkish i/ı rules, NFC, and collapsed whitespace."""
    folded = unicodedata.normalize("NFC", text).replace("i", "İ").replace("ı", "I")
    return " ".join(folded.upper().split())


# Notices that name individual trips come in a few fixed sentence shapes. A sentence is only
# understood when it matches one of them completely, so any extra clause (another direction, a
# range such as 08:30-10:30, a route change) makes the whole notice "not understood".
_TIME = r"(?:[01]?\d|2[0-3])[:.][0-5]\d"
_TIMES = rf"{_TIME}(?:\s*(?:,|ve|/)\s*{_TIME})*"
_ORIGIN = r"[^\s,:;]+(?: [^\s,:;]+)*?"
_CANCELLED = rf"(?P<o>{_ORIGIN}) kalkışlı (?P<times>{_TIMES}) seferi"
_CANCELLED_MORE = rf"{_ORIGIN} kalkışlı {_TIMES} seferi"  # repeats cannot reuse group names
_STOPS = r"\d+\. \([^()]*\) - \d+\. \([^()]*\)"
_SENTENCES = [
    # "A kalkışlı 07:00, 08:00 seferi, B kalkışlı 09:00 seferi yapılamayacaktır."
    re.compile(rf"{_CANCELLED}(?:, {_CANCELLED_MORE})* (?:yapılamayacaktır|iptal edilmiştir)\.?"),
    # "A kalkışlı 14:56 seferi 16:15 hareketle 31. (X) - 61. (Y) duraklar arasında ... "
    re.compile(
        rf"{_CANCELLED} {_TIME} hareketle {_STOPS} duraklar arasında "
        r"çalışacak şekilde düzenlenmiştir\.?"
    ),
    # "... ile A durağından başlayacak 16:10 seferi 16:40, 16:20 seferi 16:50 saatinde ..."
    re.compile(
        r"(?:trafik yoğunluğu ve işletme sebepleri ile|trafik durumu sebebiyle) "
        rf"(?P<o>{_ORIGIN}) durağından başlayacak "
        rf"(?P<times>{_TIME} seferi {_TIME}(?: \(\d+ dakika (?:erken|geç)\))?"
        rf"(?:, {_TIME} seferi {_TIME}(?: \(\d+ dakika (?:erken|geç)\))?)*) "
        r"(?:saatinde gerçekleşecektir|olarak hareket edecektir)\.?"
    ),
]
_ITEM = re.compile(_CANCELLED)


def _fold(text: str) -> str:
    # Lower-case with Turkish rules so KALKIŞLI, Kalkışlı and kalkışlı all match.
    return unicodedata.normalize("NFC", text).replace("I", "ı").replace("İ", "i").lower()


_NOT_ORIGIN_WORDS = {
    "ve", "veya", "ile", "ya", "da", "-", "–", "kalkışlı", "seferi", "sefer", "iptal",
    "edilmiştir", "yapılamayacaktır", "durağından", "başlayacak", "hareketle",
}  # fmt: skip


def _origin_ok(origin: str) -> bool:
    """A single upper-case terminal name: no connectors ("A VE B"), no clause words, no
    sentence boundary glued to a word, no brackets or slashes."""
    return (
        origin == turkish_upper(origin)
        and not _NOT_ORIGIN_WORDS & set(_fold(origin).split())
        and not re.search(r"[()/&]|[^\W\d_]\.[^\W\d_]", origin)
    )


def parse_departures(text: str) -> list[list] | None:
    """Return [[origin, hour], ...] for the departures a notice names, else None.

    None means "not understood", and such notices are always delivered. Every sentence must
    match a known shape completely, and each origin must be written in upper case.
    """
    # Drop the capture stamp "(07:45)" only where İETT puts it: after a sentence or at the end.
    stamp = r"\(\d{1,2}:\d{2}\)"
    stamped = unicodedata.normalize("NFC", re.sub(rf"(?<=\.)\s*{stamp}|\s*{stamp}\s*$", " ", text))
    found = []
    for sentence in re.split(r"(?<=\.)\s+(?=[A-ZÇĞİÖŞÜ])", stamped.strip()):
        folded = " ".join(_fold(sentence).split())  # whitespace only; length-preserving below
        clean = " ".join(sentence.split())
        for shape in _SENTENCES:
            if shape.fullmatch(folded):
                break
        else:
            return None
        for match in (
            [shape.fullmatch(folded)] if shape is _SENTENCES[2] else _ITEM.finditer(folded)
        ):
            origin = clean[match.start("o") : match.end("o")]
            if not _origin_ok(origin):
                return None
            times = match.group("times")
            if "seferi" in times:  # delayed trips: keep the scheduled time of each pair
                times = " ".join(re.findall(rf"({_TIME}) seferi", times))
            found.extend(
                [origin, int(hour)] for hour, _ in re.findall(r"(\d{1,2})[:.](\d{2})", times)
            )
    return found or None


def wants_notice(picks: dict, departures: list | None) -> bool:
    """False only when every named departure is from a direction the rider narrowed and an
    hour they did not pick. Anything unclear (no picks, not understood, unknown origin) is True."""
    if not picks or not departures:
        return True
    for origin, hour in departures:
        keys = [key for key in picks if key in ("*", origin)]
        if not keys or any(hour in picks[key] for key in keys):
            return True
    return False
