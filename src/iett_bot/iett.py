import asyncio
import html
import re
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime

import httpx

from iett_bot.domain import Line, Snapshot, fingerprint, normalize_code, turkish_upper, utcnow


class SourceError(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


def retry_after_seconds(value: str) -> float:
    try:
        return max(1.0, float(value))
    except ValueError:
        try:
            return max(1.0, (parsedate_to_datetime(value) - utcnow()).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 60.0


def parse_notices(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise SourceError("Unexpected announcement response shape")
    unique: dict[str, str] = {}
    for item in payload:
        for part in item.split("|"):
            text = part.strip()
            if text:
                unique.setdefault(fingerprint(text), text)
    return tuple(unique.values())


def parse_timetable(page: str) -> dict[str, tuple[str, ...]]:
    """Planned departures per terminal from the line page's timetable HTML.

    Weekday, Saturday and Sunday columns are merged. Anything that is not a plain
    "<TERMINAL> KALKIŞ" table (for example frequency-only metrobus lines) yields {}.
    """
    result: dict[str, tuple[str, ...]] = {}
    for table in page.split('<table class="line-table"')[1:]:
        header = re.search(r"routedetailstartend[^>]*>(.*?)</th>", table, re.S)
        if not header:
            continue
        name = turkish_upper(html.unescape(re.sub(r"<[^>]+>", "", header.group(1))))
        name = name.removesuffix("KALKIŞ").strip()
        times = sorted(set(re.findall(r"<td[^>]*>\s*(\d{2}:\d{2})\s*</td>", table)))
        if name and times:
            result[name] = tuple(times)
    return result


class IettClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._search_cache: OrderedDict[str, tuple[float, list[Line]]] = OrderedDict()
        self._search_lock = asyncio.Lock()
        self._timetable_cache: OrderedDict[str, tuple[float, dict[str, tuple[str, ...]]]] = (
            OrderedDict()
        )
        self._timetable_lock = asyncio.Lock()

    async def _get(self, path: str, params: dict) -> object:
        try:
            response = await self.client.get(path, params=params)
            if response.status_code == 429 or (
                response.status_code == 503 and "Retry-After" in response.headers
            ):
                retry = retry_after_seconds(response.headers.get("Retry-After", "60"))
                raise SourceError("Source rate limited", retry)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never include request URLs/headers or response bodies in logs/errors.
            raise SourceError("Source request failed or returned invalid JSON") from exc

    async def search(self, query: str) -> list[Line]:
        query = query.strip()
        if not 1 <= len(query) <= 80:
            raise ValueError("Search must be 1–80 characters")
        async with self._search_lock:
            cached = self._search_cache.get(query.casefold())
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            payload = await self._get(
                "/tr/RouteStation/GetSearchItems", {"key": query, "langid": 1}
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
                raise SourceError("Unexpected line search response shape")
            lines: dict[str, Line] = {}
            for item in payload["list"]:
                if not isinstance(item, dict):
                    raise SourceError("Unexpected line search item")
                if not str(item.get("Path", "")).startswith("/RouteDetail?"):
                    continue  # Stop results are returned by the same search endpoint.
                try:
                    code = normalize_code(item["Code"])
                    name = item["Name"]
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("Missing name")
                except (KeyError, ValueError, TypeError, AttributeError) as exc:
                    raise SourceError("Invalid line search item") from exc
                lines[code] = Line(code, name.strip())
            result = list(lines.values())[:20]
            self._search_cache[query.casefold()] = (time.monotonic(), result)
            if len(self._search_cache) > 256:
                self._search_cache.popitem(last=False)
            return result

    async def timetable(self, line: Line) -> dict[str, tuple[str, ...]]:
        """Planned departures per terminal, or {} when unavailable.

        A missing timetable only costs the rider the hour labels, so failures never raise.
        Cached for a day; an empty or failed answer is retried after ten minutes.
        """
        async with self._timetable_lock:
            cached = self._timetable_cache.get(line.code)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            start, _, end = line.name.partition(" - ")
            try:
                response = await self.client.post(
                    "/tr/RouteStation/GetScheduledDepartureTimes",
                    data={
                        "rstart": start,
                        "rend": end,
                        "timeschule": "",
                        "freq": "",
                        "lngid": 1,
                        "hCode": line.code,
                    },
                )
                response.raise_for_status()
                result = parse_timetable(response.text)
            except httpx.HTTPError:
                result = {}
            self._timetable_cache[line.code] = (
                time.monotonic() + (86400 if result else 600),
                result,
            )
            if len(self._timetable_cache) > 256:
                self._timetable_cache.popitem(last=False)
            return result

    async def resolve(self, code: str) -> Line | None:
        code = normalize_code(code)
        return next((line for line in await self.search(code) if line.code == code), None)

    async def fetch(self, code: str) -> Snapshot:
        code = normalize_code(code)
        payload = await self._get("/tr/RouteStation/GetLineStatus", {"rcode": code})
        notices = parse_notices(payload)
        return Snapshot(code, utcnow(), notices, tuple(payload))


def http_client(timeout: float = 10) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://iett.istanbul",
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        headers={"User-Agent": "iett-telegram-alerts/0.1", "Accept": "application/json"},
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=4),
    )
