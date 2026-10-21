import json
from pathlib import Path

import pytest

from iett_bot.domain import parse_departures, wants_notice

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "departure_notices.json").read_text())


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: f"{case['line']}-{case['text'][:24]}")
def test_real_notices_parse_as_recorded(case):
    parsed = parse_departures(case["text"])
    if case["departures"] is None:
        assert parsed is None
        return
    for origin, hours in case["departures"].items():
        assert sorted({hour for name, hour in parsed if name == origin}) == hours
    assert {hour for _, hour in parsed} == {h for hs in case["departures"].values() for h in hs}


@pytest.mark.parametrize(
    "text",
    [
        "HACIOSMAN METRO KALKIŞLI 08.30 seferi yapılamayacaktır.",
        "HACIOSMAN METRO Kalkışlı 08:30 seferi yapılamayacaktır. (07:45)",
        "HACIOSMAN METRO kalkışlı 8:30 ve 9:10 seferi yapılamayacaktır.",
    ],
)
def test_spelling_variants(text):
    assert parse_departures(text)[0] == ["HACIOSMAN METRO", 8]


@pytest.mark.parametrize(
    "text",
    [
        "Yoğun trafik nedeniyle seferler rötarlıdır.",
        "KADIKÖY kalkışlı seferlerine 30 dakika rötar vardır. (10:00)",
        "Güzergah değişti. KADIKÖY durağından başlayacak 16:10 seferi 16:40 saatinde "
        "gerçekleşecektir.",
        "HACIOSMAN METRO kalkışlı 08:30 seferi iptal. Hat her iki yönde aksamaktadır.",
        "KADIKÖY kalkışlı 08:00 seferi yapılamayacaktır; "
        "BOSTANCI yönündeki seferler de durdurulmuştur.",
        "KADIKÖY durağından başlayacak 08:00 seferi 08:15 "
        "(BOSTANCI yönündeki 09:00 seferi ise 09:20) saatinde gerçekleşecektir.",
        "Yukarı DUDULLU kalkışlı 08:00 seferi yapılamayacaktır.",
        "KADIKÖY VE ÜSKÜDAR kalkışlı 08:00 seferi yapılamayacaktır.",
        "KADIKÖY kalkışlı 08:00 (09:00) seferi yapılamayacaktır.",
        "KADIKÖY KALKIŞLI 07.00 SEFERİ İPTAL EDİLMİŞTİR.ÜSKÜDAR kalkışlı 08:00 seferi "
        "yapılamayacaktır.",
        "Kalkışlı 08:00 seferi yapılamayacaktır.",
        "TAKSİM kalkışlı 08:30-10:30 saatleri arasındaki seferler iptal edilmiştir.",
        "TAKSİM kalkışlı 08:30 seferi ve 09:10 seferi iptal edilmiştir.",
        "",
    ],
)
def test_unsure_notices_are_not_parsed(text):
    assert parse_departures(text) is None


def test_wants_notice_only_skips_confident_non_matches():
    notice = parse_departures("HACIOSMAN METRO kalkışlı 08:30 seferi yapılamayacaktır.")
    assert wants_notice({}, notice)
    assert wants_notice({"HACIOSMAN METRO": [8]}, notice)
    assert not wants_notice({"HACIOSMAN METRO": [7]}, notice)
    assert wants_notice({"RUMELİ HİSARÜSTÜ": [7]}, notice)  # other direction left alone
    assert not wants_notice({"*": [7]}, notice)
    assert wants_notice({"HACIOSMAN METRO": [7]}, None)
    two = parse_departures("A KALKIŞLI 07:00 seferi, B KALKIŞLI 08:00 seferi yapılamayacaktır.")
    assert wants_notice({"A": [9], "B": [8]}, two)
    assert not wants_notice({"A": [9], "B": [9]}, two)


def test_similar_terminal_names_are_not_confused():
    notice = parse_departures("YENİ MERKEZ kalkışlı 08:30 seferi yapılamayacaktır.")
    assert notice == [["YENİ MERKEZ", 8]]
    assert wants_notice({"MERKEZ": [9]}, notice)
    assert not wants_notice({"YENİ MERKEZ": [9]}, notice)
    mixed = parse_departures("Trafik nedeniyle Merkez kalkışlı 08:30 seferi iptal.")
    assert wants_notice({"MERKEZ": [9]}, mixed)  # unclear origin is delivered
