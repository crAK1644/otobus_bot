from datetime import UTC, datetime

import httpx
import pytest

from iett_bot.domain import fingerprint, normalize_code
from iett_bot.iett import IettClient, SourceError, parse_notices, retry_after_seconds


def test_split_deduplicate_and_preserve_original(fixture_json):
    recorded = fixture_json("live_19f.json")
    assert len(parse_notices(recorded)) == 2
    assert parse_notices([" A  B | C ", "A\n B"]) == ("A  B", "C")
    assert fingerprint("A  B") == fingerprint("A\n\tB")
    assert fingerprint("A B") != fingerprint("A B changed")
    assert parse_notices([]) == ()


@pytest.mark.parametrize("payload", [None, {}, "[]", [None], ["Valid", 42]])
def test_malformed_response_is_not_an_empty_snapshot(payload):
    with pytest.raises(SourceError):
        parse_notices(payload)


async def test_search_and_fetch_contract(fixture_json):
    seen = []

    def respond(request):
        seen.append(request)
        if "GetSearchItems" in request.url.path:
            payload = fixture_json("search_59rh.json")
            payload["list"].append({"Path": "/StationDetail?dkod=123", "Code": "123"})
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=fixture_json("simulated_59rh_cancellation.json"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        source = IettClient(client)
        line = await source.resolve("59rh")
        assert line.code == "59RH"
        assert line.url == "https://iett.istanbul/RouteDetail?hkod=59RH"
        assert await source.resolve("59RH") == line  # cached, exact code matching
        snapshot = await source.fetch(line.code)
    assert len(seen) == 2
    assert seen[1].url.params["rcode"] == "59RH"
    assert snapshot.checked_at.tzinfo is not None
    assert "TEST VERİSİ" in snapshot.notices[0]


@pytest.mark.parametrize(
    ("status", "body", "headers"),
    [
        (503, "unavailable", {}),
        (200, "<html>error</html>", {}),
        (429, "busy", {"Retry-After": "120"}),
        (200, '{"error":true}', {}),
    ],
)
async def test_source_errors(status, body, headers):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text=body, headers=headers)),
        base_url="https://iett.istanbul",
    ) as client:
        with pytest.raises(SourceError) as caught:
            await IettClient(client).fetch("59RH")
        if status == 429:
            assert caught.value.retry_after == 120


def test_line_codes():
    assert normalize_code(" 59rh ") == "59RH"
    assert normalize_code("19ş") == "19Ş"
    assert normalize_code("KM43-1") == "KM43-1"
    with pytest.raises(ValueError):
        normalize_code("../other?secret=foo")


def test_http_date_retry_after(monkeypatch):
    now = datetime(2026, 10, 21, 7, 0, tzinfo=UTC)
    monkeypatch.setattr("iett_bot.iett.utcnow", lambda: now)
    assert retry_after_seconds("Wed, 21 Oct 2026 07:02:00 GMT") == 120
    assert retry_after_seconds("not a date") == 60
