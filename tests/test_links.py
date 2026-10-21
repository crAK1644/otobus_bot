import re

import pytest

from iett_bot.bot import code_from_payload, start_payload


@pytest.mark.parametrize("code", ["59RH", "KM-43", "1ŞÜ", "ÇAĞ-İ9"])
def test_payload_round_trip_uses_only_telegram_safe_characters(code):
    payload = start_payload(code)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", payload)
    assert code_from_payload(payload) == code


def test_ascii_codes_stay_readable_and_lowercase_links_work():
    assert start_payload("59RH") == "59RH"
    assert code_from_payload("59rh") == "59RH"


@pytest.mark.parametrize("payload", ["", "t_", "t_!!!", "t_gA", "not a line", "x" * 21, "59RH;--"])
def test_invalid_payloads_are_rejected(payload):
    with pytest.raises(ValueError):
        code_from_payload(payload)
